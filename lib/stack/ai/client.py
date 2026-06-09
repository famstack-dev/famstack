"""OpenAI-compatible AI capabilities for famstack stacklets.

Container-only: this module imports the `openai` SDK, so it is **not**
imported from `stack/__init__` or `stack.ai.__init__` — the host `./stack`
CLI stays stdlib. Any stacklet's container code uses it directly:

    from stack.ai.client import LLM, Transcriber
    llm = LLM.from_env(namespace="archivist-bot")
    text = await llm.complete("classifier", prompt, json_mode=True)

    transcriber = Transcriber.from_env(namespace="scribe-bot")
    transcript = await transcriber.transcribe(audio_bytes, filename="voice.ogg")

Both classes wrap `AsyncOpenAI`, which speaks to any provider serving the
OpenAI Audio + Chat APIs via `base_url`. They are deliberately separate
classes because chat and speech-to-text live behind different services
(``OPENAI_URL`` vs ``WHISPER_URL``) — folding them into one client would
force a single base URL onto two unrelated endpoints. They do share the
typed error hierarchy so callers can handle "AI is down" uniformly.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

import openai
from loguru import logger
from openai import AsyncOpenAI

from stack.ai.models import resolve_model


# ── Errors ───────────────────────────────────────────────────────────────
#
# A small typed hierarchy so callers can distinguish "the LLM is down" from
# "the model isn't loaded" from "it timed out" — each maps to a different
# user-facing message and a different retry decision.

class LLMError(Exception):
    """Base for all LLM client errors."""


class LLMUnavailableError(LLMError):
    """Endpoint unreachable or misconfigured (down, wrong URL, bad key)."""


class LLMModelNotFoundError(LLMError):
    """The configured model isn't loaded on the server."""


class LLMTimeoutError(LLMError):
    """Request timed out — a cold model start or a large input can cause it."""


# ── Vision-capability cache ────────────────────────────────────────────────
#
# Probing a new model for image support costs one round-trip; caching the
# answer to disk means we don't re-probe on every restart or pay it on every
# call. Keyed by model name, so swapping models is a clean slate.

@dataclass
class ModelCapabilities:
    """JSON-backed capability cache.

    ``path=None`` makes the cache in-memory only — useful for tests and
    one-shot CLI invocations that shouldn't leak state to disk.
    """
    path: "os.PathLike | None" = None

    def __post_init__(self) -> None:
        self._cache: dict[str, dict] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        import json
        from pathlib import Path
        p = Path(self.path) if self.path else None
        if p and p.exists():
            try:
                data = json.loads(p.read_text())
                if isinstance(data, dict):
                    self._cache = data
            except (json.JSONDecodeError, OSError):
                self._cache = {}
        self._loaded = True

    def _save(self) -> None:
        if not self.path:
            return
        import json
        from pathlib import Path
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self._cache, indent=2, sort_keys=True))
        tmp.replace(p)

    def supports_vision(self, model: str) -> "bool | None":
        """Tri-state: ``None`` not yet probed, else the cached answer."""
        self._load()
        entry = self._cache.get(model)
        if not entry or "vision" not in entry:
            return None
        return bool(entry["vision"])

    def record_vision(self, model: str, supported: bool) -> None:
        import datetime as dt
        self._load()
        entry = self._cache.setdefault(model, {})
        entry["vision"] = bool(supported)
        entry["probed_at"] = (
            dt.datetime.now(dt.timezone.utc)
            .replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        self._save()


@dataclass(frozen=True)
class LLMImage:
    """One image to attach to a multimodal request: raw bytes + MIME type.

    Any object exposing ``.data`` and ``.mime`` works with `complete()` —
    this is just the convenient default.
    """
    data: bytes
    mime: str


# A 32×32 white PNG — small enough to be cheap on the wire, large enough
# to satisfy vision-tower patch-size requirements (14×14 / 16×16 ViTs).
# A 1×1 PNG triggers HTTP 500 in mlx_vlm because the image is smaller
# than one patch — that surfaced as "vision unsupported" in early probes.
_PROBE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAN0lE"
    "QVR4nO3RwQ0AMAjDwJT9d05HMB9+vgGCZF7bXJrT9XhgwR8gEyET"
    "IRMhEyETIRMhEyEThXzH8QM9OMM6fAAAAABJRU5ErkJggg=="
)

