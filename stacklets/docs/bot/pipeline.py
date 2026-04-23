"""Document enrichment pipeline — shared by the archivist bot and the docs CLI.

The archivist bot runs this on every new Paperless upload; the
`stack docs reprocess` CLI runs the same pipeline against already-filed
documents when the operator wants to re-tag, re-title, or refresh a stale
classification after taxonomy changes.

Design boundary: the module holds no Matrix, chat, or stdout state. Callers
feed in a fully-fetched Paperless doc dict and a classifier; they receive an
`EnrichResult` they can render into whatever surface they own (chat reply,
Forgejo mirror frontmatter, terminal diff).

Split of concerns:

    PaperlessAPI   async HTTP wrapper — every endpoint the pipeline touches
    Classifier     OpenAI-compatible client — owns the prompts and parsing
    enrich_document  classify → reconcile via matching.py → PATCH Paperless
    reformat_document  LLM-rewrite the body → PATCH content (best-effort)

Why two top-level functions rather than a class: neither has state worth
holding between calls. A bot processes one upload at a time; the CLI walks
a list of ids sequentially. Functions thread the collaborators explicitly,
which makes the contract visible in every call site.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from loguru import logger

from capabilities import ModelCapabilities
from matching import (
    MAX_TITLE_LENGTH,
    _is_empty,
    fuzzy_match_entity,
    match_persons,
    match_topics,
)
from stack import resolve_model


# A 32×32 white PNG — small enough to be cheap on the wire, large enough
# to satisfy vision-tower patch-size requirements (14×14 / 16×16 ViTs).
# A 1×1 PNG triggers HTTP 500 in mlx_vlm because the image is smaller
# than one patch — that surfaced as "vision unsupported" in early probes.
_PROBE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAN0lE"
    "QVR4nO3RwQ0AMAjDwJT9d05HMB9+vgGCZF7bXJrT9XhgwR8gEyET"
    "IRMhEyETIRMhEyEThXzH8QM9OMM6fAAAAABJRU5ErkJggg=="
)

# Substrings that show up in error responses from text-only models when
# they reject a multimodal request — used to distinguish "no vision" (a
# definitive answer worth caching) from "transport flaked" (don't cache).
_NO_VISION_HINTS = (
    "image", "vision", "multimodal", "modality",
    "image_url", "unsupported content",
)


# ── Errors ───────────────────────────────────────────────────────────────

class LLMUnavailableError(Exception):
    """LLM service is not reachable — oMLX/Ollama might not be running."""


class LLMModelNotFoundError(Exception):
    """The configured model is not loaded on the LLM server."""


class LLMTimeoutError(Exception):
    """LLM took too long — large documents or cold model start can cause this."""


class PaperlessDuplicateError(Exception):
    """Paperless rejected the upload as a content-hash duplicate.

    Carries the original doc's id + title so the caller can point the user
    at what's already filed instead of reporting a generic upload failure.
    """
    def __init__(self, doc_id: int | None, title: str | None):
        self.doc_id = doc_id
        self.title = title
        super().__init__(f"duplicate of #{doc_id}: {title}")


_DUPLICATE_RE = re.compile(r"duplicate of\s+(.+?)\s+\(#(\d+)\)", re.IGNORECASE)


# ── Enrichment result ────────────────────────────────────────────────────

@dataclass
class EnrichResult:
    """Structured outcome of enrich_document().

    `classification` holds the raw LLM response — summary, facts,
    action_items, and anything else the caller wants to render. The
    `resolved_*` fields carry the values actually written to Paperless
    after fuzzy matching, so a mirror entry or CLI diff can show what
    the archive agreed to rather than what the LLM first proposed.
    """
    classification: dict = field(default_factory=dict)
    resolved_topics: list[str] = field(default_factory=list)
    resolved_persons: list[str] = field(default_factory=list)
    resolved_correspondent: str | None = None
    resolved_type: str | None = None
    created_new: list[str] = field(default_factory=list)
    updates_applied: dict = field(default_factory=dict)
    # The Markdown summary written to Paperless as a note (Summary /
    # Facts / Parties / Action). None when the classifier produced
    # nothing worth recording — callers can use this both as "was
    # anything written" and as the rendered text to echo back to the
    # user. "Note" is Paperless's storage concept; "summary" is ours.
    summary: str | None = None
    # When classify raised, ("unavailable" | "model_missing" | "timeout", detail)
    llm_error: tuple[str, str] | None = None


# ── Paperless HTTP wrapper ───────────────────────────────────────────────

class PaperlessAPI:
    """Async client for every Paperless endpoint the docs stacklet touches.

    Used by enrich_document / reformat_document (entity reads + updates)
    and by the archivist bot (upload + OCR task polling + search). The bot
    and CLI share the same instance shape, which keeps Paperless HTTP
    errors, header wiring, and pagination in one place.
    """

    def __init__(self, http: aiohttp.ClientSession, url: str, token: str):
        self.http = http
        self.url = url.rstrip("/")
        self.token = token
        self._user_id_cache: int | None = None

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Token {self.token}"}

    @property
    def _json_headers(self) -> dict:
        return {**self._headers, "Content-Type": "application/json"}

    # ── HTTP helper ──────────────────────────────────────────────────
    #
    # One request method instead of an `async with` block per endpoint.
    # Returns (parsed_body, status). parsed_body is JSON when the server
    # returned JSON, raw text otherwise, and None on 204 / non-success.
    # Callers decide what counts as success via `expect` so the method
    # stays neutral about retry / fallback policy.

    async def _req(
        self, method: str, path: str, *,
        json_body: dict | None = None,
        params: dict | None = None,
        expect: tuple[int, ...] = (200,),
    ) -> tuple[Any, int]:
        headers = self._json_headers if json_body is not None else self._headers
        try:
            async with self.http.request(
                method, f"{self.url}{path}",
                headers=headers, json=json_body, params=params,
            ) as resp:
                if resp.status not in expect:
                    return None, resp.status
                if resp.status == 204:
                    return None, 204
                ctype = resp.headers.get("Content-Type", "")
                body = await resp.json() if "application/json" in ctype else await resp.text()
                return body, resp.status
        except (aiohttp.ClientError, OSError) as e:
            logger.debug("[pipeline] {} {} failed: {}", method, path, e)
            return None, 0

    # ── Document reads ───────────────────────────────────────────────

    async def get_doc(self, doc_id: int) -> dict | None:
        body, _ = await self._req("GET", f"/api/documents/{doc_id}/")
        return body if isinstance(body, dict) else None

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        body, _ = await self._req(
            "GET", "/api/documents/",
            params={"query": query, "page_size": limit, "ordering": "-created"},
        )
        return body.get("results", []) if isinstance(body, dict) else []

    async def _list_entity(self, endpoint: str) -> dict:
        body, _ = await self._req(
            "GET", f"/api/{endpoint}/", params={"page_size": "1000"},
        )
        if not isinstance(body, dict):
            return {}
        return {t["name"]: t["id"] for t in body.get("results", [])}

    async def get_tags(self) -> dict:
        return await self._list_entity("tags")

    async def get_doc_types(self) -> dict:
        return await self._list_entity("document_types")

    async def get_correspondents(self) -> dict:
        return await self._list_entity("correspondents")

    async def update_doc(self, doc_id: int, updates: dict) -> bool:
        _, status = await self._req(
            "PATCH", f"/api/documents/{doc_id}/", json_body=updates,
        )
        return status == 200

    # ── Upload + OCR ─────────────────────────────────────────────────

    async def upload(self, filename: str, data: bytes,
                     content_type: str | None = None) -> str | None:
        """Post a file to /post_document/. Returns the task id on success.

        When `content_type` is given it's set on the multipart field —
        important for text-like files where aiohttp's default of
        application/octet-stream stops Paperless from matching a parser
        and the server returns 400 with no useful diagnostic. On failure
        we log the response body (truncated) so the next 400 isn't a
        mystery.
        """
        form = aiohttp.FormData()
        field_kwargs: dict = {"filename": filename}
        if content_type:
            field_kwargs["content_type"] = content_type
        form.add_field("document", data, **field_kwargs)
        try:
            async with self.http.post(
                f"{self.url}/api/documents/post_document/",
                headers=self._headers, data=form,
            ) as resp:
                if resp.status == 200:
                    task_id = (await resp.text()).strip().strip('"')
                    logger.info("[pipeline] Uploaded {} → task {}", filename, task_id)
                    return task_id
                body = (await resp.text())[:400]
                logger.error("[pipeline] Upload failed (HTTP {}): {} — body: {}",
                             resp.status, filename, body)
                return None
        except (aiohttp.ClientConnectionError, aiohttp.ClientError, OSError) as e:
            logger.error("[pipeline] Paperless unreachable: {}", e)
            return None

    async def wait_task(self, task_id: str, timeout: int = 120) -> int | None:
        """Poll /tasks/ until the task completes.

        Raises PaperlessDuplicateError when Paperless rejects the upload
        as a duplicate; returns None for every other FAILURE / timeout /
        transport error so the caller can render a generic failure.
        """
        import time
        start = time.time()
        while time.time() - start < timeout:
            try:
                async with self.http.get(
                    f"{self.url}/api/tasks/?task_id={task_id}",
                    headers=self._headers,
                ) as resp:
                    if resp.status == 200:
                        tasks = await resp.json()
                        if tasks:
                            task = tasks[0] if isinstance(tasks, list) else tasks
                            status = task.get("status", "")
                            if status == "SUCCESS":
                                doc_id = task.get("related_document")
                                return int(doc_id) if doc_id else None
                            if status == "FAILURE":
                                result = task.get("result") or ""
                                logger.error("[pipeline] Task failed: {}", result)
                                match = _DUPLICATE_RE.search(result)
                                if match:
                                    title = match.group(1).strip()
                                    dup_id = int(match.group(2))
                                    raise PaperlessDuplicateError(dup_id, title)
                                return None
            except (aiohttp.ClientConnectionError, aiohttp.ClientError, OSError) as e:
                logger.error("[pipeline] Paperless unreachable while waiting for task: {}", e)
                return None
            await asyncio.sleep(3)
        logger.error("[pipeline] Task {} timed out", task_id)
        return None

    # ── Entity creation ──────────────────────────────────────────────
    #
    # All entities use matching_algorithm=0 (disabled). The LLM classifies
    # every document; Paperless just stores what the LLM decides.
    #
    # Why not auto-learn (algorithm 6)?
    #
    # Paperless auto-assigns during document consumption — BEFORE the LLM
    # runs. With algorithm 6, Paperless learns from the first few
    # LLM-assigned documents, then starts pre-assigning based on that tiny
    # sample:
    #
    #   1. LLM tags three invoices as "Shopping"
    #   2. Paperless learns: "Shopping" = common tag
    #   3. Paperless auto-assigns "Shopping" to every new document at ingest
    #   4. LLM adds the correct tag, but "Shopping" is already there too
    #   5. Result: every document gets "Shopping" regardless of content
    #
    # Same failure mode hit correspondents: "Denny Gunawan" (first
    # correspondent created) got auto-assigned to every subsequent document.
    #
    # Algorithm 0 means: Paperless never guesses. The LLM reads the actual
    # document text and makes the call. If the LLM is unavailable,
    # documents get filed without tags — the user can classify manually in
    # the UI.

    async def _create_entity(self, endpoint: str, body: dict) -> int | None:
        data, _ = await self._req(
            "POST", f"/api/{endpoint}/", json_body=body, expect=(201,),
        )
        return data["id"] if isinstance(data, dict) and "id" in data else None

    async def create_tag(self, name: str, color: str = "#9e9e9e") -> int | None:
        return await self._create_entity("tags", {
            "name": name, "color": color,
            "matching_algorithm": 0, "is_insensitive": True,
        })

    async def create_doc_type(self, name: str) -> int | None:
        return await self._create_entity("document_types", {
            "name": name, "matching_algorithm": 0, "is_insensitive": True,
        })

    async def create_correspondent(self, name: str) -> int | None:
        return await self._create_entity("correspondents", {
            "name": name, "matching_algorithm": 0,
        })

    # ── Notes ────────────────────────────────────────────────────────
    #
    # Paperless notes are free-form Markdown attached to a document and
    # included in Paperless's full-text search index. The classifier
    # writes a structured note (Summary / Facts / Parties / Action) so
    # the search backend can answer "which doc mentioned the EUR 440
    # invoice" without re-running the LLM at query time.
    #
    # Idempotency: every note carries a `user` foreign key set to whoever
    # authenticated the POST. The bot uses its own Paperless account, so
    # on reclassify we fetch /users/me/ once, then delete only notes whose
    # owner matches — human-added notes (different user) survive.

    async def get_current_user_id(self) -> int | None:
        """Return the Paperless user id behind this token, cached.

        Falls back to None if the endpoint is unreachable or returns an
        unexpected shape — callers treat that as "can't tell mine from
        human's" and skip the prior-note delete sweep.
        """
        if self._user_id_cache is not None:
            return self._user_id_cache
        body, _ = await self._req("GET", "/api/users/me/")
        if isinstance(body, dict) and isinstance(body.get("id"), int):
            self._user_id_cache = body["id"]
            return body["id"]
        return None

    async def list_notes(self, doc_id: int) -> list[dict]:
        body, _ = await self._req("GET", f"/api/documents/{doc_id}/notes/")
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            return body.get("results", []) or []
        return []

    async def add_note(self, doc_id: int, text: str) -> bool:
        _, status = await self._req(
            "POST", f"/api/documents/{doc_id}/notes/",
            json_body={"note": text}, expect=(200, 201),
        )
        return status in (200, 201)

    async def delete_note(self, doc_id: int, note_id: int) -> bool:
        _, status = await self._req(
            "DELETE", f"/api/documents/{doc_id}/notes/",
            params={"id": str(note_id)}, expect=(200, 204),
        )
        return status in (200, 204)


# ── LLM client ───────────────────────────────────────────────────────────

class Classifier:
    """OpenAI-compatible classifier + reformatter.

    Resolves the concrete model via the framework's `resolve_model(path)`
    chain — "archivist-bot/classifier" falls back through bot-level and
    global defaults. Callers set AI_DEFAULT_MODEL / AI_MODELS_JSON in the
    environment before building a Classifier so resolve_model sees the
    same config the bot runtime does.

    classify() raises on transport / model errors so the caller can
    distinguish 'the LLM is down' from 'the LLM returned nothing'.
    reformat() is best-effort and swallows the same errors, returning
    None — rewriting OCR is enrichment, never a gate.
    """

    def __init__(self, http: aiohttp.ClientSession, url: str,
                 key: str = "", bot_name: str = "archivist-bot",
                 capabilities: ModelCapabilities | None = None):
        self.http = http
        self.url = url.rstrip("/")
        self.key = key
        self.bot_name = bot_name
        # Capabilities default to in-memory only — bots inject a
        # disk-backed instance so the probe survives container restarts.
        self.capabilities = capabilities or ModelCapabilities()

    def _endpoint(self) -> str:
        if not self.url:
            raise LLMUnavailableError("No AI endpoint configured — set up AI with 'stack up ai'")
        return self.url if self.url.endswith("/chat/completions") else f"{self.url}/chat/completions"

    async def _request(self, task: str, content: Any, *,
                       json_mode: bool = False,
                       model_override: str | None = None) -> str:
        """Send content to the LLM and return the response text.

        `content` is either a plain string (text-only call, the historic
        path) or a list of OpenAI-style content parts (multimodal — see
        `_multimodal_content`). Both shapes go through the same wire
        format; the chat completions API accepts either as `content`.

        `model_override` skips `resolve_model` — used by the vision
        probe so we test the *actual* model the classifier would use,
        not a different model the resolver might return.

        The task name (e.g. "classifier", "reformat") is resolved to a
        concrete model via resolve_model("<bot>/<task>"). The fallback chain:

          1. [ai.models] <bot>.<task> — task-specific override
          2. [ai.models] <bot>        — bot-level default
          3. [ai] default             — global fallback

        Uses the OpenAI-compatible chat completions API — works with oMLX,
        Ollama, LM Studio, or any provider that serves /v1/chat/completions.
        """
        model = model_override or resolve_model(f"{self.bot_name}/{task}")

        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"

        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        try:
            async with self.http.post(
                self._endpoint(), headers=headers, json=body,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result["choices"][0]["message"]["content"]
                if resp.status == 401:
                    raise LLMUnavailableError("Authentication failed — check [ai].openai_key in stack.toml")
                if resp.status == 404:
                    body_text = await resp.text()
                    if "not found" in body_text.lower():
                        raise LLMModelNotFoundError(f"{model} — is it loaded in oMLX?")
                    raise LLMUnavailableError(f"HTTP 404: {body_text[:200]}")
                # Other 4xx/5xx — let the caller see the body so probes
                # can tell "model rejected the image" from "transport flake".
                body_text = await resp.text()
                raise LLMUnavailableError(f"HTTP {resp.status}: {body_text[:300]}")
        except asyncio.TimeoutError:
            raise LLMTimeoutError(f"{model} — model may still be loading, try again")
        except (LLMUnavailableError, LLMModelNotFoundError, LLMTimeoutError):
            raise
        except Exception as e:
            raise LLMUnavailableError(f"{e}")

    # ── Multimodal helpers ───────────────────────────────────────────

    @staticmethod
    def _multimodal_content(prompt: str, image_data: bytes,
                            image_mime: str) -> list[dict]:
        """Build OpenAI-style multimodal content: text + one image.

        Encoded as a `data:` URL so we don't have to expose a public
        image URL — every backend that supports vision (oMLX, Ollama,
        OpenAI, Anthropic via OpenAI-compat) accepts this form.
        """
        b64 = base64.b64encode(image_data).decode("ascii")
        return [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:{image_mime};base64,{b64}"}},
        ]

    async def has_vision(self, model: str | None = None) -> bool:
        """Does this model accept image inputs? Cached after first probe.

        On the first call for a given model: send a 1×1 PNG with a
        trivial text prompt. A 200 response → vision works. An error
        whose body mentions the multimodal vocabulary ("image",
        "vision", "multimodal", "modality") → text-only, cache as such.
        Anything else (timeout, network flake) → return False without
        caching, so we'll retry next session.
        """
        model = model or resolve_model(f"{self.bot_name}/classifier")

        cached = self.capabilities.supports_vision(model)
        if cached is not None:
            return cached

        try:
            await self._request(
                "classifier",
                self._multimodal_content(
                    "Reply with the single word 'ok'.",
                    base64.b64decode(_PROBE_PNG_B64),
                    "image/png",
                ),
                model_override=model,
            )
            self.capabilities.record_vision(model, True)
            logger.info("[pipeline] vision probe: {} → supported", model)
            return True
        except (LLMUnavailableError, LLMModelNotFoundError) as e:
            msg = str(e).lower()
            if any(hint in msg for hint in _NO_VISION_HINTS):
                self.capabilities.record_vision(model, False)
                logger.info("[pipeline] vision probe: {} → text-only", model)
                return False
            # Inconclusive — don't poison the cache, just say no for now.
            logger.warning(
                "[pipeline] vision probe inconclusive for {}: {} — "
                "treating as text-only this run", model, e,
            )
            return False
        except LLMTimeoutError:
            logger.warning("[pipeline] vision probe timed out for {}", model)
            return False

    async def classify(
        self, *,
        ocr_text: str,
        tags: dict,
        doc_types: dict,
        correspondents: dict,
        image_data: bytes | None = None,
        image_mime: str | None = None,
    ) -> dict:
        """Ask the LLM to classify a document based on its OCR text.

        When `image_data` + `image_mime` are supplied AND the model has
        vision capability (cached probe), the image rides alongside the
        text prompt as a multimodal message. The text prompt is
        unchanged — the image is supplementary context, not a
        replacement for OCR. Belt-and-braces: image catches layout and
        logos, OCR catches small print and numbers.

        Returns structured JSON with:
          - topics: subject areas (1-2 tags, e.g. ["Insurance", "Medical"])
          - persons: which family members this belongs to
          - correspondent: who sent / issued this document
          - document_type: optional format (Invoice, Receipt, ...)
          - title, date, summary, facts, action_items (rendered by caller)

        Invalid JSON from the LLM → {} (logged warning). Transport failures
        raise LLMUnavailableError / LLMModelNotFoundError / LLMTimeoutError
        so the caller can distinguish 'LLM is down' from 'LLM gave nothing'.
        """
        person_tags = [t for t in tags if t.startswith("Person: ")]
        person_names = [t.replace("Person: ", "") for t in person_tags]
        category_tags = [t for t in tags if not t.startswith("Person: ")]
        prompt = _build_classify_prompt(
            ocr_text=ocr_text,
            person_names=person_names,
            category_tags=category_tags,
            doc_types=list(doc_types.keys()),
            correspondents=list(correspondents.keys()),
        )

        content: Any = prompt
        if image_data and image_mime and image_mime.startswith("image/"):
            if await self.has_vision():
                content = self._multimodal_content(prompt, image_data, image_mime)
                logger.info(
                    "[pipeline] classify: attaching image ({} bytes, {})",
                    len(image_data), image_mime,
                )

        response = await self._request("classifier", content, json_mode=True)
        if not response:
            return {}
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            logger.warning("[pipeline] LLM returned invalid JSON: {}", response[:200])
            return {}

    async def reformat(self, ocr_text: str) -> str | None:
        """Reformat raw OCR text into clean, readable Markdown.

        OCR output is often messy: broken lines, garbled characters, no
        structure. The LLM fixes artifacts while preserving all factual
        content. The reformatted text replaces the original in Paperless,
        making documents actually readable.

        Non-critical — transport failures return None and the caller's
        fallback is to keep the raw OCR. Length-based usability filtering
        is the pipeline's job (see `reformat_document`); this returns
        whatever the LLM produced, trimmed.
        """
        prompt = _build_reformat_prompt(ocr_text[:6000])
        try:
            result = await self._request("reformat", prompt)
        except (LLMUnavailableError, LLMModelNotFoundError, LLMTimeoutError):
            return None
        return result.strip() if result else None


# ── Prompts ──────────────────────────────────────────────────────────────
#
# Extracted into module-level builders so the bot and CLI can print them
# for debugging (`--dry-run --show-prompt` may land in v1.1) without
# depending on classifier internals.

def _build_classify_prompt(*, ocr_text: str, person_names: list[str],
                           category_tags: list[str],
                           doc_types: list[str],
                           correspondents: list[str]) -> str:
    """The classification prompt.

    Simplified to three clear axes:
      topic         = what is this about?   "Insurance", "Shopping"
      person        = which family member?  "Homer", "Bart", or null
      correspondent = who sent it?          "Springfield Nuclear", "Kwik-E-Mart"

    Worked examples — a Kwik-E-Mart receipt for Homer:
      topic="Shopping", person="Homer", correspondent="Kwik-E-Mart"
    A school letter about Bart:
      topics=["School"], person="Bart", correspondent="Springfield Elementary"
    A health insurance invoice for Homer:
      topics=["Insurance", "Medical"], person="Homer", correspondent="AOK"

    Person names arrive stripped of the "Person: " prefix so the LLM sees
    clean first names like "Homer" rather than "Person: Homer".
    """
    return f"""Classify this document. Return ONLY a JSON object.

