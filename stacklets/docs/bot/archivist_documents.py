"""archivist_documents: a document through Paperless, and its reply.

    documents = ArchivistDocuments(bot, rooms=rooms, services=lambda: services)
    await documents.file(room_id, "Zeugnis.pdf", "Zeugnis", pdf_bytes, reply_to,
                         ctx=ctx, submitter_mxid="@marge:simpson")
    await documents.file_url(room_id, "https://example.org/rechnung.pdf", reply_to, ctx=ctx)
    await documents.document_link(42)       # "/go/card/<card id>" or "/go/docs/42"

The documents room feeds Paperless. A file, a scan of several pages and
a link to a PDF or a Google Doc all end in `file`: the document pipeline
(`document_pipeline.py`) uploads, waits for the OCR, classifies, files
the result into the memory and hands back a `FilingOutcome`. This module
maps that outcome to the answer in the room:

    upload_failed / ocr_failed       ❌ and one line
    duplicate                        ✅ and a link to the document on file
    filed_no_details                 ✅ and the link
    enriched                         ✅ and the filed summary with its envelope,
                                     or why it has no tags (no text, no AI,
                                     the AI failed)

The reply that shows a classification carries the `document.filed`
envelope, so a reply in its thread is a correction
(`archivist_corrections.py`).

Links to a document go through `/go/docs/<n>` or the card's `/go/card/`
link, which re-resolve at click time, never to a Paperless URL.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Callable

import aiohttp
from loguru import logger

from stack.bot import CHECK, CROSS, RoomContext
from stack.links import go_document, public

from .document_pipeline import FilingOutcome
from .pipeline import PaperlessDuplicateError
from .reply_presenter import render_filing_reply
from .text_utils import google_docs_export_url

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .archivist_services import ArchivistServices

__all__ = ["SUPPORTED_MSGTYPES", "ArchivistDocuments", "event_date"]


# The Matrix message types the archivist files as a document or capture.
SUPPORTED_MSGTYPES = {"m.file", "m.image"}

# Keyed by the doc type `google_docs_export_url` reports, for the "downloading
# a Google Doc" line and the filename it lands under.
_GOOGLE_TYPE_LABELS = {
    "document": "Google Doc",
    "spreadsheets": "Google Sheet",
    "presentation": "Google Slides",
}


class ArchivistDocuments:
    """Files a document in Paperless and answers in the room.

        documents = ArchivistDocuments(bot, rooms=rooms, services=lambda: services)
        await documents.file(room_id, "scan.pdf", "scan (3 pages)", pdf, reply_to, ctx=ctx)
        # ✅ on the message and the filed summary as a reply
    """

    def __init__(self, bot, *, rooms, services: Callable[[], "ArchivistServices"]):
        self._bot = bot
        self._rooms = rooms
        self._services = services

    async def _duplicate_reply(self, name: str, e: PaperlessDuplicateError) -> str:
        """Render the 'already filed' chat reply for a Paperless duplicate.

        Points the user at the original doc's card so they can verify the
        match instead of wondering why the upload 'failed'.
        """
        link = await self.document_link(e.doc_id) if e.doc_id else ""
        return self._bot.t("already_filed",
                      name=name,
                      doc_id=e.doc_id if e.doc_id is not None else "?",
                      title=e.title or "(no title)",
                      link=link)


    async def document_link(self, doc_id: int) -> str:
        """The chat link of Paperless document `doc_id`: its card's
        `/go/card/<card id>` when the memory has one, else `/go/docs/<n>`.

        A reply that knows the document only by its number asks the memory
        for its card, so it links what a filing reply links.
        """
        card_id = await self._services().mirror.document_card_id(doc_id)
        return public(go_document(doc_id, card_id), self._bot.link_base_url)


    async def file(
        self, room_id: str, filename: str, display_name: str,
        file_data: bytes, reply_to: str | None = None,
        date_filed: str | None = None,
        submitter_mxid: str | None = None,
        user_hint: str | None = None,
        extra_tags: list[str] | None = None,
        note_prefix: str | None = None,
        *, ctx: RoomContext,
    ):
        """File a document via the pipeline, then render the reply.

        Shared by all entry points: single file upload, multi-page scan,
        and URL archiving. The DocumentPipeline does the work (upload →
        OCR → classify → reformat → mirror → emit) and hands back a
        FilingOutcome; this maps the outcome to a chat reply and logs.

        `extra_tags` and `note_prefix` let a caller stamp flat provenance
        onto the filed doc *after* classification (source-archive filings
        pass the source type and header). Applied best-effort: an enrichment
        failure never fails a filing that otherwise succeeded.
        """
        outcome = await self._services().pipeline.process(
            filename=filename, display_name=display_name, file_data=file_data,
            date_filed=date_filed, submitter_mxid=submitter_mxid,
            user_hint=user_hint,
            write_in=self._rooms.write_in(ctx),
        )
        if outcome.doc_id:
            for tag in extra_tags or []:
                await self._services().paperless.ensure_doc_tag(outcome.doc_id, tag)
            if note_prefix:
                await self._services().paperless.add_note(outcome.doc_id, note_prefix)
        await self.reply(room_id, outcome, reply_to)
        if outcome.status == "enriched":
            processed_parts = [*outcome.resolved_topics, *outcome.resolved_persons]
            if outcome.resolved_type:
                processed_parts.append(outcome.resolved_type)
            if outcome.resolved_correspondent:
                processed_parts.append(outcome.resolved_correspondent)
            logger.info(
                "[archivist] Processed: {} → doc {} [{}]",
                filename, outcome.doc_id,
                ", ".join(processed_parts) or "no-classification",
            )


    async def reply(
        self, room_id: str, o: FilingOutcome, reply_to: str | None,
    ) -> None:
        """Map a FilingOutcome to a chat reply.

        Terminal statuses get a one-line message. The `enriched` status
        runs the historic priority chain: llm-error > no-text >
        no AI > classify-disabled > no-classification > the rich filed summary.
        That chain depends on chat-only inputs (openai_url, the
        translator) so it lives here, not in the pipeline.
        """
        # Terminal glyph alongside the 👀: ❌ when nothing was filed
        # (upload/OCR failed), ✅ otherwise (filed, even if classification
        # was partial or the document was already on file).
        if o.status in ("upload_failed", "ocr_failed"):
            await self._bot._react(room_id, reply_to, CROSS)
        else:
            await self._bot._react(room_id, reply_to, CHECK)

        if o.status == "enriched":
            text, metadata = self._enriched_reply(o)
        else:
            text, metadata = await self._terminal_reply(o), None
        if metadata is None:
            await self._bot._answer(room_id, text, reply_to)
        else:
            await self._bot._answer(room_id, text, reply_to, metadata=metadata)

    async def _terminal_reply(self, o: FilingOutcome) -> str:
        """The one line for a filing that stopped before classification."""
        if o.status == "upload_failed":
            return self._bot.t("upload_failed", name=o.display_name)
        if o.status == "duplicate":
            return await self._duplicate_reply(o.display_name, o.duplicate)
        if o.status == "ocr_failed":
            return self._bot.t("ocr_failed", name=o.display_name)
        return self._bot.t("filed_no_details", name=o.display_name, link=o.link)

    def _enriched_reply(self, o: FilingOutcome) -> tuple[str, dict | None]:
        """The reply to a filed document, and the metadata it carries.

        A failed classification carries the filing's envelope too, so a
        reply in its thread classifies the document again.
        """
        filing = {"dev.famstack.event": o.envelope} if o.envelope else None
        t = self._bot.t
        llm_error = _llm_error_for_chat(
            o.llm_error, name=o.display_name, openai_url=self._bot.openai_url, link=o.link,
        )
        if llm_error:
            key, kwargs = llm_error
            return t(key, **kwargs), filing
        if not o.has_text:
            return t("filed_no_text", name=o.display_name, link=o.link), None
        if not o.classify_enabled and self._bot.ai_off:
            # The stack runs without an AI for now, so the reply says why
            # the letter has no tags and who can set the AI up.
            return t("filed_without_ai", title=o.display_name, doc_id=o.doc_id, link=o.link), None
        if not o.classify_enabled:
            return f"{t('filed', title=o.display_name, doc_id=o.doc_id)}\n\n  {o.link}", None
        if not o.classification:
            return t("classify_failed", name=o.display_name, link=o.link), filing
        # The `dev.famstack.event` envelope rides on the visible
        # message: one replayable timeline event per filing.
        return self._filed_summary(o), {"dev.famstack.event": o.envelope}

    def _filed_summary(self, o: FilingOutcome) -> str:
        """The rich reply to a classified filing: title, people, type, link."""
        return render_filing_reply(
            self._bot.t,
            display_title=o.classification.get("title") or o.display_name,
            doc_id=o.doc_id,
            resolved_persons=o.resolved_persons,
            resolved_type=o.resolved_type,
            resolved_correspondent=o.resolved_correspondent,
            date_applied=o.date_applied,
            classification=o.classification,
            created_new=o.created_new,
            reformat_failed=o.reformat_failed,
            link=o.link,
            language=self._bot.language,
        )

    # ── URL archiving (documents room, feeds Paperless) ──────────────────

    async def file_url(
        self, room_id: str, url: str, reply_to: str | None = None,
        *, ctx: RoomContext, date_filed: str | None = None,
        submitter_mxid: str | None = None,
    ):
        """Download a link and file it as a document when it is a PDF.

        A Google Docs, Sheets or Slides link is exported as PDF. Every
        failure (HTTP error, timeout, empty, not a PDF) is answered in
        the chat and files nothing.
        """
        google_export = google_docs_export_url(url)
        doc_type = ""
        if google_export:
            download_url, doc_type = google_export
            await self._bot._send(room_id, self._bot.t("downloading_google", type=_GOOGLE_TYPE_LABELS.get(doc_type, "Google Doc")), reply_to)
        else:
            download_url = url
            await self._bot._send(room_id, self._bot.t("downloading_url"), reply_to)

        downloaded = await self._download(room_id, download_url, reply_to)
        if downloaded is None:
            return
        file_data, content_type = downloaded
        names = _pdf_names(url, doc_type if google_export else None, content_type, file_data)
        if names is None:
            await self._bot._send(room_id, self._bot.t("url_not_pdf", content_type=content_type), reply_to)
            return
        filename, display_name = names
        await self.file(
            room_id, filename, display_name, file_data, reply_to,
            ctx=ctx, date_filed=date_filed,
            submitter_mxid=submitter_mxid,
        )

    async def _download(
        self, room_id: str, url: str, reply_to: str | None,
    ) -> tuple[bytes, str] | None:
        """The link's bytes and content type, or None after saying in the
        chat why the download failed."""
        try:
            async with self._bot._ensure_http().get(url, timeout=aiohttp.ClientTimeout(total=60), allow_redirects=True) as resp:
                if resp.status != 200:
                    await self._bot._send(room_id, self._bot.t("url_http_error", status=resp.status), reply_to)
                    return None
                file_data = await resp.read()
                content_type = resp.content_type or ""
        except asyncio.TimeoutError:
            await self._bot._send(room_id, self._bot.t("url_timeout"), reply_to)
            return None
        except aiohttp.ClientError as e:
            await self._bot._send(room_id, self._bot.t("url_error", error=str(e)), reply_to)
            return None

        if not file_data:
            await self._bot._send(room_id, self._bot.t("url_empty"), reply_to)
            return None
        return file_data, content_type


# ── Helpers ─────────────────────────────────────────────────────────────────


def _pdf_names(
    url: str, google_type: str | None, content_type: str, file_data: bytes,
) -> tuple[str, str] | None:
    """The file name and the display name of a downloaded PDF, or None
    when the download is not a PDF.

        _pdf_names("https://duff.example/bill.pdf?x=1", None, "", b"")
        # ("bill.pdf", "bill.pdf")
    """
    if google_type is not None:
        return f"google-{google_type}.pdf", _GOOGLE_TYPE_LABELS.get(google_type, "Google Doc")
    if "pdf" in content_type or url.lower().endswith(".pdf"):
        url_path = url.split("?")[0].split("#")[0]
        filename = url_path.rsplit("/", 1)[-1] if "/" in url_path else "document.pdf"
        if not filename.lower().endswith(".pdf"):
            filename = "document.pdf"
        return filename, filename
    if file_data[:5] == b'%PDF-':
        return "document.pdf", "document.pdf"
    return None


def event_date(event) -> str | None:
    """YYYY-MM-DD from a matrix-nio event's `server_timestamp`.

    Returned date is UTC. Used as the LLM's anchor for resolving
    partial dates on the document (the doc itself usually carries
    a date close to when the user uploaded it). Returns None when
    the event lacks a usable timestamp; the caller falls back to the
    system date.

        event_date(event)   # "2026-11-17" for an event sent that day
    """
    ts_ms = getattr(event, "server_timestamp", None)
    if not isinstance(ts_ms, (int, float)) or ts_ms <= 0:
        return None
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).date().isoformat()


def _llm_error_for_chat(
    pipeline_error: tuple[str, str] | None,
    *, name: str, openai_url: str, link: str,
) -> tuple[str, dict] | None:
    """Map a pipeline llm_error tuple to (translation-key, format-kwargs).

    The pipeline speaks in transport terms ("unavailable", "model_missing",
    "timeout"); the chat reply needs translation keys that already know
    how to render the document's name and a link back to Paperless.
    Returns None when there was no error.

        >>> _llm_error_for_chat(("timeout", ""), name="Zeugnis", openai_url="", link="")
        ('llm_timeout', {'name': 'Zeugnis', 'link': ''})
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
