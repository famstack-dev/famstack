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
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import aiohttp
from loguru import logger
from openai import AsyncOpenAI

from matching import (
    MAX_TITLE_LENGTH,
    _is_empty,
    fuzzy_match_entity,
    match_persons,
    match_topics,
    submitter_person_tag,
)
# Re-export the framework's LLM surface so existing
# `from pipeline import LLMUnavailableError, ImageAttachment, ...` callers
# (capture_pipeline, document_pipeline, tests) keep working unchanged.
from stack.ai.client import (
    LLM,
    LLMError,  # noqa: F401  (re-exported)
    LLMImage,
    LLMModelNotFoundError,
    LLMTimeoutError,
    LLMUnavailableError,
    ModelCapabilities,
)

# Back-compat alias — the docs pipeline shipped with `ImageAttachment`
# before the framework introduced `LLMImage`. The fields are identical
# so callers stay duck-typed.
ImageAttachment = LLMImage

if TYPE_CHECKING:
    from stack.ontology import Ontology


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


# Cap for the body window the classifier and the reformat pass operate
# on. Defined here near the other module constants so it's visible to
# ``Classifier`` methods that take it as a default parameter. The
# enrichment functions further down the file consume the same value.
DEFAULT_CLASSIFY_MAX_CHARS = 20000


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


# ── Whoosh query translation ─────────────────────────────────────────────

# Whoosh special-character set (https://whoosh.readthedocs.io/). When any
# of these appears in a token we assume the caller is writing Whoosh
# syntax on purpose (wildcards, fielded search, fuzzy match, grouping,
# quoted phrases) and leave the token alone.
_WHOOSH_SPECIAL = set("*?:()\"~")


