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

from git_mirror import GitMirror
from matching import (
    _is_empty, fuzzy_match_entity, match_persons, match_topics,
    build_document_event, deduplicate_hashtags, MAX_TITLE_LENGTH,
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


# ── Paperless Errors ─────────────────────────────────────────────────────────

class PaperlessDuplicateError(Exception):
    """Paperless rejected the upload as a content-hash duplicate of an
    already-filed doc. Carries the original's id + title so the chat
    reply can point the user at what's already there."""
    def __init__(self, doc_id: int | None, title: str | None):
        self.doc_id = doc_id
        self.title = title
        super().__init__(f"duplicate of #{doc_id}: {title}")


_DUPLICATE_RE = re.compile(r"duplicate of\s+(.+?)\s+\(#(\d+)\)", re.IGNORECASE)



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
        self.mirror_to_git = settings.get("mirror_to_git", True)
        self.mirror_org = settings.get("mirror_org", "family")
        self._scan_sessions: dict[str, dict] = {}
        self._http: aiohttp.ClientSession | None = None
        self._mirror: GitMirror | None = None
        self._paperless_version: str = ""

    def t(self, key: str, **kwargs) -> str:
        return _t(self.language, key, **kwargs)

    def register_callbacks(self, client: AsyncClient) -> None:
        self.add_event_callback(self._on_file, (RoomMessageMedia, RoomMessageImage, RoomMessageFile))
        self.add_event_callback(self._on_text, RoomMessageText)

    async def start(self) -> None:
        logger.info("[archivist] Config: paperless={} openai={} language={} classify={} reformat={} mirror_to_git={}",
                     self.paperless_url, self.openai_url, self.language,
                     self.classify_enabled, self.reformat_enabled, self.mirror_to_git)
        try:
            default_model = resolve_model(f"{self.name}/classifier")
            logger.info("[archivist] Model (classifier): {}", default_model)
        except ValueError as e:
            logger.warning("[archivist] {}", e)

        self._http = aiohttp.ClientSession()
        try:
            if self.mirror_to_git:
                self._init_mirror()
            await super().start()
        finally:
            await self._http.close()

    def _init_mirror(self) -> None:
        """Build a GitMirror if all required env is present.

        Soft-fails: missing env just disables the mirror for this run.
        The live reachability check happens inside `GitMirror.ensure_setup`
        on first publish.
        """
        code_url = os.environ.get("CODE_URL", "")
        admin_user = os.environ.get("MATRIX_ADMIN_USER", "")
        admin_password = os.environ.get("MATRIX_ADMIN_PASSWORD", "")
        admin_ids = os.environ.get("STACK_ADMIN_USER_IDS", "")

        if not (code_url and admin_user and admin_password):
            logger.info("[archivist] Git mirror disabled — CODE_URL or admin creds missing")
            return

        # @arthur:homestead.me → arthur
        admin_usernames = []
        for raw in admin_ids.split(","):
            raw = raw.strip()
            if not raw:
                continue
            name = raw.lstrip("@").split(":", 1)[0]
            if name and name != admin_user:
                admin_usernames.append(name)

        # `self._session_dir` is already the in-container path the bot
        # runner mounts (`/data/<stacklet>/bot`). Don't read DATA_DIR —
        # that env var carries the host path and would dump mirror state
        # outside the container's volume mount.
        self._mirror = GitMirror(
            code_url=code_url,
            admin_user=admin_user,
            admin_password=admin_password,
            admin_usernames=admin_usernames,
            data_dir=self._session_dir,
            org_name=self.mirror_org,
        )
        logger.info("[archivist] Git mirror configured: {} org={} (admins: {})",
                    code_url, self.mirror_org, ", ".join(admin_usernames) or "-")

    def _ai_status(self) -> str:
        if self.openai_url:
            return "🧠 **AI classification:** enabled — documents are tagged automatically."
        return "💡 **AI classification:** not configured. Run `stack up ai` to enable automatic tagging."

    async def _safe_mirror(
        self, *,
        doc_id: int,
        classification: dict,
        body_text: str,
        processing: str,
        model: str | None,
        fallback_title: str,
        paperless_tags: list[str],
    ) -> None:
        """Mirror a filed doc to Forgejo — never raises, never blocks the reply.

        Runs for every Paperless-accepted doc, regardless of whether AI
        classification or reformat succeeded. That way the Forgejo archive
        stays 1:1 with Paperless: an LLM flake or OCR miss can reduce the
        richness of a mirror entry but can't make it disappear.
        """
        if not self._mirror:
            return
        try:
            await self._mirror.publish(
                paperless_id=doc_id,
                classification=classification,
                body_text=body_text,
                processing=processing,
                model=model,
                paperless_url=self.paperless_public_url or self.paperless_url,
                tags=paperless_tags,
                fallback_title=fallback_title,
            )
        except Exception as e:
            logger.warning("[archivist] Git mirror failed for doc #{}: {}", doc_id, e)

    def _duplicate_reply(self, name: str, e: PaperlessDuplicateError) -> str:
        """Render the 'already filed' chat reply for a Paperless duplicate.

        Points the user at the original doc's Paperless page so they can
        verify the match instead of wondering why the upload 'failed'.
        """
        link = (f"{self.paperless_public_url}/documents/{e.doc_id}/details"
                if e.doc_id and self.paperless_public_url else "")
        return self.t("already_filed",
                      name=name,
                      doc_id=e.doc_id if e.doc_id is not None else "?",
                      title=e.title or "(no title)",
                      link=link)

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

    async def _paperless_upload(self, filename: str, data: bytes,
                                 content_type: str | None = None) -> str | None:
        """Upload a file to Paperless. Returns the task id on success.

        When `content_type` is given it's set on the multipart field —
        important for text-like files where aiohttp's default of
        application/octet-stream stops Paperless from matching a parser
        and the server returns 400 with no useful chat message. On
        failure we log the response body (truncated) so the next 400
        isn't a mystery.
        """
        form = aiohttp.FormData()
        field_kwargs: dict = {"filename": filename}
        if content_type:
            field_kwargs["content_type"] = content_type
        form.add_field("document", data, **field_kwargs)
        try:
            async with self._http.post(
                f"{self.paperless_url}/api/documents/post_document/",
                headers=self._paperless_headers(), data=form,
            ) as resp:
                if resp.status == 200:
                    task_id = (await resp.text()).strip().strip('"')
                    logger.info("[archivist] Uploaded {} → task {}", filename, task_id)
                    return task_id
                body = (await resp.text())[:400]
                logger.error("[archivist] Upload failed (HTTP {}): {} — body: {}",
                             resp.status, filename, body)
                return None
        except (aiohttp.ClientConnectionError, aiohttp.ClientError, OSError) as e:
            logger.error("[archivist] Paperless unreachable: {}", e)
            return None

    async def _paperless_wait_task(self, task_id: str, timeout: int = 120) -> int | None:
        """Poll Paperless for task completion. Raises PaperlessDuplicateError
        when Paperless rejects the upload as a duplicate; returns None for
        every other FAILURE / timeout / transport error so the caller can
        render the generic `ocr_failed` message."""
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
                                result = task.get("result") or ""
                                logger.error("[archivist] Task failed: {}", result)
                                match = _DUPLICATE_RE.search(result)
                                if match:
                                    title = match.group(1).strip()
                                    dup_id = int(match.group(2))
                                    raise PaperlessDuplicateError(dup_id, title)
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

    # ── Paperless entity creation ──────────────────────────────────────
    #
    # All entities use matching_algorithm=0 (disabled). The LLM classifies
    # every document; Paperless just stores what the LLM decides.
    #
    # Why not auto-learn (algorithm 6)?
    #
    # Paperless auto-assigns during document consumption -- BEFORE the LLM
    # runs. With algorithm 6, Paperless learns from the first few LLM-assigned
    # documents, then starts pre-assigning based on that tiny sample:
    #
    #   1. LLM tags three invoices as "Shopping"
    #   2. Paperless learns: "Shopping" = common tag
    #   3. Paperless auto-assigns "Shopping" to every new document at ingest
    #   4. LLM adds the correct tag, but "Shopping" is already there too
    #   5. Result: every document gets "Shopping" regardless of content
    #
    # Same failure mode hit correspondents: "Denny Gunawan" (first correspondent
    # created) got auto-assigned to all subsequent documents.
    #
    # Algorithm 0 means: Paperless never guesses. The LLM reads the actual
    # document text and makes the call. If the LLM is unavailable, documents
    # get filed without tags -- the user can classify manually in the UI.

    async def _paperless_create_tag(self, name: str, color: str = "#9e9e9e") -> int | None:
        async with self._http.post(
            f"{self.paperless_url}/api/tags/",
            headers={**self._paperless_headers(), "Content-Type": "application/json"},
            json={
                "name": name,
                "color": color,
                "matching_algorithm": 0,
                "is_insensitive": True,
            },
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
            json={
                "name": name,
                "matching_algorithm": 0,
                "is_insensitive": True,
            },
        ) as resp:
            if resp.status == 201:
                return (await resp.json())["id"]
            return None

    async def _paperless_create_correspondent(self, name: str) -> int | None:
        # matching_algorithm 0 (none): don't let Paperless auto-assign
        # correspondents. The LLM reads the document and decides who sent
        # it. Paperless's auto-learn (6) guesses wrong with few samples
        # (e.g. assigns the first correspondent to every new document).
        async with self._http.post(
            f"{self.paperless_url}/api/correspondents/",
            headers={**self._paperless_headers(), "Content-Type": "application/json"},
            json={
                "name": name,
                "matching_algorithm": 0,
            },
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

        Returns structured JSON with:
        - topics: subject areas (1-2 tags, e.g. ["Insurance", "Medical"])
        - persons: which family members this belongs to
        - correspondent: who sent/issued this document
        - document_type: optional format (Invoice, Receipt, etc.)
        """
        person_tags = [t for t in tags if t.startswith("Person: ")]
        # Strip "Person: " prefix for the prompt so the LLM sees clean
        # first names like "Homer", "Marge" -- not "Person: Homer".
        person_names = [t.replace("Person: ", "") for t in person_tags]
        category_tags = [t for t in tags if not t.startswith("Person: ")]
        truncated = ocr_text[:3000]

        # ── Classification prompt ────────────────────────────────────────
        #
        # Simplified to three clear axes:
        #   topic         = what is this about?   "Insurance", "Shopping"
        #   person        = which family member?   "Homer", "Bart", or null
        #   correspondent = who sent it?           "Springfield Nuclear", "Kwik-E-Mart"
        #
        # A Kwik-E-Mart receipt for Homer:
        #   topic="Shopping", person="Homer", correspondent="Kwik-E-Mart"
        # A school letter about Bart:
        #   topics=["School"], person="Bart", correspondent="Springfield Elementary"
        # A health insurance invoice for Homer:
        #   topics=["Insurance", "Medical"], person="Homer", correspondent="AOK"

        prompt = f"""Classify this document. Return ONLY a JSON object.

IMPORTANT: Always prefer existing values from the lists below. Only suggest
a new value when NOTHING in the list is a reasonable match.

Family members: {json.dumps(person_names, ensure_ascii=False)}
Existing topic tags: {json.dumps(category_tags, ensure_ascii=False)}
Existing document types: {json.dumps(list(doc_types.keys()), ensure_ascii=False)}
Existing correspondents: {json.dumps(list(correspondents.keys()), ensure_ascii=False)}

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
        """The core pipeline: upload → OCR → classify → tag → report → emit event.

        Shared by all entry points: single file upload, multi-page scan,
        and URL archiving. Each step can fail independently — the bot
        reports partial progress so the user knows what happened.

        After the human-readable summary, emits a dev.famstack.document
        custom event with full structured metadata for downstream bots.
        """
        logger.info("[archivist] Processing: {} ({} bytes)", display_name, len(file_data))

        # Text-like extensions skip reformat (the content is already clean)
        # but still run classification + mirror like every other document.
        # Paperless only has parsers registered for text/plain and text/csv,
        # so everything else in the text-like set is renamed to .txt at
        # upload time. The Paperless-side filename loses its source suffix,
        # but the mirror keeps `display_name` as its fallback title, so the
        # original `.md` / `.yaml` / ... shows up in the archive.
        TEXT_LIKE = ("md", "txt", "csv", "json", "yaml", "yml", "toml")
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        is_text = ext in TEXT_LIKE
        if is_text:
            if ext == "csv":
                upload_filename, upload_type = filename, "text/csv"
            elif ext == "txt":
                upload_filename, upload_type = filename, "text/plain"
            else:
                base = filename.rsplit(".", 1)[0] or "document"
                upload_filename, upload_type = f"{base}.txt", "text/plain"
        else:
            upload_filename, upload_type = filename, None

        task_id = await self._paperless_upload(upload_filename, file_data, content_type=upload_type)
        if not task_id:
            await self._send(room_id, self.t("upload_failed", name=display_name), reply_to)
            return

        try:
            doc_id = await self._paperless_wait_task(task_id)
        except PaperlessDuplicateError as e:
            await self._send(room_id, self._duplicate_reply(display_name, e), reply_to)
            return
        if not doc_id:
            await self._send(room_id, self.t("ocr_failed", name=display_name), reply_to)
            return

        # Everything past this line runs with a Paperless-filed doc. The
        # mirror is reached unconditionally before we return: classification
        # and reformat are *enrichment*, not gates, so a flaky LLM reduces
        # the richness of the mirror entry rather than dropping it entirely.

        link = f"{self.paperless_public_url}/documents/{doc_id}/details" if self.paperless_public_url else ""
        doc = await self._paperless_get_doc(doc_id)

        if not doc:
            # Paperless accepted the upload but we can't read the doc back.
            # Rare — still mirror a minimal entry so Paperless ⇄ mirror stay 1:1.
            await self._safe_mirror(
                doc_id=doc_id, classification={}, body_text="",
                processing="ocr", model=None,
                fallback_title=display_name, paperless_tags=[],
            )
            await self._send(room_id, self.t("filed_no_details", name=display_name, link=link), reply_to)
            return

        ocr_text = doc.get("content", "") or ""
        has_text = len(ocr_text.strip()) >= 10

        # ── Enrichment state ─────────────────────────────────────────────
        # Populated when classification succeeds, untouched otherwise so
        # the mirror and chat-reply branches read the same outcome.
        classification: dict = {}
        llm_error: tuple[str, dict] | None = None
        resolved_topics: list[str] = []
        resolved_persons: list[str] = []
        resolved_correspondent: str | None = None
        resolved_type: str | None = None
        summary: list[str] = []
        created_new: list[str] = []
        updates: dict = {}
        title: str | None = None

        # ── Classify (only if enabled and we have text) ──────────────────
        if self.classify_enabled and has_text:
            tags = await self._paperless_get_tags()
            doc_types = await self._paperless_get_doc_types()
            correspondents = await self._paperless_get_correspondents()

            try:
                classification = await self._classify(ocr_text, tags, doc_types, correspondents)
            except LLMUnavailableError:
                llm_error = ("llm_unavailable",
                             {"name": display_name, "url": self.openai_url, "link": link})
            except LLMModelNotFoundError as e:
                llm_error = ("llm_model_missing",
                             {"name": display_name, "model": str(e), "link": link})
            except LLMTimeoutError:
                llm_error = ("llm_timeout",
                             {"name": display_name, "link": link})

        # ── Apply classification to Paperless (if we got one) ────────────
        if classification:
            title = classification.get("title")
            if title and isinstance(title, str):
                updates["title"] = title[:MAX_TITLE_LENGTH]

            # Topic tags — LLM returns 1-2 topics; match_topics splits
            # against existing tags (ignoring "Person: " ones) and flags
            # the rest for creation.
            tag_ids = list(doc.get("tags", []))
            category_tags_dict = {t: tags[t] for t in tags if not t.startswith("Person: ")}
            topics_raw = classification.get("topics") or classification.get("topic")
            matched_topics, new_topics = match_topics(topics_raw, category_tags_dict)
            for mt in matched_topics:
                tag_ids.append(tags[mt])
                resolved_topics.append(mt)
                summary.append(self.t("category", value=mt))
            for nt in new_topics:
                new_id = await self._paperless_create_tag(nt, "#4caf50")
                if new_id:
                    tag_ids.append(new_id)
                    resolved_topics.append(nt)
                    summary.append(self.t("category_new", value=nt))
                    created_new.append(f"tag \"{nt}\"")

            # Person tags — closed set, never create. match_persons handles
            # strings, lists, full names, and "Person: X" prefixes.
            persons_raw = classification.get("persons") or classification.get("person")
            person_tags_matched = match_persons(persons_raw, tags)
            for pt in person_tags_matched:
                tag_ids.append(tags[pt])
                name = pt.replace("Person: ", "")
                resolved_persons.append(name)
                summary.append(self.t("person", value=name))

            if tag_ids:
                updates["tags"] = list(set(tag_ids))

            # Document type — respect a manual type; otherwise apply/create.
            doc_type = classification.get("document_type")
            if not _is_empty(doc_type):
                existing_type = doc.get("document_type")
                if existing_type:
                    summary.append(self.t("type", value=doc_type))
                else:
                    matched = fuzzy_match_entity(doc_type, doc_types)
                    if matched:
                        updates["document_type"] = doc_types[matched]
                        resolved_type = matched
                        summary.append(self.t("type", value=matched))
                    else:
                        new_id = await self._paperless_create_doc_type(doc_type)
                        if new_id:
                            updates["document_type"] = new_id
                            resolved_type = doc_type
                            summary.append(self.t("type_new", value=doc_type))
                            created_new.append(f"document type \"{doc_type}\"")

            # Correspondent — always overwrite Paperless's auto-classifier
            # guess; the LLM has read the actual text and knows better.
            correspondent = classification.get("correspondent")
            if not _is_empty(correspondent):
                matched = fuzzy_match_entity(correspondent, correspondents)
                if matched:
                    updates["correspondent"] = correspondents[matched]
                    resolved_correspondent = matched
                    summary.append(self.t("from", value=matched))
                else:
                    new_id = await self._paperless_create_correspondent(correspondent)
                    if new_id:
                        updates["correspondent"] = new_id
                        resolved_correspondent = correspondent
                        summary.append(self.t("from_new", value=correspondent))
                        created_new.append(f"correspondent \"{correspondent}\"")

            date = classification.get("date")
            if date and isinstance(date, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', date):
                updates["created"] = date
                summary.append(self.t("date", value=date))

            if updates:
                await self._paperless_update_doc(doc_id, updates)

        # ── Reformat (only when we had a classification, and only for
        #               non-text files — a .md is already clean, reformatting
        #               would re-LLM content that's already in its final shape).
        await self._set_typing(room_id)
        reformat_failed = False
        formatted: str | None = None
        if classification and self.reformat_enabled and not is_text:
            formatted = await self._reformat(ocr_text)
            if formatted:
                await self._paperless_update_doc(doc_id, {"content": formatted})
            else:
                reformat_failed = True

        # ── Mirror to Forgejo (always, when configured) ─────────────────
        # Runs before the chat reply so failure-path replies are still
        # preceded by a committed mirror entry. Merging resolved_* into
        # `enriched` keeps the mirror frontmatter in lockstep with the
        # tag names actually written to Paperless.
        #
        # For text files the mirror body is the original bytes decoded —
        # preserves the source exactly (markdown stays markdown, JSON stays
        # JSON) instead of whatever Paperless's text parser produced.
        # `processing` describes the provenance of `body_text`:
        #   ai_formatted — LLM reformat rewrote the body into clean markdown
        #   ocr          — Paperless's OCR output, unchanged
        #   original     — original bytes of a text-like file (markdown,
        #                  JSON, YAML, ...) — no transformation applied
        # Classification-ran-or-not is orthogonal: `topics`, `persons`,
        # `correspondent`, `document_type` reflect what the LLM decided,
        # independent of whether the body was rewritten.
        if is_text:
            try:
                body_text = file_data.decode("utf-8")
            except UnicodeDecodeError:
                body_text = file_data.decode("utf-8", errors="replace")
            processing = "original"
            model = None
        else:
            body_text = formatted or ocr_text
            processing = "ai_formatted" if formatted else "ocr"
            try:
                model = resolve_model(f"{self.name}/reformat") if formatted else None
            except ValueError:
                model = None
        enriched = dict(classification) if classification else {}
        enriched["topics"] = resolved_topics
        enriched["persons"] = resolved_persons
        enriched["correspondent"] = resolved_correspondent
        enriched["document_type"] = resolved_type
        paperless_tags = [
            *resolved_topics,
            *(f"Person: {p}" for p in resolved_persons),
        ]
        await self._safe_mirror(
            doc_id=doc_id,
            classification=enriched,
            body_text=body_text,
            processing=processing,
            model=model,
            fallback_title=display_name,
            paperless_tags=paperless_tags,
        )

        # ── Chat reply ───────────────────────────────────────────────────
        # Priority order:
        #   LLM error > no-text > classify-disabled > classify-returned-nothing > happy path
        if llm_error:
            key, kwargs = llm_error
            await self._send(room_id, self.t(key, **kwargs), reply_to)
        elif not has_text:
            await self._send(room_id, self.t("filed_no_text", name=display_name, link=link), reply_to)
        elif not self.classify_enabled:
            await self._send(room_id, f"{self.t('filed', title=display_name)}\n\n  {link}", reply_to)
        elif not classification:
            await self._send(room_id, self.t("classify_failed", name=display_name, link=link), reply_to)
        else:
            # Happy path — rich summary. Layout optimised for scanning in
            # Element:
            #
            #   Filed: Cursor - Pro Subscription USD 192.00 (#10)
            #
            #   Subscription | Invoice | Cursor | 2025-03-27
            #
            #   Summary text from LLM...
            #
            #   - Invoice number: 4182A976 0001
            #   - Amount due: USD 192.00
            #
            #   Payment of USD 192.00 due (due 2025-03-27)
            #
            #   http://...

            display_title = title or display_name
            lines = [self.t("filed", title=display_title, doc_id=doc_id)]

            # Compact metadata line: topic | type | from | date
            meta_parts = []
            for s in summary:
                value = s.split(": ", 1)[-1] if ": " in s else s
                meta_parts.append(value)
            if meta_parts:
                lines.extend(["", "  " + " | ".join(meta_parts)])

            doc_summary = classification.get("summary", "")
            if doc_summary and isinstance(doc_summary, str):
                lines.extend(["", f"  {doc_summary}"])

            facts = classification.get("facts", [])
            if facts and isinstance(facts, list):
                fact_lines = [f for f in facts if isinstance(f, str) and f.strip()]
                if fact_lines:
                    lines.append("")
                    for f in fact_lines[:5]:
                        lines.append(f"  - {f}")

            action_items = classification.get("action_items", [])
            if action_items and isinstance(action_items, list):
                valid_actions = [a for a in action_items if isinstance(a, dict) and a.get("action")]
                if valid_actions:
                    lines.append("")
                    for a in valid_actions[:3]:
                        due = a.get("due", "")
                        due_str = f" (due {due})" if due else ""
                        lines.append(f"  {a['action']}{due_str}")

            if created_new:
                lines.extend(["", f"  {self.t('new_in_paperless', items=', '.join(created_new))}"])

            if reformat_failed:
                lines.extend(["", f"  {self.t('reformat_failed')}"])

            if link:
                lines.extend(["", f"  {link}"])

            await self._send(room_id, "\n".join(lines), reply_to)

        logger.info("[archivist] Processed: {} → doc {} [{}]",
                     filename, doc_id, ", ".join(summary) or "no-classification")

        # ── Structured event — only when we have classification data ─────
        # Element ignores unknown event types, so the event rides next to
        # the human reply without showing up in chat. Skip when
        # classification is empty so downstream bots don't see bare events.
        if classification:
            event_payload = build_document_event(
                doc_id, classification,
                resolved_topics=resolved_topics,
                resolved_persons=resolved_persons,
                resolved_correspondent=resolved_correspondent,
                resolved_type=resolved_type,
                paperless_url=self.paperless_public_url or self.paperless_url,
            )
            await self.emit_event(room_id, event_payload["type"], event_payload["body"])

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
