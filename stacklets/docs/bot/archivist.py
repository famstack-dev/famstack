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
from pypdf import PdfReader

from git_mirror import GitMirror
from matching import build_document_event
from microbot import MicroBot
from capabilities import ModelCapabilities
from pipeline import (
    Classifier,
    DEFAULT_CLASSIFY_MAX_CHARS,
    EnrichResult,
    PaperlessAPI,
    PaperlessDuplicateError,
    enrich_document,
    reformat_document,
)
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

def _llm_error_for_chat(
    pipeline_error: tuple[str, str] | None,
    *, name: str, openai_url: str, link: str,
) -> tuple[str, dict] | None:
    """Map a pipeline llm_error tuple to (translation-key, format-kwargs).

    The pipeline speaks in transport terms ("unavailable", "model_missing",
    "timeout"); the chat reply needs translation keys that already know
    how to render the document's name and a link back to Paperless.
    Returns None when there was no error.
    """
    if not pipeline_error:
        return None
    kind, detail = pipeline_error
    if kind == "unavailable":
        return ("llm_unavailable", {"name": name, "url": openai_url, "link": link})
    if kind == "model_missing":
        return ("llm_model_missing", {"name": name, "model": detail, "link": link})
    if kind == "timeout":
        return ("llm_timeout", {"name": name, "link": link})
    return None


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