def _to_whoosh_query(q: str) -> str:
    """Translate a chat search input into a Whoosh query string.

    The visible problem this solves: a user types `pangasius` expecting
    to find the doc titled "Pangasiusfilet". Whoosh tokenises titles
    on whitespace and lowercases them, so the index has the token
    `pangasiusfilet` -- a bare-term query for `pangasius` is not a
    full token and German compound-splitting only kicks in for words
    in the analyzer's dictionary (it splits "fisch" out of
    "fischrezept" but not "pangasius" out of "pangasiusfilet"). The
    fix is to wildcard each user-supplied token so prefix matching
    catches "pangasius*" against "pangasiusfilet".

    Each whitespace-separated token gets a `*` appended unless it
    already contains a Whoosh special character (the caller is using
    intentional syntax) or is itself a Whoosh operator (AND/OR/NOT).
    Tokens are joined back with spaces, which Whoosh treats as AND
    under Paperless's default operator -- so multi-word queries narrow
    the set rather than blowing it up.

    Empty input passes through unchanged so a defensive caller doesn't
    accidentally search for `*` (which would match every doc).
    """
    if not q.strip():
        return q
    tokens: list[str] = []
    for tok in q.split():
        if tok.upper() in {"AND", "OR", "NOT"}:
            tokens.append(tok)
            continue
        if any(c in _WHOOSH_SPECIAL for c in tok):
            tokens.append(tok)
            continue
        tokens.append(f"{tok}*")
    return " ".join(tokens)


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
            params={
                "query": _to_whoosh_query(query),
                "page_size": limit,
                "ordering": "-created",
            },
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
    """Domain wrapper over `stack.ai.client.LLM` for the docs pipeline.

    Holds the bot-specific prompts (classify, capture, reformat, rewrite,
    synthesize) and delegates all HTTP, error translation, and vision
    probing to the framework LLM. Construct via :py:meth:`from_endpoint`
    in production; pass any LLM-shaped object directly when stubbing in
    tests.

    classify() and classify_capture() raise the framework LLM*Errors so
    the caller can distinguish 'the LLM is down' from 'the LLM returned
    nothing'. reformat(), rewrite_query() and synthesize_answer() are
    best-effort: they swallow the same errors and degrade to a safe empty
    fallback so document enrichment never blocks on AI hiccups.
    """

    def __init__(self, llm: LLM):
        self._llm = llm

    @classmethod
    def from_endpoint(cls, url: str, key: str = "", *,
                      bot_name: str = "archivist-bot",
                      capabilities: ModelCapabilities | None = None) -> "Classifier":
        """Build a Classifier from a base URL + key, like the bot runtime sees.

        The OpenAI SDK appends `/chat/completions` itself, so the caller
        may pass either the `/v1` root (what `stack.toml` stores) or the
        full endpoint — both shapes are tolerated.

        Refuses an empty URL: the SDK would otherwise default to
        api.openai.com, which on a privacy-first family server is the
        wrong destination to ever reach by accident.
        """
        clean = url.rstrip("/")
        if clean.endswith("/chat/completions"):
            clean = clean[: -len("/chat/completions")]
        if not clean:
            raise LLMUnavailableError(
                "No AI endpoint configured — set up AI with 'stack up ai'"
            )
        client = AsyncOpenAI(
            base_url=clean,
            api_key=key or "not-needed",
            max_retries=1,
        )
        llm = LLM(client, namespace=bot_name, capabilities=capabilities)
        return cls(llm)

    @property
    def llm(self) -> LLM:
        """Expose the framework LLM for other components that need it.

        Captures piggyback on this for transcript cleanup -- the LLM is
        the same one the classifier already built, so we don't open a
        second HTTP client just to polish a voice memo.
        """
        return self._llm

    @property
    def capabilities(self) -> ModelCapabilities:
        """Expose the vision-capability cache for tests + diagnostics."""
        return self._llm.capabilities

    async def has_vision(self) -> bool:
        """Does the classifier's model accept image inputs? See :py:meth:`LLM.has_vision`."""
        return await self._llm.has_vision(role="classifier")

    async def aclose(self) -> None:
        """Close the underlying HTTP client — paired with :py:meth:`from_endpoint`."""
        await self._llm.aclose()

    async def classify(
        self, *,
        ocr_text: str,
        tags: dict,
        doc_types: dict,
        correspondents: dict,
        images: list[ImageAttachment] | None = None,
        ontology_section: str = "",
        correspondents_section: str = "",
        persons_section: str = "",
        date_filed: str | None = None,
        user_hint: str | None = None,
        initial_classification: dict | None = None,
    ) -> dict:
        """Ask the LLM to classify a document based on its OCR text.

        When `images` is supplied AND the model has vision capability
        (cached probe), each (bytes, mime) becomes its own image_url
        part in the multimodal message. The text prompt is unchanged —
        images are supplementary context, not a replacement for OCR.
        Belt-and-braces: vision catches layout and logos, OCR catches
        small print and numbers.

        Multi-image is per-page for scanned PDFs and one-image for
        photo uploads — the caller decides what counts as a "page".
        Each image rides at native resolution; we don't pre-stack so
        the model can downscale each independently.

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
            ontology_section=ontology_section,
            correspondents_section=correspondents_section,
            persons_section=persons_section,
            date_filed=date_filed,
            user_hint=user_hint,
            initial_classification=initial_classification,
        )

        valid_images = [
            img for img in (images or [])
            if img.data and img.mime and img.mime.startswith("image/")
        ]
        attach: list | None = None
        if valid_images and await self.has_vision():
            attach = valid_images
            total = sum(len(img.data) for img in valid_images)
            logger.info(
                "[pipeline] classify: attaching {} image(s), {} bytes total",
                len(valid_images), total,
            )

        response = await self._llm.complete(
            "classifier", prompt, images=attach, json_mode=True,
        )
        if not response:
            return {}
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            logger.warning("[pipeline] LLM returned invalid JSON: {}", response[:200])
            return {}

    async def classify_capture(
        self, *,
        text: str,
        person_names: list[str],
        existing_tags: list[str] | None = None,
        images: list[ImageAttachment] | None = None,
        user_hint: str | None = None,
        initial_classification: dict | None = None,
    ) -> dict:
        """Capture-specific classification.

        Returns a smaller payload than `classify`: title, summary,
        facts, tags, persons. No correspondent, no document_type, no
        action_items, no ontology coupling. The summary is the
        load-bearing artifact — captures are bookmarks/notes, not
        archives, and the user reads the summary later to remember
        what this was about. Action items deliberately stay out:
        we don't want every Reddit paste manufacturing a todo.

        Existing tags are fed as a vocabulary hint so the LLM reuses
        what's already in the system ("LLMs" not "llm", "Apple
        Silicon" not "M-series chips"). New tags are still allowed
        when nothing existing fits.

        ``images`` lets binary captures (PDFs, photos) ride the same
        prompt path as text captures -- the prompt asks the LLM to
        summarize what the page(s) show. When images are present but
        the model lacks vision, they are silently dropped and the
        caller's ``text`` becomes the sole signal.
        """
        prompt = _build_capture_prompt(
            text=text,
            person_names=person_names,
            existing_tags=existing_tags or [],
            user_hint=user_hint,
            initial_classification=initial_classification,
        )
        valid_images = [
            img for img in (images or [])
            if img.data and img.mime and img.mime.startswith("image/")
        ]
        attach: list | None = None
        if valid_images and await self.has_vision():
            attach = valid_images
            total = sum(len(img.data) for img in valid_images)
            logger.info(
                "[pipeline] capture: attaching {} image(s), {} bytes total",
                len(valid_images), total,
            )
        response = await self._llm.complete(
            "classifier", prompt, images=attach, json_mode=True,
        )
        if not response:
            return {}
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            logger.warning(
                "[pipeline] capture: LLM returned invalid JSON: {}",
                response[:200],
            )
            return {}

    async def reformat(
        self, ocr_text: str, *, max_chars: int = DEFAULT_CLASSIFY_MAX_CHARS,
    ) -> str | None:
        """Reformat raw OCR text into clean, readable Markdown.

        OCR output is often messy: broken lines, garbled characters, no
        structure. The LLM fixes artifacts while preserving all factual
        content. The reformatted text replaces the original in Paperless,
        making documents actually readable.

        ``max_chars`` caps the prompt input so the LLM call stays bounded
        on extremely long documents. Default aligns with the classify
        cap (``DEFAULT_CLASSIFY_MAX_CHARS``); callers that have already
        truncated their input can pass through their own cap to keep
        the pipeline single-sourced.

        Non-critical — transport failures return None and the caller's
        fallback is to keep the raw OCR. Length-based usability filtering
        is the pipeline's job (see `reformat_document`); this returns
        whatever the LLM produced, trimmed.
        """
        prompt = _build_reformat_prompt(ocr_text[:max_chars])
        try:
            result = await self._llm.complete("reformat", prompt)
        except (LLMUnavailableError, LLMModelNotFoundError, LLMTimeoutError):
            return None
        return result.strip() if result else None

    async def rewrite_query(
        self, question: str, ontology_section: str, lang: str = "en",
    ) -> list[str]:
        """Extract search keywords from a natural-language question.

        Used by the archivist when a message ends with `?`. The LLM
        reads the family's topic + doctype ontology and produces 2-4
        keywords that would literally appear in a matching document --
        translation and synonym expansion happen here, so the regex
        walker downstream stays dumb.

        Best-effort: any LLM transport failure or parse error returns
        an empty list, which the caller treats as "no rewrite, search
        the question verbatim." Synonym selection is the LLM's job;
        we don't second-guess it here, but we do strip empties and
        cap the list length so a chatty model can't blow the regex up.
        """
        prompt = _build_rewrite_prompt(question, ontology_section, lang)
        try:
            raw = await self._llm.complete("recall", prompt, json_mode=True)
        except (LLMUnavailableError, LLMModelNotFoundError, LLMTimeoutError) as e:
            logger.warning("[recall] LLM unavailable for rewrite: {}", e)
            return []
        keywords = _parse_rewrite_response(raw)
        if not keywords:
            # Surface the raw payload so an empty keyword list is
            # debuggable: an off-shape JSON response is a prompt or
            # model issue, not a transport one, and we can't fix it
            # blind.
            logger.warning(
                "[recall] rewrite parse produced no keywords; raw={!r}",
                (raw or "")[:200],
            )
        return keywords

    async def synthesize_answer(
        self,
        question: str,
        evidence: list[dict],
        lang: str = "en",
        *,
        today: str | None = None,
    ) -> str:
        """Compose a natural-language answer to a question from indexed hits.

        Used by the archivist after question-mode search returns its
        results. `evidence` is the list of hits the bot wants to feed
        the LLM as context -- each dict should carry as many of these
        keys as are available:

          kind       "Memory" or "Paperless"
          title      doc title
          date       "YYYY-MM-DD" or ""
          persons    list of person names
          summary    the LLM-written summary block (callout or note)

        `today` is the calendar date the prompt embeds so the model
        can resolve relative-time phrases ("the last invoice", "this
        month", "in the past two weeks"). Defaults to today's UTC
        date when not passed; callers running in a different timezone
        can override with their local "today". The bot already runs
        with `TZ=Europe/Berlin` in the container, so the default
        matches the family's wall clock for a German install.

        The model is prompted to answer using *only* the evidence and
        to cite hits by [N]; when the summaries are not enough it
        says "I need to read [N], [M] in detail" rather than guessing.
        That deferral matters: the bot then surfaces those hits so
        the human can read them directly, instead of fabricating an
        answer from thin context. Returns "" on any LLM transport
        failure -- callers fall back to "no synthesis, just the
        evidence list."
        """
        if not evidence:
            return ""
        if today is None:
            from datetime import date
            today = date.today().isoformat()
        prompt = _build_synthesize_prompt(question, evidence, lang, today=today)
        try:
            raw = await self._llm.complete("recall", prompt, json_mode=False)
        except (LLMUnavailableError, LLMModelNotFoundError, LLMTimeoutError) as e:
            logger.warning("[recall] LLM unavailable for synthesis: {}", e)
            return ""
        return (raw or "").strip()


# ── Prompts ──────────────────────────────────────────────────────────────
#
# Extracted into module-level builders so the bot and CLI can print them
# for debugging (`--dry-run --show-prompt` may land in v1.1) without
# depending on classifier internals.

def _initial_classification_block(initial: dict | None) -> str:
    """Render the latest classification as a delta-anchor.

    Reprocess uses this so each correction pass starts from the same
    picture the user SAW when they typed their correction (the
    immediate parent envelope's payload) instead of re-deriving
    everything from the OCR. The human's note is a delta against
    THIS state -- preserve fields the note doesn't address, change
    the ones it does. Empty when no prior state is known
    (a fresh classification with no chain).
    """
    if not isinstance(initial, dict) or not initial:
        return ""
    parts: list[str] = []
    for key in ("title", "topics", "persons", "correspondent",
                "document_type", "tags"):
        if key not in initial:
            continue
        value = initial.get(key)
        if value in (None, "", [], {}):
            continue
        parts.append(f"  {key}: {value!r}")
    if not parts:
        return ""
    return (
        "\n\nCurrent classification — what the human saw on screen "
        "when they wrote the correction below. Treat the human note "
        "as a DELTA on this state: preserve every field the note "
        "does NOT explicitly address, change only the fields a "
        "correction touches. If the note says 'Arbeit war richtig', "
        "the topic stays Arbeit even if the OCR could plausibly "
        "suggest something else:\n" + "\n".join(parts)
    )


def _user_hint_block(user_hint: str | None) -> str:
    """Render the user's note as a high-signal prompt block.

    Returned empty when the hint is missing or blank so well-formed
    initial classifications without a caption produce the same prompt
    as before. One source for every channel: Element X attachment
    captions, scan-session opener/closer text, reply-to-correct on a
    prior filing. The classifier treats them all the same -- they are
    what the human said about the document. The hint is wrapped in
    unambiguous fences so a chatty user message can't be mistaken
    for the document text below.
    """
    if not user_hint or not user_hint.strip():
        return ""
    hint = user_hint.strip()
    return (
        "\n\nUser context — what the human said when filing this document. "
        "Treat as supplementary evidence about the document's purpose, "
        "audience, and how it should be tagged, NOT as a substitute for "
        "what the document itself shows. Specifically:\n"
        "  - Weave the human's framing into the SUMMARY so the document's "
        "content and their note fit together naturally (paraphrase in the "
        "document's language; never quote the note verbatim).\n"
        "  - Use it to disambiguate `persons`, `correspondent`, or the "
        "title's wording when the document text alone is ambiguous (an "
        "un-named receipt becomes attributable when the note says who it's "
        "for; a generic invoice gets a more specific title).\n"
        "  - When the note names a subject area or category "
        "('Krankenversicherung', 'Auto', 'Schule', 'Steuer', ...) treat it "
        "as a topic hint -- prefer existing canonicals from the topic list "
        "when there's a fit, and let it tip the choice when the document "
        "could plausibly belong to more than one topic.\n"
        "  - Do NOT invent facts the document contradicts. If the document "
        "plainly shows one sender, don't flip to a different sender just "
        "because the note mentions another.\n"
        f"---\n{hint}\n---"
    )


def _build_classify_prompt(*, ocr_text: str, person_names: list[str],
                           category_tags: list[str],
                           doc_types: list[str],
                           correspondents: list[str],
                           ontology_section: str = "",
                           correspondents_section: str = "",
                           persons_section: str = "",
                           date_filed: str | None = None,
                           user_hint: str | None = None,
                           initial_classification: dict | None = None) -> str:
    """The classification prompt.

    Simplified to three clear axes:
      topic         = what is this about?   "Insurance", "Shopping"
      person        = which family member?  "Homer", "Bart", or null
      correspondent = who sent it?          "Duff Insurance", "Kwik-E-Mart"

    Worked examples — a Kwik-E-Mart receipt for Homer:
      topic="Shopping", person="Homer", correspondent="Kwik-E-Mart"
    A school letter about Bart:
      topics=["School"], person="Bart", correspondent="Springfield Elementary"
    A health insurance invoice for Homer:
      topics=["Insurance", "Medical"], person="Homer", correspondent="Springfield Mutual"

    Person names arrive stripped of the "Person: " prefix so the LLM sees
    clean first names like "Homer" rather than "Person: Homer".

    Topic and doctype context comes from the memory stacklet's ontology
    when `ontology_section` is supplied — a richer block that lists
    canonical names with their synonyms, so the LLM can pick a single
    canonical id even when the OCR text uses an alternative phrasing
    ("policy" → Insurance, "Police" → Versicherung). Without it (e.g.
    when memory isn't reachable on first classify), we fall back to the
    flat lists pulled from Paperless.

    Correspondent context comes from the memory wiki when
    `correspondents_section` is supplied — canonical names with their
    learned aliases inline ("Duff Insurance (Duff Insurance Ortsverband Springfield)"). This
    teaches the LLM what to canonicalize before Paperless ever sees a
    duplicate. Without it, we fall back to the flat Paperless list,
    which has no alias signal.

    Person context follows the same pattern: when `persons_section` is
    supplied, canonical first names ride alongside their curated
    synonyms ("Marge (Marjorie, Margaret Bouvier)") so a document that
    refers to a member by their formal name or maiden name still
    resolves to the canonical roster entry. Without it, the prompt
    falls back to the bare list of Paperless Person tags.
    """
    if ontology_section:
        vocabulary_block = ontology_section
    else:
        vocabulary_block = (
            f"Existing topic tags: {json.dumps(category_tags, ensure_ascii=False)}\n"
            f"Existing document types: {json.dumps(doc_types, ensure_ascii=False)}"
        )

    if correspondents_section:
        correspondents_block = correspondents_section
    else:
        correspondents_block = (
            f"Existing correspondents: {json.dumps(correspondents, ensure_ascii=False)}"
        )

    if persons_section:
        persons_block = persons_section
    else:
        persons_block = (
            f"Family members: {json.dumps(person_names, ensure_ascii=False)}"
        )

    # `date_filed` is the document's filing date — for initial Matrix
    # uploads, the message's server timestamp; for the reprocess CLI,
    # Paperless's immutable `added` field. The LLM uses it to resolve
    # partial dates on the document. System date is only a fallback
    # when neither is available — using it for reprocess would silently
    # shift the anchor to "today" and re-hallucinate dates.
    if date_filed is None:
        import datetime as _dt
        date_filed = _dt.date.today().isoformat()

    return f"""Classify this document. Return ONLY a JSON object.

Date filed: {date_filed}{_initial_classification_block(initial_classification)}{_user_hint_block(user_hint)}

IMPORTANT: Always prefer existing values from the lists below. Only suggest
a new value when NOTHING in the list is a reasonable match.

{persons_block}
{vocabulary_block}
{correspondents_block}

Return this exact JSON structure:
{{
  "title": "short identifying title — 3 to 6 words, max ~50 chars. What this document IS, not how much or when. Amounts live in facts, the date lives in `date`, the sender lives in `correspondent`. Include the year ONLY when it disambiguates an annually-recurring document ('Kfz-Versicherung 2026'). Include the sender's name only when it's part of the natural identifier ('Bergchalet Refugium Martius', 'Anthropic Max Plan'). NEVER include amounts, full dates, invoice numbers, or addresses. This title becomes the Paperless title AND the filename slug, so keep it stable across reprocessing. Document's language. Examples: 'Bergchalet Refugium Martius', 'Anthropic Max Plan', 'Kfz-Versicherung 2026', 'Kwik-E-Mart Kassenbon'.",
  "date": "YYYY-MM-DD or null — the document's own date (issue / booking / invoice date), not the date you read it. Apply the date-resolution rule below.",
  "topics": ["what is this document about? One or two subject areas. E.g. ['Insurance'], ['Insurance', 'Vehicle'], ['Shopping']. A health insurance bill is ['Insurance', 'Medical']. A car repair invoice is ['Vehicle']. Pick the canonical name from existing topic tags. Usually one topic, two only when the document genuinely spans two areas."],
  "persons": ["which NAMED family members does this belong to? Pick from the family members list by first name. Names MUST appear in the document text (or be inferable from a labeled field like 'Customer: John Doe', 'Versicherter: Homer Simpson'). EXCEPTION: when the human note above explicitly names or attributes the document to a household member ('Marges Rechnung', 'für Bart', 'this is Lisa's'), include that member here even if their name does not appear in the OCR text -- the human's explicit attribution is authoritative for this field. When neither the document nor the human note names anyone (a booking confirmation that says '2 Erwachsene, 2 Kinder', 'die Familie' WITHOUT naming individuals, a receipt with no named customer and no human note), return an empty list -- the system has a separate fallback to attribute the doc to whoever uploaded it. Can be multiple for joint documents where everyone is named (a marriage certificate listing both spouses)."],
  "document_type": "optional: the document's format. Pick the canonical name from existing document types. null if unclear.",
  "correspondent": "the SENDER's CANONICAL short name. If the printed sender matches one of the Existing correspondents (or one of its aliases in parens), return the canonical exactly. Otherwise return the cleanest short form — strip regional, branch, and legal-form suffixes. 'Duff Insurance Ortsverband Springfield' → 'Duff Insurance'. 'Burns Industries LLC' → 'Burns Industries'. 'Springfield Nuclear Power Plant Division 7' → 'Springfield Nuclear'. null is better than guessing from fragments.",
  "correspondent_aliases": ["full names as printed on THIS document, useful for growing the wiki. Include only when the printed name differs from the canonical you returned above. Empty list when the printed name matches the canonical exactly."],
  "correspondent_facts": ["STABLE facts about the SENDER organization that are useful on every future document from them: address, phone, email, website, IBAN, your customer/membership/policy number with them. NOT facts about THIS document (totals, invoice numbers, dates — those go in facts). Empty list if none visible."],
  "summary": "2-3 sentence summary in the document's language. Lead with the document type (Invoice/Receipt/Letter/Certificate or Rechnung/Quittung/Brief/Bescheinigung — whichever matches) and the correspondent; include the document's date and any total amount; name the person(s) involved (use the human note when the document doesn't name them). Examples — match the document's language, not these literal strings: EN 'Invoice from Duff Insurance for EUR 340/year covering Homer Simpson's car insurance, effective 2026-04-01.' / EN 'Receipt from Kwik-E-Mart dated 2026-05-12 for EUR 7.42 (cash purchase).' / DE 'Rechnung der Duff Insurance über EUR 340/Jahr für die Kfz-Versicherung von Homer Simpson, gültig ab 01.04.2026.' / DE 'Quittung von Kwik-E-Mart vom 12.05.2026 über EUR 7,42 (Einkauf, bar bezahlt).' Omit a field only when the document genuinely lacks it; never invent.",
  "facts": ["key structured facts about THIS document, one bullet per fact. Top-level facts: totals, account/invoice/policy numbers, dates, plan/tariff names, deadlines — e.g. 'Total: EUR 90.00', 'Invoice: #12345', 'Plan: Premium'. Line items: when the document lists individual purchases or services, include each one as its own bullet with quantity/unit-price/total — e.g. 'Donuts 5x EUR 5.00', 'Cola 1x EUR 2.42', 'Reparatur Bremsbeläge EUR 240.00'. Don't fabricate line items the document doesn't print; a one-line receipt has no line items, only a total."],
  "action_items": [{{"action": "what needs to happen", "due": "YYYY-MM-DD or null"}}]
}}

Rules:
- VISION VS OCR: when an image of the document is attached AND the OCR text below conflicts with what you see in the image, prefer the image. The OCR pass may pick up template footers, hidden text layers, watermarks, or unrelated PDF metadata that don't reflect the document's actual content — dates, proper nouns, and numeric fields are the usual victims. The image is the source of truth; OCR is supplementary context, not a higher authority.
- DATE: this applies to the top-level `date` field AND to any date you put in `facts` / `action_items`.
  - When the document shows a full date (year included), use it verbatim.
  - When only a partial date is visible (no year), pick the year closest in time to `Date filed` — past for backward-looking documents (invoices, receipts, statements, letters confirming past events), future for forward-looking documents (booking confirmations, reservations, appointments, event tickets). A chalet booking confirmation filed in December 2025 mentioning "14 FEBRUAR" means 2026-02-14 (next February), not 2025-02-14 (last February). An invoice filed in December 2025 mentioning "14 FEBRUAR" means 2025-02-14 (this year's February).
  - Never invent a year that isn't visible and isn't derivable from `Date filed`. When even the month is unclear, return null (or omit the date from a fact).
  - Do NOT pull dates from sample texts, legal disclaimers, copyright footers, or unrelated logos.
- LANGUAGE: use the document's original language for title, summary, facts, and action_items. A German document gets a German title and German facts. Never translate.
- topics: the subject area(s), not the document format. An invoice from a shop is ["Shopping"], not ["Invoice"]. An invoice for insurance is ["Insurance"]. A health insurance claim is ["Insurance", "Medical"]. When the document uses a synonym of a listed topic (the list shows synonyms in parentheses), return the canonical name. Use the document's language for new topic tags too. Most documents have one topic; use two only when clearly spanning two areas. EXCEPTION: when the human note block above explicitly assigns a topic ("Arbeit war richtig", "this is health insurance", "tag as Steuer"), that topic IS the right answer for this document regardless of what the OCR text would suggest. The human's intent overrides OCR-derived defaults for this field; pick the canonical that matches their term.
- persons: return names that EXPLICITLY appear in the document text OR are explicitly attributed by the human note block above. Match by first name against the family members list. A marriage certificate naming "Homer Simpson" and "Marge Simpson": ["Homer", "Marge"]. A booking confirmation that says "2 Erwachsene, 2 Kinder (0 und 6 Jahre alt)" with no actual names AND no human note: []. A health insurance bill in Marge's name only: ["Marge"]. A receipt with no printed customer name AND a human note "Marges Tankquittung" or "its marges invoice": ["Marge"] — the human attribution stands in for a missing customer field. NEVER guess based on group counts ("2 Personen" is not "Homer + Marge"), document type ("Kinderarztrechnung" doesn't mean "Bart" or "Lisa" unless the human note says so), or who you think the doc is "probably for". When neither the document nor the human note names anyone, return [] — the system attributes the doc to the uploader as a fallback.
- correspondent: always the SENDER, never the addressee/customer/recipient. When the existing list shows aliases in parentheses, those are previous spellings of the same correspondent — use the canonical (the name OUTSIDE the parentheses). Strip regional/branch/legal-form suffixes for new correspondents. Use null if the sender is not clearly identifiable. Do not guess from fragments. EXCEPTION: when the human note block above explicitly names the correspondent ("File it under Leapter GmbH", "this is from Duff Insurance"), use that name as the canonical -- the human knows the institution better than the printed letterhead. Add any additional sender forms the human mentions ("Leapter GmbH" alongside "Leapter") to `correspondent_aliases` so the wiki grows the alias set.
- correspondent_aliases: only when the printed sender name on THIS document differs from your canonical answer. Single-element list is fine.
- correspondent_facts: stable across documents from the same sender. Address and customer numbers belong here; this month's total does not.
- facts: concrete numbers, dates, account numbers, amounts that describe THIS document. When the document itemises purchases or services, each line item is its own fact bullet alongside the top-level totals. Empty list if none.
- action_items: deadlines, payments due, forms to return. Empty list if none.

Document text:
---
{ocr_text}
---"""


def _build_capture_prompt(
    *,
    text: str,
    person_names: list[str],
    existing_tags: list[str] | None = None,
    user_hint: str | None = None,
    initial_classification: dict | None = None,
) -> str:
    """The capture prompt — smaller and focused on summary + tags.

    Captures are bookmarks (URL pointers with a digest) and notes
    (pasted text with a digest). Unlike documents, they don't carry a
    sender, a document type, or a place in the Paperless taxonomy.
    The user's question at retrieval time is "what was this about?"
    The summary answers it; tags index it.

    `existing_tags` is the vocabulary the system already uses for
    captures. Feeding it in biases the LLM toward consistency — the
    second time someone saves something about LLMs, it lands under
    the same tag as the first. New tags are still allowed when
    nothing existing fits; the dream-cycle wiki rebuild canonicalizes
    synonyms across the whole corpus later.
    """
    existing_tags = existing_tags or []
    tags_hint = (
        f"Existing tags in use: {json.dumps(existing_tags, ensure_ascii=False)}\n"
        "Prefer these when they fit. Only invent new tags when nothing existing matches.\n"
        if existing_tags
        else
        "No existing tags yet — invent useful single-word or two-word topic tags.\n"
    )

    return f"""Summarize and tag this content for a personal knowledge vault.
Return ONLY a JSON object.

The user is bookmarking or noting this content to find it later. Your
job: produce a digest they can scan in 10 seconds and tags that
position this content among their interests.{_initial_classification_block(initial_classification)}{_user_hint_block(user_hint)}

Family members: {json.dumps(person_names, ensure_ascii=False)}
{tags_hint}
Return this exact JSON structure:
{{
  "title": "scannable title under 80 chars. Use the content's language. Capture what this is *about*, not just the source name.",
  "summary": "Markdown summary. Length scales with input — short paste (under ~300 chars): 1-2 sentences. Long content (articles, threads, posts): 200-400 words covering key points, claims, named entities, and conclusions. The user reads this instead of reopening the source.",
  "facts": ["Concrete facts extracted from the content. Each fact MUST anchor on a number, date, named entity, or proper noun — a sentence without one of those is filler and belongs in the summary instead. Count scales with content: 0 for a short note with nothing to extract, 1-3 for a homepage bookmark, 4-8 for a typical article, more for data-heavy content. Don't pad, don't cap."],
  "tags": ["3-5 content-specific tags. Format: lowercase, hyphen-separated (kebab-case). DERIVE tags from concrete nouns, named activities, named items, places, and seasons that appear in the content. PREFER SPECIFIC over generic: 'camping' beats 'travel', 'wäschesack' beats 'haushalt', 'lasagna-recipe' beats 'food', 'local-llms' beats 'ai'. German content → German tags ('campingurlaub', 'bremsen', 'kindergarten'). MINIMUM 3 entries — if a short note has only one obvious specific (e.g. 'camping'), add adjacent ones (the activity, the gear named, the season, the place). Existing-tag reuse: only when an existing tag is content-specific itself; ignore generic categories from the list."],
  "persons": ["which family members this is for or about. Pick from the family members list. Empty list if unclear — the caller will default to the sender."]
}}

Rules:
- LANGUAGE: use the content's original language for title, summary, facts. German content → German output.
- summary: write a real digest, not a teaser. Match length to input — terse for short pastes, fuller for long-form. Do NOT include the source URL; it's surfaced separately in the vault entry.
- facts: each fact carries an anchor (number, date, named entity, proper noun). "X is widely used" is not a fact; "X is used by 600K+ agents" is. Don't pad to hit a count; an empty list beats invented facts.
- tags: 3-5 entries, no exceptions. Each tag must be content-specific: 'camping' not 'travel', 'wäschesack' not 'haushalt', 'bremsen' not 'auto'. The retrieval test for a good tag: would the user, six months from now, type this word to search for this specific content? If no, replace it with a more specific one. Lowercase, hyphen-separated, 1-3 words. Match the content's language.
- persons: only if the content explicitly names a family member. Don't guess from sender.

Content:
---
{text}
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


# ── Query rewrite ────────────────────────────────────────────────────────
#
# The recall-mode entry point. When a family member asks a question
# (anything ending in `?`), the archivist asks the LLM to extract 2-4
# keywords that would literally appear in a matching document. The
# regex walker then OR-alternates them into a single search pattern.
# Two wins: (1) "When did Bart get vaccinated?" becomes a search for
# Impfung/MMR/Auffrischung, which actually hits the German vaccination
# record; (2) the ontology block primes synonym + translation knowledge
# without us having to ship a thesaurus.

def _build_rewrite_prompt(
    question: str, ontology_section: str, lang: str,
) -> str:
    """Recall-mode prompt: question → JSON list of search keywords."""
    return f"""You extract search keywords from a question, so a regex walker can look up family documents.

The family classifies their documents under these topics and forms:

{ontology_section}

Language hint: {lang}.
- If the question is in German, prefer German keywords.
- If it is in English, prefer English keywords.
- For an ambiguous or generic question, include the most likely topic name plus one or two synonyms in the document language.

Question: {question}

Reply with a JSON object: {{"keywords": ["word1", "word2", "word3"]}}.
2 to 4 keywords. Each keyword is a literal word that would appear in
the document (a noun, a name, a topic). No phrases, no quotes, no
prose around the JSON. Output ONLY the JSON object."""


def _parse_rewrite_response(raw: str) -> list[str]:
    """Pull a keyword list out of the rewrite LLM's response.

    Accepts the requested object form `{"keywords": [...]}` and the
    bare-array fallback that some smaller models produce when they
    forget the wrapper. Empty list on any parse failure -- the caller
    treats empty as "no rewrite, search the question verbatim." Caps
    at 6 entries so a chatty model can't blow up the alternation
    regex; trims whitespace and drops empties so a stray `""` doesn't
    poison the join.
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []

    if isinstance(data, dict):
        v = data.get("keywords")
        candidates = v if isinstance(v, list) else []
    elif isinstance(data, list):
        candidates = data
    else:
        candidates = []

    cleaned: list[str] = []
    for x in candidates:
        if isinstance(x, (str, int, float)):
            s = str(x).strip()
            if s:
                cleaned.append(s)
        if len(cleaned) >= 6:
            break
    return cleaned


# ── Answer synthesis ─────────────────────────────────────────────────────

def _format_evidence_block(evidence: list[dict]) -> str:
    """Render the hit list the synthesis prompt feeds the LLM.

    Each hit becomes a numbered stanza with the fields the model is
    most likely to cite (date for "when", title + persons for "who/
    what"), followed by the summary. The summary is the high-signal
    payload -- the rest is metadata the model can quote when citing.
    A missing field is omitted from its line rather than rendered as
    `(none)` to keep noise down.
    """
    lines: list[str] = []
    for n, hit in enumerate(evidence, start=1):
        meta_bits: list[str] = []
        if hit.get("kind"):
            meta_bits.append(str(hit["kind"]))
        if hit.get("date"):
            meta_bits.append(str(hit["date"]))
        title = (hit.get("title") or "Untitled").strip()
        header = f"[{n}] " + " · ".join(meta_bits + [title]) if meta_bits else f"[{n}] {title}"
        lines.append(header)
        persons = [p for p in (hit.get("persons") or []) if p]
        if persons:
            lines.append(f"    Persons: {', '.join(persons)}")
        summary = (hit.get("summary") or "").strip()
        if summary:
            # Indent the summary so the block is visually one unit
            # when the model echoes pieces of it back.
            summary_indented = "\n".join("    " + ln for ln in summary.splitlines())
            lines.append(summary_indented)
        else:
            lines.append("    (no summary available)")
        lines.append("")  # blank separator between hits
    return "\n".join(lines).rstrip()


def _build_synthesize_prompt(
    question: str, evidence: list[dict], lang: str,
    *,
    today: str,
) -> str:
    """Synthesis prompt: question + evidence summaries → answer with citations.

    The contract this prompt sets up:

      - Use only the evidence; don't invent facts.
      - Cite hits as `[N]` so the reader can verify.
      - If the summaries aren't enough, defer with a "need to read
        [N] in detail" line rather than guessing.
      - Reply in the family's language so a German household gets a
        German answer even when the question routed through an
        English-speaking model intermediary.
      - Today's date is included so relative-time questions ("the
        last invoice", "this month", "since February") can resolve
        without the model guessing what "now" means.
    """
    evidence_block = _format_evidence_block(evidence)
    return f"""You are answering a family member's question using a small set of indexed documents. Answer using ONLY the evidence below -- never invent facts.

Today's date is {today}. Use it to resolve relative-time phrases like "the last", "this month", "in the past N days".

Question: {question}

Evidence (each hit is numbered; cite the ones you used as [N]):

{evidence_block}

Rules:
- Answer concisely. One sentence when the answer is obvious; only longer when the question genuinely needs it.
- Cite every fact with the source bracket(s): "[1]", "[2, 3]".
- If the summaries alone are not enough to answer, reply: "I'd need to read [N] in detail to answer that." (List the most relevant hit numbers.)
- Respond in the family's language: {lang}.
- Do not include any preamble like "Based on the evidence..." -- answer directly.

Answer:"""


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
    """Render the classifier payload as a Paperless note body.

    Sections are conditional and untitled — separated by blank lines
    rather than `## Summary` / `## Facts` / `## Parties` headings.
    The titles forced an English label onto German (or any non-English)
    content; without them the note reads natively in whatever language
    the document is in, and the structure is still obvious: prose,
    then bulleted facts, then the parties line.

    A trailing `<!-- archivist-bot -->` marker tags the note as
    bot-written so the sweep on the next classify can identify and
    delete prior versions without depending on Paperless's `user`
    field. The marker sits at the bottom so the note opens with its
    actual content; it's invisible in rendered Markdown either way.

    Returns None when there is nothing to record — the caller skips
    the write entirely rather than posting an empty stub.
    """
    parts: list[str] = []

    summary = (classification.get("summary") or "").strip()
    if summary:
        parts.append(summary)

    facts = [str(f).strip() for f in (classification.get("facts") or []) if str(f).strip()]
    if facts:
        parts.append("\n".join(f"- {f}" for f in facts))

    parties = _format_parties(
        correspondent=resolved_correspondent,
        persons=resolved_persons,
    )
    if parties:
        parts.append(parties)

    if not parts:
        return None
    return "\n\n".join(parts) + f"\n\n{_BOT_NOTE_MARKER}"


def _format_parties(*, correspondent: str | None, persons: list[str]) -> str:
    """"Sender → recipients" one-liner, with either side omitted if empty."""
    left = correspondent.strip() if correspondent else ""
    right = ", ".join(p for p in persons if p) if persons else ""
    if left and right:
        return f"{left} → {right}"
    return left or right


# HTML comment marker — invisible in Paperless's rendered Markdown,
# trivially detectable in raw text. Every classifier-written note
# carries this so the sweep on the next reprocess can identify and
# remove prior versions without inspecting ownership. The label is
# the bot's id ("archivist-bot", matching `Archivist.name`) so any
# future bot writing Paperless notes uses its own marker rather than
# stomping on this one.
_BOT_NOTE_MARKER = "<!-- archivist-bot -->"

# Legacy section-heading prefixes — fallback for notes written before
# the marker was introduced. Safe to remove once vaults have been
# reprocessed at least once and only marker-tagged notes remain.
_LEGACY_BOT_NOTE_PREFIXES = ("## Summary", "## Facts", "## Parties")


def _looks_like_bot_note(text: str) -> bool:
    """True when a note is one the archivist wrote.

    Primary signal: the `_BOT_NOTE_MARKER` HTML comment appears in the
    note (currently appended as the last line). Doesn't depend on
    Paperless's `user` serialization, doesn't get fooled by reformatted
    content, doesn't accidentally sweep a free-text user note. Plain
    `in` rather than a positional check so the sweep stays correct if
    the marker ever moves back to the top, or if the user (intentionally
    or not) prepends a header line before it.

    Legacy fallback: pre-marker notes still match by their section
    heading shape so accumulated stale summaries from previous deploys
    get swept on the next reprocess.
    """
    if _BOT_NOTE_MARKER in text:
        return True
    return text.lstrip().startswith(_LEGACY_BOT_NOTE_PREFIXES)


async def _replace_classifier_summary(
    paperless: PaperlessAPI, doc_id: int, summary_text: str,
) -> None:
    """Write `summary_text` as the bot's summary, replacing prior ones.

    Bot notes carry an HTML comment marker so the sweep is independent
    of Paperless's user-ownership field (which varies in shape across
    versions and silently let stale notes pile up on every reprocess
    in the previous implementation).
    """
    for note in await paperless.list_notes(doc_id):
        text = note.get("note") or ""
        if _looks_like_bot_note(text) and isinstance(note.get("id"), int):
            await paperless.delete_note(doc_id, note["id"])
    await paperless.add_note(doc_id, summary_text)


def extract_bot_summary(doc: dict) -> str:
    """Pull the archivist-written summary out of a Paperless doc dict.

    `doc` is the JSON object returned by `/api/documents/{id}/` and
    by `/api/documents/?query=...` -- both include a `notes` list.
    The archivist's note carries the `_BOT_NOTE_MARKER` HTML comment
    (or a legacy `## Summary` heading); both are recognised by
    `_looks_like_bot_note`. The marker line is stripped on the way
    out so the synthesis prompt sees pure content.

    Returns "" when no bot note is found -- callers can fall back to
    the OCR `content` field, but the summary is the higher-signal
    starting point.
    """
    for note in doc.get("notes") or []:
        text = note.get("note") or ""
        if not _looks_like_bot_note(text):
            continue
        # Drop the trailing marker (and any blank lines before it) so
        # the caller doesn't have to filter HTML comments out of its
        # prompt context.
        cleaned = text.replace(_BOT_NOTE_MARKER, "").rstrip()
        return cleaned
    return ""


# ── Enrichment ───────────────────────────────────────────────────────────

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NEW_TOPIC_COLOR = "#4caf50"


async def enrich_document(
    *,
    paperless: PaperlessAPI,
    classifier: Classifier,
    doc: dict,
    classify_max_chars: int = DEFAULT_CLASSIFY_MAX_CHARS,
    images: list[ImageAttachment] | None = None,
    ontology_section: str = "",
    correspondents_section: str = "",
    persons_section: str = "",
    ontology: "Ontology | None" = None,
    lang: str = "en",
    is_reprocess: bool = False,
    date_filed: str | None = None,
    user_hint: str | None = None,
    initial_classification: dict | None = None,
    submitter_mxid: str | None = None,
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

    `images` is forwarded to the classifier and used only when the
    model has vision capability (probed lazily, cached on disk). The
    caller decides what to supply — one ImageAttachment for a photo
    upload, N for the rendered pages of a scanned PDF. Vision is
    additive: the OCR-text prompt is unchanged, images ride alongside
    as supplementary context.
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
            images=images,
            ontology_section=ontology_section,
            correspondents_section=correspondents_section,
            persons_section=persons_section,
            date_filed=date_filed,
            user_hint=user_hint,
            initial_classification=initial_classification,
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
    matched_topics, new_topics = match_topics(
        topics_raw, category_tags, ontology=ontology, lang=lang,
    )
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

    # Submitter fallback: when the classifier returned no persons, the
    # prompt rules tell it to refuse guessing — but the document still
    # belongs to whoever uploaded it. Attribute the doc to the
    # submitter's Person tag instead of leaving it un-personed.
    # Live archivist passes `event.sender` here; CLI reprocess and the
    # reply-to-reprocess path don't (we don't know who originally
    # uploaded a doc Paperless has had for weeks, so we don't pretend).
    if not result.resolved_persons and submitter_mxid:
        fallback = submitter_person_tag(submitter_mxid, tags)
        if fallback:
            tag_ids.append(tags[fallback])
            result.resolved_persons.append(fallback.replace("Person: ", ""))

    # Always write the tag set — even an empty list. A reprocess that
    # yields no topics/persons should leave the doc with no tags, matching
    # the state a fresh upload would produce from the same LLM output.
    updates["tags"] = list(set(tag_ids))

    # Document type — LLM-decided, no manual-type preservation. "Fresh
    # reprocess" means the LLM's pick wins; a user who curated a type
    # manually before should reprocess knowing they're asking for the
    # AI's verdict. Ontology canonicalisation runs first so a German
    # household doesn't accumulate English "Invoice" doctypes when the
    # LLM emits the wrong language (or stuffs a topic name in the
    # doctype field).
    doc_type = classification.get("document_type")
    if not _is_empty(doc_type):
        target_doctype = doc_type
        skip_doctype = False
        if ontology is not None:
            resolution = ontology.canonicalize_doctype(doc_type, lang)
            if resolution.cross_field:
                skip_doctype = True  # LLM put a topic in the doctype field.
            elif resolution.canonical:
                target_doctype = resolution.canonical
        if not skip_doctype:
            matched = fuzzy_match_entity(target_doctype, doc_types)
            if matched:
                updates["document_type"] = doc_types[matched]
                result.resolved_type = matched
            else:
                new_id = await paperless.create_doc_type(target_doctype)
                if new_id:
                    updates["document_type"] = new_id
                    result.resolved_type = target_doctype
                    result.created_new.append(f'document type "{target_doctype}"')

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

    # The classifier's date overwrites Paperless's auto-set `created`
    # on first classification (the auto-set value is just the upload
    # timestamp; the document's actual date is far more useful for
    # browsing and the mirror's filepath). On reprocess we leave the
    # existing `created` alone — by then it's either the previous
    # pipeline's verdict or a human correction, both better than
    # whatever a fresh LLM pass guesses today.
    date = classification.get("date")
    if not is_reprocess and date and isinstance(date, str) and _ISO_DATE.match(date):
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
