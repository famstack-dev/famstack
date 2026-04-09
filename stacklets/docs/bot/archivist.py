"""Archivist — the document filing bot.

The killer feature of famstack: send a photo of a document to your family
chat, and it gets OCR'd, classified by AI, tagged, and filed in Paperless
— all automatically. No scanning app, no manual filing, no desktop needed.

The pipeline for every document:
  1. User sends a file/photo to the #documents Matrix room
  2. Archivist downloads it from Matrix
  3. Uploads to Paperless-ngx, which runs OCR and extracts text
  4. Sends the OCR text to the LLM (oMLX) for classification
  5. LLM returns structured JSON: title, category, person, type, correspondent
  6. Archivist creates any missing tags/types in Paperless and applies them
  7. Optionally reformats the raw OCR text into clean Markdown
  8. Reports back in the chat room with a summary and link

Beyond single files, the archivist also handles:
  - Multi-page scans: type ( → upload pages → type ) to combine into one PDF
  - URL archiving: paste a PDF or Google Docs link to download and file it
  - Document search: type any text to full-text search across all documents
  - Bilingual messages (en/de) loaded from messages/archivist.yml

Refactored from a standalone archivist_bot.py (1099 lines) into a
MicroBot subclass. The base class handles Matrix login, E2E encryption,
and the sync loop — this file focuses purely on document processing logic.
"""

import asyncio
import io
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

import aiohttp
import markdown
import yaml
from loguru import logger
from PIL import Image
from nio import (
    AsyncClient,
    RoomMessageMedia,
    RoomMessageImage,
    RoomMessageFile,
    RoomMessageText,
)

from microbot import MicroBot
from stack import resolve_model


@contextmanager
def _timed(operation: str):
    """Log an operation with its elapsed time. Use as a context manager:

        with _timed("LLM classify"):
            result = await llm_call(...)

    Logs start and completion with duration. On exception, logs the error
    with duration — useful for diagnosing timeouts and slow services.
    """
    t0 = time.monotonic()
    logger.info("[archivist] {} started", operation)
    try:
        yield
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error("[archivist] {} failed after {:.1f}s: {}", operation, elapsed, e)
        raise
    else:
        elapsed = time.monotonic() - t0
        logger.info("[archivist] {} completed in {:.1f}s", operation, elapsed)

# ── Constants ────────────────────────────────────────────────────────────────

SUPPORTED_MSGTYPES = {"m.file", "m.image"}

SCAN_BEGIN = {"scan", "("}
SCAN_END = {"done", "fertig", ")"}

HELP_COMMANDS = {"help", "hilfe", "?"}

# Regex to detect a message that is just a URL (no other text)
URL_PATTERN = re.compile(r'^https?://[^\s/]+\.[^\s/]+(/\S*)?$')

# Google Docs/Sheets/Slides URL patterns → export as PDF
GOOGLE_DOC_PATTERNS = {
    re.compile(r'https://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)'): "document",
    re.compile(r'https://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)'): "spreadsheets",
    re.compile(r'https://docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)'): "presentation",
}


# ── LLM Errors ───────────────────────────────────────────────────────────────

class LLMUnavailableError(Exception):
    """LLM service is not reachable — oMLX/Ollama might not be running."""

class LLMModelNotFoundError(Exception):
    """The configured model is not loaded on the LLM server."""

class LLMTimeoutError(Exception):
    """LLM took too long — large documents or cold model start can cause this."""


# ── Translations ─────────────────────────────────────────────────────────────

_messages_path = Path(__file__).parent / "messages" / "archivist.yml"
_MESSAGES = yaml.safe_load(_messages_path.read_text(encoding="utf-8"))


def _t(lang: str, key: str, **kwargs) -> str:
    """Get a translated message. Falls back to English if key is missing."""
    lang_msgs = _MESSAGES.get(lang, _MESSAGES["en"])
    text = lang_msgs.get(key, _MESSAGES["en"].get(key, key))
    if kwargs:
        text = text.format(**kwargs)
    return text.rstrip("\n")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _clean_filename(raw_filename: str, msgtype: str = "") -> str:
    """Strip UUIDs, tildes, and other noise from filenames for display."""
    clean = re.sub(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}~?\d*\.?', '', raw_filename)
    if not clean or clean == raw_filename:
        ext = raw_filename.rsplit(".", 1)[-1] if "." in raw_filename else ""
        if ext.lower() in ("jpg", "jpeg", "png", "tiff", "heic"):
            return f"photo.{ext}"
        elif ext.lower() == "pdf":
            return "document.pdf"
        elif ext:
            return f"file.{ext}"
        else:
            return "document"
    return clean