def _has_pdf_text_layer(file_data: bytes) -> bool:
    """Best-effort check: does this PDF carry an embedded text layer?

    Native-text PDFs (contracts, invoices generated by software, research
    papers) return True. Scanned PDFs built from photographed pages, and
    the archivist's own scan-mode-generated PDFs, return False. Any
    parsing error is logged at debug level and treated as 'no text
    layer' so reformat still runs: this is best-effort assistance, not
    a gate on correctness.

    Only the first three pages are inspected. Text layers are either
    present throughout or absent throughout in practice, and sampling
    keeps the check cheap on a 70-page paper.
    """
    try:
        reader = PdfReader(io.BytesIO(file_data))
    except Exception as e:
        logger.debug("[archivist] pdf text-layer check failed: {}", e)
        return False
    for page in reader.pages[:3]:
        try:
            if page.extract_text().strip():
                return True
        except Exception:
            continue
    return False


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
        # Max OCR chars the classifier sees. The default is generous so
        # most docs classify on their full content; bump it in bot.toml
        # when running a larger-context model.
        self.classify_max_chars = int(settings.get(
            "classify_max_chars", DEFAULT_CLASSIFY_MAX_CHARS,
        ))
        # Mirror is opt-in while we validate the invariant in real use.
        # Flip `mirror_to_git = true` in bot.toml to enable.
        self.mirror_to_git = settings.get("mirror_to_git", False)
        self.mirror_org = settings.get("mirror_org", "family")
        self._scan_sessions: dict[str, dict] = {}
        self._http: aiohttp.ClientSession | None = None
        self._paperless: PaperlessAPI | None = None
        self._classifier: Classifier | None = None
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
        if not self.mirror_to_git:
            logger.info("[archivist] Git mirror (BETA): disabled. "
                         "Flip `mirror_to_git = true` in stacklets/docs/bot/bot.toml to enable.")
        try:
            default_model = resolve_model(f"{self.name}/classifier")
            logger.info("[archivist] Model (classifier): {}", default_model)
        except ValueError as e:
            logger.warning("[archivist] {}", e)

        self._http = aiohttp.ClientSession()
        self._paperless = PaperlessAPI(self._http, self.paperless_url, self.paperless_token)
        # Vision-capability cache lives in the bot's data dir so a probe
        # done in one container restart isn't repeated by the next one.
        self._classifier = Classifier(
            self._http, self.openai_url, self.openai_key, bot_name=self.name,
            capabilities=ModelCapabilities(
                path=self._session_dir / "model-capabilities.json",
            ),
        )
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
        summary: str | None = None,
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
                summary=summary,
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
        """Called after initial sync — send welcome message to rooms.

        Also kicks off a background vision-capability probe so the cache
        is warm before the first user upload. Fire-and-forget: probe
        failures already log + degrade to text-only inside has_vision().
        """
        url = self.paperless_public_url or self.paperless_url
        welcome = self.t("welcome", url=url, ai_status=self._ai_status())
        for room_id in self._client.rooms:
            await self._send(room_id, welcome)
        if self.classify_enabled and self.openai_url:
            asyncio.create_task(self._classifier.has_vision())

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
        # Image extensions get the multimodal classify path when the
        # model has vision — the binary rides alongside the OCR text as
        # supplementary context. PDFs are deliberately excluded for now
        # (rendering pages to images would need a new system dep).
        IMAGE_EXTS = ("png", "jpg", "jpeg", "webp", "gif")
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        is_text = ext in TEXT_LIKE
        is_image = ext in IMAGE_EXTS
        # A PDF that already carries an embedded text layer is, by
        # definition, readable without OCR. Reformat's job is cleaning up
        # OCR artifacts; running it on a native-text PDF risks degrading
        # already-clean content and costs minutes of generation on long
        # papers. Scan-mode PDFs (built from photographed pages) have no
        # text layer and still reformat normally.
        is_pdf_with_text = ext == "pdf" and _has_pdf_text_layer(file_data)
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

        task_id = await self._paperless.upload(upload_filename, file_data, content_type=upload_type)
        if not task_id:
            await self._send(room_id, self.t("upload_failed", name=display_name), reply_to)
            return

        try:
            doc_id = await self._paperless.wait_task(task_id)
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
        doc = await self._paperless.get_doc(doc_id)

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

        # ── Enrich via the shared pipeline ───────────────────────────────
        # Pipeline hands back structured data; this function renders it for
        # Matrix. When classification is disabled or the doc has no text,
        # we skip the LLM call entirely by handing back an empty result.
        if self.classify_enabled and has_text:
            # Image uploads carry the raw bytes alongside OCR text — the
            # classifier only attaches the image when the model has
            # vision capability (lazily probed, cached on disk). For
            # PDFs and text uploads `image_data` stays None, so the
            # classifier takes the historic text-only path.
            image_data = file_data if is_image else None
            image_mime = mime_type if is_image else None
            result = await enrich_document(
                paperless=self._paperless,
                classifier=self._classifier,
                doc=doc,
                classify_max_chars=self.classify_max_chars,
                image_data=image_data,
                image_mime=image_mime,
            )
        else:
            result = EnrichResult()

        classification = result.classification
        resolved_topics = result.resolved_topics
        resolved_persons = result.resolved_persons
        resolved_correspondent = result.resolved_correspondent
        resolved_type = result.resolved_type
        created_new = result.created_new
        llm_error = _llm_error_for_chat(
            result.llm_error,
            name=display_name, openai_url=self.openai_url, link=link,
        )

        # ── Reformat (only when we had a classification, and only for
        #               non-text files — a .md is already clean, reformatting
        #               would re-LLM content that's already in its final shape.
        #               PDFs with an embedded text layer are treated the same
        #               way: Paperless already extracted clean text from them,
        #               so rerunning the content through the reformat prompt
        #               can only dilute or truncate it).
        await self._set_typing(room_id)
        reformat_failed = False
        formatted: str | None = None
        if classification and self.reformat_enabled and not is_text and not is_pdf_with_text:
            formatted = await reformat_document(
                paperless=self._paperless,
                classifier=self._classifier,
                doc_id=doc_id,
                ocr_text=ocr_text,
            )
            if not formatted:
                reformat_failed = True
        elif is_pdf_with_text and classification:
            logger.info(
                "[archivist] reformat skipped for doc #{}: PDF with embedded text layer",
                doc_id,
            )

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
            summary=result.summary,
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

            title = classification.get("title")
            display_title = title or display_name
            lines = [self.t("filed", title=display_title, doc_id=doc_id)]

            # Compact metadata line: topic(s) | person(s) | type | from | date.
            # Built from EnrichResult.resolved_* so the values on screen match
            # exactly what was written to Paperless — no translation-key
            # round-trip needed.
            meta_parts: list[str] = []
            meta_parts.extend(resolved_topics)
            meta_parts.extend(resolved_persons)
            if resolved_type:
                meta_parts.append(resolved_type)
            if resolved_correspondent:
                meta_parts.append(resolved_correspondent)
            date_applied = result.updates_applied.get("created")
            if date_applied:
                meta_parts.append(date_applied)
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

        processed_parts = [*resolved_topics, *resolved_persons]
        if resolved_type:
            processed_parts.append(resolved_type)
        if resolved_correspondent:
            processed_parts.append(resolved_correspondent)
        logger.info("[archivist] Processed: {} → doc {} [{}]",
                     filename, doc_id, ", ".join(processed_parts) or "no-classification")

        # ── Structured event — always, once Paperless has the doc ───────
        # Element ignores unknown event types, so the event rides next to
        # the human reply without showing up in chat. Fires symmetrically
        # with the mirror: every Paperless-filed doc produces an event,
        # even when classification returned nothing. Downstream bots
        # deciding whether to act on an event filter on the fields they
        # care about — empty `topics` / `correspondent` is a valid
        # "filed-but-uninterpreted" signal, not a reason to hide the
        # event.
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
        results = await self._paperless.search(query)
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
        doc = await self._paperless.get_doc(doc_id)
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
