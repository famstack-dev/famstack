"""Document enrichment pipeline — shared by the archivist bot and the docs CLI.

The archivist bot runs this on every new Paperless upload; the
`stack docs reclassify` CLI runs the same pipeline against already-filed
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
from typing import Any

import aiohttp
from loguru import logger

from matching import (
    MAX_TITLE_LENGTH,
    _is_empty,
    fuzzy_match_entity,
    match_persons,
    match_topics,
)
from stack import resolve_model


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

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Token {self.token}"}

    @property
    def _json_headers(self) -> dict:
        return {**self._headers, "Content-Type": "application/json"}

    # ── Document reads ───────────────────────────────────────────────

    async def get_doc(self, doc_id: int) -> dict | None:
        async with self.http.get(
            f"{self.url}/api/documents/{doc_id}/", headers=self._headers,
        ) as resp:
            return await resp.json() if resp.status == 200 else None

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        async with self.http.get(
            f"{self.url}/api/documents/", headers=self._headers,
            params={"query": query, "page_size": limit, "ordering": "-created"},
        ) as resp:
            if resp.status == 200:
                return (await resp.json()).get("results", [])
            return []

    async def _list_entity(self, endpoint: str) -> dict:
        async with self.http.get(
            f"{self.url}/api/{endpoint}/?page_size=1000", headers=self._headers,
        ) as resp:
            if resp.status == 200:
                return {t["name"]: t["id"] for t in (await resp.json()).get("results", [])}
            return {}

    async def get_tags(self) -> dict:
        return await self._list_entity("tags")

    async def get_doc_types(self) -> dict:
        return await self._list_entity("document_types")

    async def get_correspondents(self) -> dict:
        return await self._list_entity("correspondents")

    async def update_doc(self, doc_id: int, updates: dict) -> bool:
        async with self.http.patch(
            f"{self.url}/api/documents/{doc_id}/",
            headers=self._json_headers, json=updates,
        ) as resp:
            return resp.status == 200

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

    async def create_tag(self, name: str, color: str = "#9e9e9e") -> int | None:
        async with self.http.post(
            f"{self.url}/api/tags/", headers=self._json_headers,
            json={
                "name": name, "color": color,
                "matching_algorithm": 0, "is_insensitive": True,
            },
        ) as resp:
            if resp.status == 201:
                data = await resp.json()
                return data["id"]
            return None

    async def create_doc_type(self, name: str) -> int | None:
        async with self.http.post(
            f"{self.url}/api/document_types/", headers=self._json_headers,
            json={"name": name, "matching_algorithm": 0, "is_insensitive": True},
        ) as resp:
            if resp.status == 201:
                return (await resp.json())["id"]
            return None

    async def create_correspondent(self, name: str) -> int | None:
        async with self.http.post(
            f"{self.url}/api/correspondents/", headers=self._json_headers,
            json={"name": name, "matching_algorithm": 0},
        ) as resp:
            if resp.status == 201:
                return (await resp.json())["id"]
            return None


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
                 key: str = "", bot_name: str = "archivist-bot"):
        self.http = http
        self.url = url.rstrip("/")
        self.key = key
        self.bot_name = bot_name

    def _endpoint(self) -> str:
        if not self.url:
            raise LLMUnavailableError("No AI endpoint configured — set up AI with 'stack up ai'")
        return self.url if self.url.endswith("/chat/completions") else f"{self.url}/chat/completions"

    async def _request(self, task: str, prompt: str, *, json_mode: bool = False) -> str:
        """Send a prompt to the LLM and return the response text.

        The task name (e.g. "classifier", "reformat") is resolved to a
        concrete model via resolve_model("<bot>/<task>"). The fallback chain:

          1. [ai.models] <bot>.<task> — task-specific override
          2. [ai.models] <bot>        — bot-level default
          3. [ai] default             — global fallback

        Uses the OpenAI-compatible chat completions API — works with oMLX,
        Ollama, LM Studio, or any provider that serves /v1/chat/completions.
        """
        model = resolve_model(f"{self.bot_name}/{task}")

        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"

        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
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
                raise LLMUnavailableError(f"HTTP {resp.status}")
        except asyncio.TimeoutError:
            raise LLMTimeoutError(f"{model} — model may still be loading, try again")
        except (LLMUnavailableError, LLMModelNotFoundError, LLMTimeoutError):
            raise
        except Exception as e:
            raise LLMUnavailableError(f"{e}")

    async def classify(
        self, *,
        ocr_text: str,
        tags: dict,
        doc_types: dict,
        correspondents: dict,
    ) -> dict:
        """Ask the LLM to classify a document based on its OCR text.

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
            ocr_text=ocr_text[:3000],
            person_names=person_names,
            category_tags=category_tags,
            doc_types=list(doc_types.keys()),
            correspondents=list(correspondents.keys()),
        )

        response = await self._request("classifier", prompt, json_mode=True)
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


# ── Enrichment ───────────────────────────────────────────────────────────

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NEW_TOPIC_COLOR = "#4caf50"


async def enrich_document(
    *,
    paperless: PaperlessAPI,
    classifier: Classifier,
    doc: dict,
) -> EnrichResult:
    """Classify a doc, reconcile entities, PATCH Paperless. Pure data out.

    No HTTP assumptions about the caller — collaborators are injected.
    No stdout / Matrix concerns — caller renders the result. Never raises:
    LLM / Paperless failures arrive through `EnrichResult.llm_error` or as
    empty resolved_* lists.
    """
    ocr_text = (doc.get("content") or "").strip()
    if not ocr_text:
        return EnrichResult()

    tags = await paperless.get_tags()
    doc_types = await paperless.get_doc_types()
    correspondents = await paperless.get_correspondents()

    try:
        classification = await classifier.classify(
            ocr_text=ocr_text, tags=tags,
            doc_types=doc_types, correspondents=correspondents,
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

    # Topics — open set. matching.py splits into existing vs new; new tags
    # are created in Paperless and treated as resolved.
    tag_ids = list(doc.get("tags", []))
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

    if tag_ids:
        updates["tags"] = list(set(tag_ids))

    # Document type — respect a manually-set type on the doc: the user
    # curated it and the LLM shouldn't overwrite. When unset, apply the
    # LLM's call (matching existing or creating new).
    doc_type = classification.get("document_type")
    if not _is_empty(doc_type):
        if doc.get("document_type"):
            result.resolved_type = doc_type
        else:
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