def _combine_images_to_pdf(files: list[tuple[str, bytes]]) -> bytes:
    """Combine multiple image files into a single multi-page PDF."""
    images = []
    for _, data in files:
        img = Image.open(io.BytesIO(data))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        images.append(img)

    pdf_buffer = io.BytesIO()
    images[0].save(pdf_buffer, "PDF", save_all=True, append_images=images[1:])
    return pdf_buffer.getvalue()


def _google_docs_export_url(url: str) -> tuple[str, str] | None:
    """If URL is a Google Docs link, return (export_url, doc_type). None otherwise."""
    for pattern, doc_type in GOOGLE_DOC_PATTERNS.items():
        match = pattern.search(url)
        if match:
            doc_id = match.group(1)
            export_url = f"https://docs.google.com/{doc_type}/d/{doc_id}/export?format=pdf"
            return export_url, doc_type
    return None


# ── ArchivistBot ─────────────────────────────────────────────────────────────

class ArchivistBot(MicroBot):
    """Document filing bot — watches a Matrix room for uploads, classifies
    them with an LLM, and files them in Paperless-ngx."""

    name = "archivist-bot"

    def __init__(self, homeserver, user_id, password, session_dir, **settings):
        super().__init__(homeserver, user_id, password, session_dir, **settings)
        # Shared config from env vars — rendered by the CLI from stack.toml
        self.paperless_url = os.environ.get("PAPERLESS_URL", "")
        self.paperless_token = os.environ.get("PAPERLESS_TOKEN", "")
        self.paperless_public_url = os.environ.get("PAPERLESS_PUBLIC_URL", "")
        self.openai_url = os.environ.get("OPENAI_URL", "")
        self.openai_key = os.environ.get("OPENAI_KEY", "")
        self.language = os.environ.get("LANGUAGE", "en")
        # Per-bot settings from stacklet.toml [bots.archivist.settings]
        self.classify_enabled = settings.get("classify", True)
        self.reformat_enabled = settings.get("reformat", True)
        self._scan_sessions: dict[str, dict] = {}
        self._http: aiohttp.ClientSession | None = None

    def t(self, key: str, **kwargs) -> str:
        return _t(self.language, key, **kwargs)

    def register_callbacks(self, client: AsyncClient) -> None:
        self.add_event_callback(self._on_file, (RoomMessageMedia, RoomMessageImage, RoomMessageFile))
        self.add_event_callback(self._on_text, RoomMessageText)

    async def start(self) -> None:
        logger.info("[archivist] Config: paperless={} openai={} language={} classify={} reformat={}",
                     self.paperless_url, self.openai_url, self.language,
                     self.classify_enabled, self.reformat_enabled)
        try:
            default_model = resolve_model(f"{self.name}/classifier")
            logger.info("[archivist] Model (classifier): {}", default_model)
        except ValueError as e:
            logger.warning("[archivist] {}", e)

        self._http = aiohttp.ClientSession()
        try:
            await super().start()
        finally:
            await self._http.close()

    def _ai_status(self) -> str:
        if self.openai_url:
            return "🧠 **AI classification:** enabled — documents are tagged automatically."
        return "💡 **AI classification:** not configured. Run `stack up ai` to enable automatic tagging."

    async def on_first_sync(self) -> None:
        """Called after initial sync — send welcome message to rooms."""
        url = self.paperless_public_url or self.paperless_url
        welcome = self.t("welcome", url=url, ai_status=self._ai_status())
        for room_id in self._client.rooms:
            await self._send(room_id, welcome)

    # ── Matrix helpers ───────────────────────────────────────────────────

    async def _set_typing(self, room_id: str, typing: bool = True):
        try:
            await self._client.room_typing(room_id, typing_state=typing, timeout=300000)
        except Exception:
            pass

    async def _send(self, room_id: str, text: str, reply_to: str | None = None):
        html = markdown.markdown(text, extensions=["tables", "fenced_code"])
        content = {
            "msgtype": "m.text",
            "body": text,
            "format": "org.matrix.custom.html",
            "formatted_body": html,
        }
        if reply_to:
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to}}
        await self._client.room_send(room_id=room_id, message_type="m.room.message", content=content)

    async def _download_matrix_file(self, mxc_url: str) -> bytes | None:
        """Download a file from Matrix using the authenticated media API."""
        server_name = mxc_url.replace("mxc://", "").split("/")[0]
        media_id = mxc_url.replace("mxc://", "").split("/")[1]
        download_url = f"{self.homeserver}/_matrix/client/v1/media/download/{server_name}/{media_id}"

        async with self._http.get(
            download_url,
            headers={"Authorization": f"Bearer {self._client.access_token}"},
        ) as resp:
            if resp.status == 200:
                return await resp.read()
            else:
                body = await resp.text()
                logger.error("[archivist] Download failed (HTTP {}): {}", resp.status, body)
                return None

    # ── Paperless API ────────────────────────────────────────────────────

    def _paperless_headers(self) -> dict:
        return {"Authorization": f"Token {self.paperless_token}"}

    async def _paperless_upload(self, filename: str, data: bytes) -> str | None:
        form = aiohttp.FormData()
        form.add_field("document", data, filename=filename)
        try:
            async with self._http.post(
                f"{self.paperless_url}/api/documents/post_document/",
                headers=self._paperless_headers(), data=form,
            ) as resp:
                if resp.status == 200:
                    task_id = (await resp.text()).strip().strip('"')
                    logger.info("[archivist] Uploaded {} → task {}", filename, task_id)
                    return task_id
                else:
                    logger.error("[archivist] Upload failed (HTTP {})", resp.status)
                    return None
        except (aiohttp.ClientConnectionError, aiohttp.ClientError, OSError) as e:
            logger.error("[archivist] Paperless unreachable: {}", e)
            return None

    async def _paperless_wait_task(self, task_id: str, timeout: int = 120) -> int | None:
        import time
        start = time.time()
        while time.time() - start < timeout:
            try:
                async with self._http.get(
                    f"{self.paperless_url}/api/tasks/?task_id={task_id}",
                    headers=self._paperless_headers(),
                ) as resp:
                    if resp.status == 200:
                        tasks = await resp.json()
                        if tasks:
                            task = tasks[0] if isinstance(tasks, list) else tasks
                            status = task.get("status", "")
                            if status == "SUCCESS":
                                doc_id = task.get("related_document")
                                return int(doc_id) if doc_id else None
                            elif status == "FAILURE":
                                logger.error("[archivist] Task failed: {}", task.get("result"))
                                return None
            except (aiohttp.ClientConnectionError, aiohttp.ClientError, OSError) as e:
                logger.error("[archivist] Paperless unreachable while waiting for task: {}", e)
                return None
            await asyncio.sleep(3)
        logger.error("[archivist] Task {} timed out", task_id)
        return None

    async def _paperless_get_doc(self, doc_id: int) -> dict | None:
        async with self._http.get(
            f"{self.paperless_url}/api/documents/{doc_id}/",
            headers=self._paperless_headers(),
        ) as resp:
            return await resp.json() if resp.status == 200 else None

    async def _paperless_get_tags(self) -> dict:
        async with self._http.get(
            f"{self.paperless_url}/api/tags/?page_size=1000",
            headers=self._paperless_headers(),
        ) as resp:
            if resp.status == 200:
                return {t["name"]: t["id"] for t in (await resp.json()).get("results", [])}
            return {}

    async def _paperless_get_doc_types(self) -> dict:
        async with self._http.get(
            f"{self.paperless_url}/api/document_types/?page_size=1000",
            headers=self._paperless_headers(),
        ) as resp:
            if resp.status == 200:
                return {t["name"]: t["id"] for t in (await resp.json()).get("results", [])}
            return {}

    async def _paperless_get_correspondents(self) -> dict:
        async with self._http.get(
            f"{self.paperless_url}/api/correspondents/?page_size=1000",
            headers=self._paperless_headers(),
        ) as resp:
            if resp.status == 200:
                return {c["name"]: c["id"] for c in (await resp.json()).get("results", [])}
            return {}

    async def _paperless_create_tag(self, name: str, color: str = "#9e9e9e") -> int | None:
        async with self._http.post(
            f"{self.paperless_url}/api/tags/",
            headers={**self._paperless_headers(), "Content-Type": "application/json"},
            json={"name": name, "color": color},
        ) as resp:
            if resp.status == 201:
                data = await resp.json()
                logger.info("[archivist] Created tag: {} (id={})", name, data["id"])
                return data["id"]
            return None

    async def _paperless_create_doc_type(self, name: str) -> int | None:
        async with self._http.post(
            f"{self.paperless_url}/api/document_types/",
            headers={**self._paperless_headers(), "Content-Type": "application/json"},
            json={"name": name},
        ) as resp:
            if resp.status == 201:
                return (await resp.json())["id"]
            return None

    async def _paperless_create_correspondent(self, name: str) -> int | None:
        async with self._http.post(
            f"{self.paperless_url}/api/correspondents/",
            headers={**self._paperless_headers(), "Content-Type": "application/json"},
            json={"name": name},
        ) as resp:
            if resp.status == 201:
                return (await resp.json())["id"]
            return None

    async def _paperless_update_doc(self, doc_id: int, updates: dict) -> bool:
        async with self._http.patch(
            f"{self.paperless_url}/api/documents/{doc_id}/",
            headers={**self._paperless_headers(), "Content-Type": "application/json"},
            json=updates,
        ) as resp:
            return resp.status == 200

    async def _paperless_search(self, query: str, limit: int = 5) -> list[dict]:
        async with self._http.get(
            f"{self.paperless_url}/api/documents/",
            headers=self._paperless_headers(),
            params={"query": query, "page_size": limit, "ordering": "-created"},
        ) as resp:
            if resp.status == 200:
                return (await resp.json()).get("results", [])
            return []

    # ── LLM (OpenAI-compatible API) ──────────────────────────────────────

    async def _llm_request(self, task: str, prompt: str, json_mode: bool = False) -> str:
        """Send a prompt to the LLM and return the response text.

        The task name (e.g. "classifier", "reformat") is resolved to a
        concrete model via resolve_model("archivist/{task}"). This walks:
          1. [ai.models] archivist.classifier — task-specific
          2. [ai.models] archivist            — bot-level
          3. [ai] default                     — global fallback

        Uses the OpenAI-compatible chat completions API — works with oMLX,
        Ollama, LM Studio, or any provider that serves /v1/chat/completions.
        """
        model = resolve_model(f"{self.name}/{task}")

        headers = {"Content-Type": "application/json"}
        if self.openai_key:
            headers["Authorization"] = f"Bearer {self.openai_key}"

        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        url = self.openai_url.rstrip("/")
        if not url:
            raise LLMUnavailableError("No AI endpoint configured — set up AI with 'stack up ai'")
        if not url.endswith("/chat/completions"):
            url = url.rstrip("/") + "/chat/completions"

        with _timed(f"LLM {task} (model={model})"):
            try:
                async with self._http.post(
                    url, headers=headers, json=body,
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result["choices"][0]["message"]["content"]
                    elif resp.status == 401:
                        raise LLMUnavailableError("Authentication failed — check [ai].openai_key in stack.toml")
                    elif resp.status == 404:
                        body_text = await resp.text()
                        if "not found" in body_text.lower():
                            raise LLMModelNotFoundError(f"{model} — is it loaded in oMLX?")
                        raise LLMUnavailableError(f"HTTP 404: {body_text[:200]}")
                    else:
                        raise LLMUnavailableError(f"HTTP {resp.status}")
            except asyncio.TimeoutError:
                raise LLMTimeoutError(f"{model} — model may still be loading, try again")
            except (LLMUnavailableError, LLMModelNotFoundError, LLMTimeoutError):
                raise
            except Exception as e:
                raise LLMUnavailableError(f"{e}")

    async def _classify(self, ocr_text: str, tags: dict, doc_types: dict, correspondents: dict) -> dict:
        """Ask the LLM to classify a document based on its OCR text.

        The prompt gives the LLM the list of existing tags, document types,
        and correspondents from Paperless so it picks from known values when
        possible. If nothing fits, it can suggest new ones — the archivist
        creates them automatically. Returns structured JSON metadata.
        """
        person_tags = [t for t in tags if t.startswith("Person: ")]
        category_tags = [t for t in tags if not t.startswith("Person: ")]
        truncated = ocr_text[:3000]

        prompt = f"""Classify this document. Return ONLY a JSON object.

IMPORTANT: Always prefer existing values from the lists below. Only suggest
a new value when NOTHING in the list is a reasonable match. For example,
if "Finanzamt" exists and the document is from "Finanzamt Friedrichshafen",
use the existing "Finanzamt" — don't create a new entry.

Existing categories: {json.dumps(category_tags, ensure_ascii=False)}
Existing persons: {json.dumps(person_tags, ensure_ascii=False)}
Existing document types: {json.dumps(list(doc_types.keys()), ensure_ascii=False)}
Existing correspondents: {json.dumps(list(correspondents.keys()), ensure_ascii=False)}

Return this exact JSON structure:
{{
  "title": "short descriptive title in the document's language",
  "date": "YYYY-MM-DD or null",
  "category": "pick from existing categories, or suggest a new one only if nothing fits",
  "person": "pick from existing persons when possible, or null if no person is relevant",
  "document_type": "pick from existing types, or suggest a new one only if nothing fits",
  "correspondent": "pick from existing correspondents, or the actual sender if truly new",
  "summary": "2-3 sentence summary with key facts: amounts, dates, names, deadlines"
}}

Document text:
---
{truncated}
---"""

        response = await self._llm_request("classifier", prompt, json_mode=True)
        try:
            return json.loads(response) if response else {}
        except json.JSONDecodeError:
            logger.warning("[archivist] LLM returned invalid JSON: {}", response[:200])
            return {}

    async def _reformat(self, ocr_text: str) -> str | None:
        """Reformat raw OCR text into clean, readable Markdown.

        OCR output is often messy: broken lines, garbled characters, no
        structure. The LLM fixes artifacts while preserving all factual
        content. The reformatted text replaces the original in Paperless,
        making documents actually readable. Non-critical — if it fails,
        the original OCR text is kept.
        """
        prompt = f"""Reformat this OCR-scanned document into clean, well-structured Markdown.

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
{ocr_text[:6000]}
---"""

        try:
            result = await self._llm_request("reformat", prompt)
            if result and len(result.strip()) > 20:
                return result.strip()
            return None
        except (LLMUnavailableError, LLMTimeoutError):
            return None

    # ── Document processing pipeline ─────────────────────────────────────

    async def _process_document(
        self, room_id: str, filename: str, display_name: str,
        file_data: bytes, reply_to: str | None = None,
    ):
        """The core pipeline: upload → OCR → classify → tag → report.

        Shared by all entry points: single file upload, multi-page scan,
        and URL archiving. Each step can fail independently — the bot
        reports partial progress so the user knows what happened.
        """
        logger.info("[archivist] Processing: {} ({} bytes)", display_name, len(file_data))

        # Text-based files are already readable — just upload and file them
        # without running OCR classification or reformatting.
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in ("md", "txt", "csv", "json", "yaml", "yml", "toml"):
            task_id = await self._paperless_upload(filename, file_data)
            if not task_id:
                await self._send(room_id, self.t("upload_failed", name=display_name), reply_to)
                return
            doc_id = await self._paperless_wait_task(task_id)
            link = f"{self.paperless_public_url}/documents/{doc_id}/details" if doc_id and self.paperless_public_url else ""
            if doc_id:
                await self._send(room_id, f"{self.t('filed', title=display_name)}\n\n  {link}", reply_to)
            else:
                await self._send(room_id, self.t("ocr_failed", name=display_name), reply_to)
            return

        task_id = await self._paperless_upload(filename, file_data)
        if not task_id:
            await self._send(room_id, self.t("upload_failed", name=display_name), reply_to)
            return

        doc_id = await self._paperless_wait_task(task_id)
        if not doc_id:
            await self._send(room_id, self.t("ocr_failed", name=display_name), reply_to)
            return

        link = f"{self.paperless_public_url}/documents/{doc_id}/details" if self.paperless_public_url else ""
        doc = await self._paperless_get_doc(doc_id)
        if not doc:
            await self._send(room_id, self.t("filed_no_details", name=display_name, link=link), reply_to)
            return

        ocr_text = doc.get("content", "")
        if not ocr_text or len(ocr_text.strip()) < 10:
            await self._send(room_id, self.t("filed_no_text", name=display_name, link=link), reply_to)
            return

        # Classification disabled — just file with the link
        if not self.classify_enabled:
            await self._send(room_id, f"{self.t('filed', title=display_name)}\n\n  {link}", reply_to)
            return

        # Classify
        tags = await self._paperless_get_tags()
        doc_types = await self._paperless_get_doc_types()
        correspondents = await self._paperless_get_correspondents()

        try:
            classification = await self._classify(ocr_text, tags, doc_types, correspondents)
        except LLMUnavailableError:
            await self._send(room_id, self.t("llm_unavailable", name=display_name, url=self.openai_url, link=link), reply_to)
            return
        except LLMModelNotFoundError as e:
            await self._send(room_id, self.t("llm_model_missing", name=display_name, model=str(e), link=link), reply_to)
            return
        except LLMTimeoutError:
            await self._send(room_id, self.t("llm_timeout", name=display_name, link=link), reply_to)
            return

        if not classification:
            await self._send(room_id, self.t("classify_failed", name=display_name, link=link), reply_to)
            return

        # Apply classification
        updates = {}
        summary = []
        created_new = []

        title = classification.get("title")
        if title and isinstance(title, str):
            updates["title"] = title

        tag_ids = list(doc.get("tags", []))
        category = classification.get("category")
        if category and isinstance(category, str):
            if category in tags:
                tag_ids.append(tags[category])
                summary.append(self.t("category", value=category))
            else:
                new_id = await self._paperless_create_tag(category, "#4caf50")
                if new_id:
                    tag_ids.append(new_id)
                    summary.append(self.t("category_new", value=category))
                    created_new.append(f"tag \"{category}\"")

        # Person tags — only match existing, never create
        person = classification.get("person")
        if person and isinstance(person, str) and person in tags:
            tag_ids.append(tags[person])
            summary.append(self.t("person", value=person.replace("Person: ", "")))

        if tag_ids:
            updates["tags"] = list(set(tag_ids))

        doc_type = classification.get("document_type")
        if doc_type and isinstance(doc_type, str):
            if doc_type in doc_types:
                updates["document_type"] = doc_types[doc_type]
                summary.append(self.t("type", value=doc_type))
            else:
                new_id = await self._paperless_create_doc_type(doc_type)
                if new_id:
                    updates["document_type"] = new_id
                    summary.append(self.t("type_new", value=doc_type))
                    created_new.append(f"document type \"{doc_type}\"")

        correspondent = classification.get("correspondent")
        if correspondent and isinstance(correspondent, str):
            if correspondent in correspondents:
                updates["correspondent"] = correspondents[correspondent]
                summary.append(self.t("from", value=correspondent))
            else:
                new_id = await self._paperless_create_correspondent(correspondent)
                if new_id:
                    updates["correspondent"] = new_id
                    summary.append(self.t("from_new", value=correspondent))
                    created_new.append(f"correspondent \"{correspondent}\"")

        date = classification.get("date")
        if date and isinstance(date, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', date):
            updates["created"] = date
            summary.append(self.t("date", value=date))

        if updates:
            await self._paperless_update_doc(doc_id, updates)

        # Reformat OCR text into clean markdown
        await self._set_typing(room_id)
        reformat_failed = False
        if self.reformat_enabled:
            formatted = await self._reformat(ocr_text)
            if formatted:
                await self._paperless_update_doc(doc_id, {"content": formatted})
            else:
                reformat_failed = True

        # Build summary message
        doc_summary = classification.get("summary", "")
        display_title = title or display_name
        lines = [self.t("filed", title=display_title, doc_id=doc_id), ""]

        if summary:
            for s in summary:
                lines.append(f"  {s}")
        else:
            lines.append(f"  {self.t('no_classification')}")

        if doc_summary and isinstance(doc_summary, str):
            lines.extend(["", f"  {doc_summary}"])

        tag_keywords = []
        if category:
            tag_keywords.append(f"#{category}")
        if person and isinstance(person, str):
            tag_keywords.append(f"#{person.replace('Person: ', '')}")
        if doc_type:
            tag_keywords.append(f"#{doc_type}")
        if correspondent:
            tag_keywords.append(f"#{correspondent}")
        if tag_keywords:
            lines.extend(["", "  " + " ".join(tag_keywords)])

        if created_new:
            lines.extend(["", f"  {self.t('new_in_paperless', items=', '.join(created_new))}"])

        if reformat_failed:
            lines.extend(["", f"  {self.t('reformat_failed')}"])

        if link:
            lines.extend(["", f"  {link}"])

        await self._send(room_id, "\n".join(lines), reply_to)
        logger.info("[archivist] Processed: {} → doc {} [{}]", filename, doc_id, ", ".join(summary))

    # ── Event handlers ───────────────────────────────────────────────────

    async def _on_file(self, room, event) -> None:
        if event.sender == self.user_id:
            return

        content = event.source.get("content", {})
        msgtype = content.get("msgtype", "")
        if msgtype not in SUPPORTED_MSGTYPES:
            return

        url = content.get("url", "")
        if not url or not url.startswith("mxc://"):
            return

        raw_filename = content.get("body", "document")
        display_name = _clean_filename(raw_filename, msgtype)
        sender_name = event.sender.split(":")[0].replace("@", "").capitalize()
        reply_to = event.event_id

        # Multi-page scan mode
        if event.sender in self._scan_sessions:
            await self._handle_scan_page(room.room_id, event, url, raw_filename)
            return

        await self._set_typing(room.room_id)
        try:
            file_data = await self._download_matrix_file(url)
            if not file_data:
                await self._send(room.room_id, self.t("download_failed_matrix", name=display_name), reply_to)
                return

            if msgtype == "m.image":
                await self._send(room.room_id, self.t("received_photo", sender=sender_name), reply_to)
            else:
                await self._send(room.room_id, self.t("received_document", sender=sender_name), reply_to)

            await self._process_document(room.room_id, raw_filename, display_name, file_data, reply_to)
        finally:
            await self._set_typing(room.room_id, typing=False)

    async def _on_text(self, room, event: RoomMessageText) -> None:
        if event.sender == self.user_id:
            return

        query = event.body.strip()
        if not query:
            return
        query_lower = query.lower()
        reply_to = event.event_id

        try:
            if query_lower in HELP_COMMANDS:
                url = self.paperless_public_url or self.paperless_url
                await self._send(room.room_id, self.t("welcome", url=url, ai_status=self._ai_status()), reply_to)

            elif query_lower in SCAN_BEGIN:
                sender_name = event.sender.split(":")[0].replace("@", "").capitalize()
                self._scan_sessions[event.sender] = {"files": [], "room_id": room.room_id}
                await self._send(room.room_id, self.t("scan_started", sender=sender_name), reply_to)

            elif query_lower in SCAN_END:
                if event.sender in self._scan_sessions:
                    await self._handle_scan_complete(room.room_id, event.sender, reply_to)
                else:
                    await self._send(room.room_id, self.t("no_active_scan"), reply_to)

            elif query_lower.startswith("show ") and query[5:].strip().isdigit():
                await self._handle_show(room.room_id, int(query[5:].strip()), reply_to)

            elif URL_PATTERN.match(query):
                await self._handle_url(room.room_id, query, reply_to)

            else:
                await self._handle_search(room.room_id, query, reply_to)

        except Exception as e:
            logger.error("[archivist] Error handling message: {}", e, exc_info=True)

    # ── Scan mode ────────────────────────────────────────────────────────

    async def _handle_scan_page(self, room_id: str, event, url: str, raw_filename: str):
        reply_to = event.event_id
        try:
            file_data = await self._download_matrix_file(url)
        except Exception as e:
            await self._send(room_id, self.t("scan_page_failed", error=str(e)), reply_to)
            return

        if not file_data:
            await self._send(room_id, self.t("scan_page_failed_matrix"), reply_to)
            return

        session = self._scan_sessions[event.sender]
        session["files"].append((raw_filename, file_data))
        page_num = len(session["files"])
        await self._send(room_id, self.t("page_received", num=page_num), reply_to)

    async def _handle_scan_complete(self, room_id: str, sender: str, reply_to: str | None = None):
        session = self._scan_sessions.pop(sender)
        files = session["files"]
        sender_name = sender.split(":")[0].replace("@", "").capitalize()

        if not files:
            await self._send(room_id, self.t("scan_cancelled"), reply_to)
            return

        await self._set_typing(room_id)
        try:
            if len(files) == 1:
                filename, file_data = files[0]
                display_name = _clean_filename(filename)
                await self._send(room_id, self.t("scan_complete_single"), reply_to)
                await self._process_document(room_id, filename, display_name, file_data, reply_to)
                return

            page_count = len(files)
            await self._send(room_id, self.t("scan_complete_multi", count=page_count), reply_to)

            try:
                pdf_data = _combine_images_to_pdf(files)
            except Exception as e:
                await self._send(room_id, self.t("scan_combine_failed", error=str(e)), reply_to)
                return

            filename = f"scan-{sender_name.lower()}-{page_count}p.pdf"
            display_name = f"scan ({page_count} pages)"
            await self._process_document(room_id, filename, display_name, pdf_data, reply_to)
        finally:
            await self._set_typing(room_id, typing=False)

    # ── URL archiving ────────────────────────────────────────────────────

    async def _handle_url(self, room_id: str, url: str, reply_to: str | None = None):
        google_export = _google_docs_export_url(url)
        if google_export:
            download_url, doc_type = google_export
            type_labels = {"document": "Google Doc", "spreadsheets": "Google Sheet", "presentation": "Google Slides"}
            await self._send(room_id, self.t("downloading_google", type=type_labels.get(doc_type, "Google Doc")), reply_to)
        else:
            download_url = url
            await self._send(room_id, self.t("downloading_url"), reply_to)

        await self._set_typing(room_id)
        try:
            try:
                async with self._http.get(download_url, timeout=aiohttp.ClientTimeout(total=60), allow_redirects=True) as resp:
                    if resp.status != 200:
                        await self._send(room_id, self.t("url_http_error", status=resp.status), reply_to)
                        return
                    file_data = await resp.read()
                    content_type = resp.content_type or ""
            except asyncio.TimeoutError:
                await self._send(room_id, self.t("url_timeout"), reply_to)
                return
            except aiohttp.ClientError as e:
                await self._send(room_id, self.t("url_error", error=str(e)), reply_to)
                return

            if not file_data:
                await self._send(room_id, self.t("url_empty"), reply_to)
                return

            # Determine filename
            if google_export:
                filename = f"google-{doc_type}.pdf"
                display_name = type_labels.get(doc_type, "Google Doc")
            elif "pdf" in content_type or url.lower().endswith(".pdf"):
                url_path = url.split("?")[0].split("#")[0]
                filename = url_path.rsplit("/", 1)[-1] if "/" in url_path else "document.pdf"
                if not filename.lower().endswith(".pdf"):
                    filename = "document.pdf"
                display_name = filename
            elif file_data[:5] == b'%PDF-':
                filename = display_name = "document.pdf"
            else:
                await self._send(room_id, self.t("url_not_pdf", content_type=content_type), reply_to)
                return

            await self._process_document(room_id, filename, display_name, file_data, reply_to)
        finally:
            await self._set_typing(room_id, typing=False)

    # ── Search ───────────────────────────────────────────────────────────

    async def _handle_search(self, room_id: str, query: str, reply_to: str | None = None):
        results = await self._paperless_search(query)
        if not results:
            await self._send(room_id, self.t("search_no_results", query=query), reply_to)
            return

        base_url = self.paperless_public_url or self.paperless_url
        lines = [self.t("search_results", count=len(results), query=query)]
        for doc in results:
            title = doc.get("title", "Untitled")
            doc_id = doc.get("id")
            created = doc.get("created", "")[:10]
            lines.append(f"  #{doc_id} {created} — {title} → {base_url}/documents/{doc_id}/details")

        await self._send(room_id, "\n".join(lines), reply_to)

    # ── Show document content ─────────────────────────────────────────

    async def _handle_show(self, room_id: str, doc_id: int, reply_to: str | None = None):
        """Fetch a document's content from Paperless and return it as Markdown."""
        doc = await self._paperless_get_doc(doc_id)
        if not doc:
            await self._send(room_id, f"Document #{doc_id} not found.", reply_to)
            return

        title = doc.get("title", "Untitled")
        content = doc.get("content", "").strip()
        link = f"{self.paperless_public_url}/documents/{doc_id}/details" if self.paperless_public_url else ""

        if not content:
            await self._send(room_id, f"**{title}** — no text content available.\n\n  {link}", reply_to)
            return

        # Matrix has message size limits — truncate long documents
        if len(content) > 4000:
            content = content[:4000] + "\n\n[... truncated]"

        lines = [f"**{title}**", "", content]
        if link:
            lines.extend(["", link])

        await self._send(room_id, "\n".join(lines), reply_to)