# Substrings text-only models emit when rejecting a multimodal request —
# used to tell "no vision" (cache it) from "transport flaked" (don't).
_NO_VISION_HINTS = (
    "image", "vision", "multimodal", "modality",
    "image_url", "unsupported content",
)


class LLM:
    """An OpenAI-compatible chat client scoped to one stacklet/bot.

    ``namespace`` is the role prefix: with ``namespace="archivist-bot"``,
    ``complete("classifier", ...)`` resolves the model for the role
    ``"archivist-bot/classifier"``. A role already containing "/" is used
    verbatim, so callers can reach across namespaces when needed.
    """

    def __init__(self, client: AsyncOpenAI, *, namespace: str | None = None,
                 capabilities: ModelCapabilities | None = None):
        self._client = client
        self.namespace = namespace
        # Default to in-memory; bots inject a disk-backed cache so the
        # vision probe survives container restarts.
        self.capabilities = capabilities or ModelCapabilities()

    @classmethod
    def from_env(cls, *, namespace: str | None = None,
                 capabilities: ModelCapabilities | None = None,
                 max_retries: int = 1) -> "LLM":
        """Build from OPENAI_URL / OPENAI_KEY in the environment.

        ``max_retries=1`` is deliberate: the SDK retries connection/timeout
        errors, but a model that's still loading should surface as a
        timeout quickly rather than be masked by many retries.

        Refuses to build a client when ``OPENAI_URL`` is empty: the SDK
        would silently fall back to ``api.openai.com``, which for a
        privacy-first family server is the wrong default to ever reach
        by accident. Callers must point at a configured endpoint.
        """
        url = os.environ.get("OPENAI_URL", "").rstrip("/")
        # The SDK appends /chat/completions to base_url; tolerate callers
        # who set the full endpoint instead of the /v1 root.
        if url.endswith("/chat/completions"):
            url = url[: -len("/chat/completions")]
        if not url:
            raise LLMUnavailableError(
                "No AI endpoint configured — set up AI with 'stack up ai'"
            )
        key = os.environ.get("OPENAI_KEY", "") or "not-needed"
        client = AsyncOpenAI(base_url=url, api_key=key, max_retries=max_retries)
        return cls(client, namespace=namespace, capabilities=capabilities)

    def _full_role(self, role: str) -> str:
        if "/" in role or not self.namespace:
            return role
        return f"{self.namespace}/{role}"

    async def complete(self, role: str, prompt: str, *,
                       images: "list | None" = None,
                       json_mode: bool = False,
                       model_override: str | None = None) -> str:
        """Run a single chat completion and return the response text.

        ``role`` resolves to a concrete model via `resolve_model`. Pass
        ``images`` (objects with ``.data``/``.mime``) for a multimodal
        call, and ``json_mode=True`` to ask for a JSON object back. SDK
        errors are translated to the typed LLM errors above.
        """
        model = model_override or resolve_model(self._full_role(role))
        content = prompt if not images else _content_parts(prompt, images)

        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = await self._client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                **kwargs,
            )
        # Order matters: APITimeoutError < APIConnectionError, and
        # Authentication/NotFound < APIStatusError — catch specific first.
        except openai.APITimeoutError as e:
            raise LLMTimeoutError(f"{model} — model may still be loading, try again") from e
        except openai.AuthenticationError as e:
            raise LLMUnavailableError("Authentication failed — check [ai].openai_key in stack.toml") from e
        except openai.NotFoundError as e:
            raise LLMModelNotFoundError(f"{model} — is it loaded on the AI server?") from e
        except openai.APIConnectionError as e:
            raise LLMUnavailableError("No AI endpoint reachable — set up AI with 'stack up ai'") from e
        except openai.APIStatusError as e:
            raise LLMUnavailableError(f"HTTP {e.status_code}: {str(e)[:200]}") from e

        return resp.choices[0].message.content or ""

    async def has_vision(self, *, role: str = "classifier",
                         model_override: str | None = None) -> bool:
        """Does the model for ``role`` accept image inputs? Cached per model.

        First call per model sends a tiny image with a trivial prompt. A
        success means vision works; an error whose body mentions the
        multimodal vocabulary means text-only (cache it). Anything else is
        inconclusive — return False without caching, so we retry next run.
        """
        model = model_override or resolve_model(self._full_role(role))
        cached = self.capabilities.supports_vision(model)
        if cached is not None:
            return cached

        probe_img = LLMImage(data=base64.b64decode(_PROBE_PNG_B64), mime="image/png")
        try:
            await self.complete(role, "Reply with the single word 'ok'.",
                                images=[probe_img], model_override=model)
            self.capabilities.record_vision(model, True)
            logger.info("[llm] vision probe: {} -> supported", model)
            return True
        except (LLMUnavailableError, LLMModelNotFoundError) as e:
            if any(hint in str(e).lower() for hint in _NO_VISION_HINTS):
                self.capabilities.record_vision(model, False)
                logger.info("[llm] vision probe: {} -> text-only", model)
                return False
            logger.warning("[llm] vision probe inconclusive for {}: {}", model, e)
            return False
        except LLMTimeoutError:
            logger.warning("[llm] vision probe timed out for {}", model)
            return False

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Owned by us via from_env()."""
        await self._client.close()


def _content_parts(prompt: str, images: list) -> list[dict]:
    """OpenAI-style multimodal content: one text part + N image_url parts.

    Images are inlined as ``data:`` URLs so we never expose a public image
    URL — every vision backend (oMLX, Ollama, OpenAI-compat) accepts this.
    """
    parts: list[dict] = [{"type": "text", "text": prompt}]
    for img in images:
        b64 = base64.b64encode(img.data).decode("ascii")
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{img.mime};base64,{b64}"},
        })
    return parts


# ── Transcription ────────────────────────────────────────────────────────
#
# Whisper-server (whisper.cpp) speaks the OpenAI ``/v1/audio/transcriptions``
# shape, so the same SDK we use for chat handles speech-to-text. It lives
# on a different base URL than the LLM, though — the AI stacklet runs the
# LLM (oMLX) and whisper as two native services on different ports — so
# this class owns its own AsyncOpenAI instance pointed at ``WHISPER_URL``.
# Folding it into ``LLM`` would conflate two unrelated endpoints under one
# client.

# Whisper-server has exactly one model loaded; the SDK still requires a
# ``model`` argument, so we send the canonical OpenAI name. The local
# server ignores it and dispatches to whatever it loaded at startup.
_DEFAULT_WHISPER_MODEL = "whisper-1"


# whisper.cpp output is raw: no punctuation, no capitalization, no
# sentence breaks. The optional cleanup pass restores those without
# changing what was said. The prompt is deliberately strict — content
# drift would silently corrupt the family's note vault.
#
# We send the rules as a single user message because the framework LLM
# client doesn't take a system role today; modern small models honour
# these constraints reliably when the rules are imperative and the
# input is the last thing in the prompt.
_CLEANUP_ROLE = "transcript_cleanup"
_CLEANUP_PROMPT = """\
You are cleaning up a raw speech-to-text transcript that has no \
punctuation, capitalization, or sentence breaks. Restore them in the \
SAME language as the input.