IMPORTANT: Always prefer existing values from the lists below. Only suggest
a new value when NOTHING in the list is a reasonable match.

Family members: {json.dumps(person_names, ensure_ascii=False)}
Existing topic tags: {json.dumps(category_tags, ensure_ascii=False)}
Existing document types: {json.dumps(doc_types, ensure_ascii=False)}
Existing correspondents: {json.dumps(correspondents, ensure_ascii=False)}

Return this exact JSON structure:
{{
  "title": "scannable title: [Correspondent] - [what] [key amount]. E.g. 'Anthropic - Max Plan EUR 90.00', 'ADAC - Kfz-Versicherung 2026 EUR 340'. Must be useful when scanning a list of 500 documents. Max 128 chars. Document's language.",
  "date": "YYYY-MM-DD or null",
  "topics": ["what is this document about? One or two subject areas. E.g. ['Insurance'], ['Insurance', 'Vehicle'], ['Shopping']. A health insurance bill is ['Insurance', 'Medical']. A car repair invoice is ['Vehicle']. Pick from existing topic tags when possible. Usually one topic, two only when the document genuinely spans two areas."],
  "persons": ["which family members does this belong to? Pick from the family members list by first name. Can be multiple for joint documents (marriage, family insurance). Empty list if unclear. These are who the document is FOR or ABOUT, not who sent it."],
  "document_type": "optional: what format is this? Invoice, Receipt, Contract, Letter, Certificate, or null if unclear.",
  "correspondent": "the SENDER or ISSUING ORGANIZATION, or null if unclear. Who wrote or sent this? NOT the recipient. On an invoice, this is the company that billed, not the customer. Use the shortest recognizable name. null is better than guessing.",
  "summary": "2-3 sentence summary with key facts: amounts, dates, names, deadlines",
  "facts": ["key structured facts, e.g. 'Total: EUR 90.00', 'Invoice: #12345', 'Plan: Premium'"],
  "action_items": [{{"action": "what needs to happen", "due": "YYYY-MM-DD or null"}}]
}}

Rules:
- LANGUAGE: use the document's original language for title, summary, facts, and action_items. A German document gets a German title and German facts. Never translate.
- topics: the subject area(s), not the document format. An invoice from a shop is ["Shopping"], not ["Invoice"]. An invoice for insurance is ["Insurance"]. A health insurance claim is ["Insurance", "Medical"]. Use the document's language for new topic tags too. Most documents have one topic; use two only when clearly spanning two areas.
- persons: match by first name from the family members list. Can be multiple for joint documents. A marriage certificate for Homer and Marge: ["Homer", "Marge"]. A personal invoice for Homer only: ["Homer"].
- correspondent: always the SENDER, never the addressee/customer/recipient. Use null if the sender is not clearly identifiable. Do not guess from fragments.
- facts: concrete numbers, dates, account numbers, amounts. Empty list if none.
- action_items: deadlines, payments due, forms to return. Empty list if none.

Document text:
---
{ocr_text}
---"""


def _build_reformat_prompt(ocr_text: str) -> str:
    """The OCR-to-clean-markdown prompt."""
    return f"""Reformat this OCR-scanned document into clean, well-structured Markdown.

Rules:
- Fix OCR artifacts, broken lines, and garbled text
- Correct obvious OCR errors in names and words
- Preserve ALL factual content: numbers, dates, names, amounts, addresses
- Structure with appropriate headings, lists, and tables
- Do NOT summarize, translate, or add any content not in the original
- NEVER guess or invent values — mark unreadable text as [unreadable]
- If something is unreadable garbage (TSE signatures, hash strings), omit it
- Keep the document language as-is
- Output ONLY the formatted markdown, nothing else