Rules you MUST follow:
- Do not change, add, remove, or reorder any words.
- Do not correct grammar or word choice.
- Do not summarise, expand, rephrase, or translate.
- Keep every word in the original language and original order.
- Add ONLY: punctuation, capitalization, sentence breaks, paragraph breaks.

Reply with ONLY the cleaned transcript. No preamble, no commentary, no \
code fences, no quotes around the output.

Raw transcript:
{raw}
"""


class Transcriber:
    """OpenAI-compatible speech-to-text scoped to one stacklet.

    ``namespace`` mirrors the role-prefix idea from ``LLM`` even though
    whisper-server doesn't route by role today — recording it keeps the
    door open for per-bot model routing (different whisper variants for
    archivist vs scribe) without touching call sites later.
    """

    def __init__(self, client: AsyncOpenAI, *, namespace: str | None = None):
        self._client = client
        self.namespace = namespace

    @classmethod
    def from_env(cls, *, namespace: str | None = None,
                 max_retries: int = 1) -> "Transcriber":
        """Build from ``WHISPER_URL`` in the environment.

        The bot-runner env renders ``WHISPER_URL`` as the full endpoint
        (``…/v1/audio/transcriptions``) for the legacy raw-HTTP path; the
        SDK appends ``/audio/transcriptions`` itself, so we strip a
        trailing endpoint suffix the same way ``LLM.from_env`` strips
        ``/chat/completions``. Either form just works.

        Refuses to build a client when ``WHISPER_URL`` is empty: the SDK
        would silently fall back to ``api.openai.com``, which for a
        privacy-first family server is the wrong default to ever reach
        by accident.
        """
        url = os.environ.get("WHISPER_URL", "").rstrip("/")
        if url.endswith("/audio/transcriptions"):
            url = url[: -len("/audio/transcriptions")]
        if not url:
            raise LLMUnavailableError(
                "No whisper endpoint configured — set up AI with 'stack up ai'"
            )
        # Native whisper-server doesn't authenticate, but the SDK insists
        # on *some* key — same trick as LLM.from_env.
        key = os.environ.get("WHISPER_KEY", "") or "not-needed"
        client = AsyncOpenAI(base_url=url, api_key=key, max_retries=max_retries)
        return cls(client, namespace=namespace)

    async def transcribe(self, audio: bytes, *, filename: str = "voice.ogg",
                         model: str | None = None,
                         cleanup_with: "LLM | None" = None) -> str:
        """Transcribe audio bytes to text, stripped of leading/trailing space.

        ``filename`` lets the server pick a decoder by extension (whisper.cpp
        sniffs the container from the filename). ``model`` is forwarded to
        the SDK for OpenAI-compat servers that route by model name; the
        native whisper-server ignores it.

        ``cleanup_with`` is an optional :class:`LLM` to polish the raw STT
        output with punctuation and sentence breaks. When provided, the
        result is the LLM-cleaned text; when omitted or the LLM call
        fails, the raw transcript is returned unchanged. Cleanup is a
        best-effort polish, never a hard requirement -- a voice memo
        without punctuation still beats no transcript at all.

        SDK errors from the STT call are translated to the same typed
        LLM errors :class:`LLM` uses so callers can ``except LLMError``
        once for both surfaces.
        """
        try:
            resp = await self._client.audio.transcriptions.create(
                model=model or _DEFAULT_WHISPER_MODEL,
                file=(filename, audio),
                response_format="json",
            )
        except openai.APITimeoutError as e:
            raise LLMTimeoutError(
                "whisper — transcription timed out, try a shorter clip"
            ) from e
        except openai.AuthenticationError as e:
            raise LLMUnavailableError(
                "Whisper authentication failed — check [ai].whisper_key in stack.toml"
            ) from e
        except openai.APIConnectionError as e:
            raise LLMUnavailableError(
                "Whisper server unreachable — check 'stack status ai'"
            ) from e
        except openai.APIStatusError as e:
            raise LLMUnavailableError(f"HTTP {e.status_code}: {str(e)[:200]}") from e

        # The SDK returns a Transcription object whose `.text` mirrors
        # whisper-server's `{"text": ...}` response. Strip incidental
        # whitespace so callers don't have to.
        raw = (getattr(resp, "text", "") or "").strip()
        if not raw or cleanup_with is None:
            return raw
        return await self._cleanup(raw, cleanup_with)

    @staticmethod
    async def _cleanup(raw: str, llm: "LLM") -> str:
        """Run the LLM-cleanup pass; fall back to raw on any failure.

        Cleanup is best-effort: if the LLM is down or the model returns
        empty output, the caller still gets a usable transcript. We log
        a warning so the admin can see drift between raw and clean if
        they ever want to investigate model quality.
        """
        try:
            cleaned = await llm.complete(
                _CLEANUP_ROLE, _CLEANUP_PROMPT.format(raw=raw),
            )
        except LLMError as e:
            logger.warning("[transcriber] cleanup failed, returning raw: {}", e)
            return raw
        cleaned = (cleaned or "").strip()
        # A model that returned nothing is no better than no model.
        return cleaned or raw

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Owned by us via from_env()."""
        await self._client.close()