OCR text:
---
{ocr_text}
---"""


# ── Classifier summary ───────────────────────────────────────────────────
#
# After a document is classified we write a structured Markdown summary
# back to Paperless (stored as a Paperless "note", which is its storage
# concept — we call the thing a summary in this module). The summary is
# FTS-indexed by Paperless, so headline facts become searchable
# alongside the raw OCR text. That's the groundwork for a future "ask a
# question about my archive" path: a shallow keyword hit against the
# bot's summaries is the baseline retrieval before any embedding index
# enters the picture.
#
# Empty short-circuit: when the classifier returned a shape with no
# summary prose, no facts, no actions, _format_classifier_summary
# returns None and the caller skips the write — a doc with nothing
# interesting should not get a stub "## Summary\n" line on its record.

def _format_classifier_summary(
    classification: dict,
    *,
    resolved_persons: list[str],
    resolved_correspondent: str | None,
) -> str | None:
    """Render the classifier payload as the Markdown summary body.

    Sections are conditional; the caller never sees empty `## Summary\n`
    placeholders. Returns None when there is nothing to record — the
    caller should then not write anything at all.
    """
    parts: list[str] = []

    summary = (classification.get("summary") or "").strip()
    if summary:
        parts.append(f"## Summary\n{summary}")

    facts = [str(f).strip() for f in (classification.get("facts") or []) if str(f).strip()]
    if facts:
        parts.append("## Facts\n" + "\n".join(f"- {f}" for f in facts))

    parties = _format_parties(
        correspondent=resolved_correspondent,
        persons=resolved_persons,
    )
    if parties:
        parts.append(f"## Parties\n{parties}")

    return "\n\n".join(parts) if parts else None


def _format_parties(*, correspondent: str | None, persons: list[str]) -> str:
    """"Sender → recipients" one-liner, with either side omitted if empty."""
    left = correspondent.strip() if correspondent else ""
    right = ", ".join(p for p in persons if p) if persons else ""
    if left and right:
        return f"{left} → {right}"
    return left or right


async def _replace_classifier_summary(
    paperless: PaperlessAPI, doc_id: int, summary_text: str,
) -> None:
    """Write `summary_text` as the bot's summary, replacing any prior one.

    Idempotency strategy: the bot's Paperless user owns every note it
    writes. We fetch /users/me/ once (cached), then drop notes whose
    owner matches before posting the new one. Human-added notes have a
    different owner and stay put.

    Fallback: if we can't determine our own user id (endpoint 500, token
    lacks permission), we post the new summary without deleting anything
    — a duplicate is better than losing human edits to an optimistic
    sweep.
    """
    user_id = await paperless.get_current_user_id()
    if user_id is not None:
        for note in await paperless.list_notes(doc_id):
            owner = note.get("user")
            owner_id = owner.get("id") if isinstance(owner, dict) else owner
            if owner_id == user_id and isinstance(note.get("id"), int):
                await paperless.delete_note(doc_id, note["id"])
    await paperless.add_note(doc_id, summary_text)


# ── Enrichment ───────────────────────────────────────────────────────────

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NEW_TOPIC_COLOR = "#4caf50"


DEFAULT_CLASSIFY_MAX_CHARS = 20000


async def enrich_document(
    *,
    paperless: PaperlessAPI,
    classifier: Classifier,
    doc: dict,
    classify_max_chars: int = DEFAULT_CLASSIFY_MAX_CHARS,
    image_data: bytes | None = None,
    image_mime: str | None = None,
) -> EnrichResult:
    """Classify a doc, reconcile entities, PATCH Paperless. Pure data out.

    No HTTP assumptions about the caller: collaborators are injected.
    No stdout / Matrix concerns: caller renders the result. Never raises:
    LLM / Paperless failures arrive through `EnrichResult.llm_error` or as
    empty resolved_* lists.

    `classify_max_chars` bounds what the classifier sees. The default is
    deliberately well above what a typical contract or receipt reaches;
    deployments with larger-context models can lift it further via the
    bot setting. Truncation, when it happens, is logged loudly. A silent
    3000-char cap used to live inside Classifier.classify and lost the
    tail of every long document.

    `image_data` + `image_mime` are forwarded to the classifier and used
    only when the model has vision capability (probed lazily, cached on
    disk). The caller decides whether to supply them — typically yes for
    image uploads (PNG/JPG), no for PDFs and text files. Vision is
    additive: the OCR-text prompt is unchanged, the image rides
    alongside as supplementary context.
    """
    ocr_text = (doc.get("content") or "").strip()
    if not ocr_text:
        return EnrichResult()

    if len(ocr_text) > classify_max_chars:
        logger.warning(
            "[pipeline] doc #{} ocr_text truncated for classify: {} > {} chars",
            doc.get("id"), len(ocr_text), classify_max_chars,
        )
        ocr_text = ocr_text[:classify_max_chars]

    tags = await paperless.get_tags()
    doc_types = await paperless.get_doc_types()
    correspondents = await paperless.get_correspondents()

    try:
        classification = await classifier.classify(
            ocr_text=ocr_text, tags=tags,
            doc_types=doc_types, correspondents=correspondents,
            image_data=image_data, image_mime=image_mime,
        )
    except LLMUnavailableError as e:
        return EnrichResult(llm_error=("unavailable", str(e)))
    except LLMModelNotFoundError as e:
        return EnrichResult(llm_error=("model_missing", str(e)))
    except LLMTimeoutError as e:
        return EnrichResult(llm_error=("timeout", str(e)))

    if not classification:
        return EnrichResult()

    result = EnrichResult(classification=classification)
    updates: dict[str, Any] = {}

    # Title — capped at Paperless's 128-char column length.
    title = classification.get("title")
    if title and isinstance(title, str):
        updates["title"] = title[:MAX_TITLE_LENGTH]

    # Fresh-filed semantics: the classification result IS the full state
    # of the doc after this call. We don't merge with whatever was there
    # before — that would accumulate tags on every re-run (the "Haushalt
    # sticks around after reprocess" bug). The bot's new-upload path is
    # unaffected: a just-filed doc has no prior tags or type, so starting
    # from a blank slate is a no-op.
    tag_ids: list[int] = []

    # Topics — open set. matching.py splits into existing vs new; new tags
    # are created in Paperless and treated as resolved.
    category_tags = {t: tid for t, tid in tags.items() if not t.startswith("Person: ")}
    topics_raw = classification.get("topics") or classification.get("topic")
    matched_topics, new_topics = match_topics(topics_raw, category_tags)
    for mt in matched_topics:
        tag_ids.append(tags[mt])
        result.resolved_topics.append(mt)
    for nt in new_topics:
        new_id = await paperless.create_tag(nt, _NEW_TOPIC_COLOR)
        if new_id:
            tag_ids.append(new_id)
            result.resolved_topics.append(nt)
            result.created_new.append(f'tag "{nt}"')

    # Persons — closed set seeded from users.toml. match_persons handles
    # full names, lists, "Person: X" prefixes, and returns the prefixed
    # tag name; we strip the prefix for the resolved-name list callers
    # want to render.
    persons_raw = classification.get("persons") or classification.get("person")
    for pt in match_persons(persons_raw, tags):
        tag_ids.append(tags[pt])
        result.resolved_persons.append(pt.replace("Person: ", ""))

    # Always write the tag set — even an empty list. A reprocess that
    # yields no topics/persons should leave the doc with no tags, matching
    # the state a fresh upload would produce from the same LLM output.
    updates["tags"] = list(set(tag_ids))

    # Document type — LLM-decided, no manual-type preservation. "Fresh
    # reprocess" means the LLM's pick wins; a user who curated a type
    # manually before should reprocess knowing they're asking for the
    # AI's verdict.
    doc_type = classification.get("document_type")
    if not _is_empty(doc_type):
        matched = fuzzy_match_entity(doc_type, doc_types)
        if matched:
            updates["document_type"] = doc_types[matched]
            result.resolved_type = matched
        else:
            new_id = await paperless.create_doc_type(doc_type)
            if new_id:
                updates["document_type"] = new_id
                result.resolved_type = doc_type
                result.created_new.append(f'document type "{doc_type}"')

    # Correspondent — always overwrite. Paperless's auto-classifier guesses
    # wrong with few samples; the LLM has read the actual text.
    correspondent = classification.get("correspondent")
    if not _is_empty(correspondent):
        matched = fuzzy_match_entity(correspondent, correspondents)
        if matched:
            updates["correspondent"] = correspondents[matched]
            result.resolved_correspondent = matched
        else:
            new_id = await paperless.create_correspondent(correspondent)
            if new_id:
                updates["correspondent"] = new_id
                result.resolved_correspondent = correspondent
                result.created_new.append(f'correspondent "{correspondent}"')

    date = classification.get("date")
    if date and isinstance(date, str) and _ISO_DATE.match(date):
        updates["created"] = date

    if updates:
        await paperless.update_doc(doc["id"], updates)
        result.updates_applied = updates

    # Summary note — written after entities are patched so any newly
    # created correspondent / tags are reflected in the rendered body.
    # Failure here doesn't poison the return: a doc without its summary
    # is still a correctly-classified doc, and the next reclassify will
    # get another chance.
    summary_text = _format_classifier_summary(
        classification,
        resolved_persons=result.resolved_persons,
        resolved_correspondent=result.resolved_correspondent,
    )
    if summary_text:
        await _replace_classifier_summary(paperless, doc["id"], summary_text)
        result.summary = summary_text

    return result


_REFORMAT_MIN_CHARS = 20


async def reformat_document(
    *,
    paperless: PaperlessAPI,
    classifier: Classifier,
    doc_id: int,
    ocr_text: str,
) -> str | None:
    """Ask the LLM to rewrite OCR text into clean Markdown, PATCH Paperless.

    Returns the new text on success, None on any failure (LLM down, too
    short to be a usable body, Paperless PATCH rejected). Non-critical —
    the caller's fallback is to leave the original OCR in place.

    The minimum-length guard catches the model returning a single token
    or a stray " ok" when it misinterprets the prompt. Better to keep
    the OCR text than replace it with garbage.
    """
    formatted = await classifier.reformat(ocr_text)
    if not formatted or len(formatted) <= _REFORMAT_MIN_CHARS:
        return None
    ok = await paperless.update_doc(doc_id, {"content": formatted})
    return formatted if ok else None
