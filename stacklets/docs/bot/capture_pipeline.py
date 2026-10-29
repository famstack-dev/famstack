"""CapturePipeline — URL/text capture → classify → mirror → outcome.

The capture flow, lifted out of ArchivistBot. Far leaner than the
document pipeline: no Paperless write, no entity reconciliation, no
event envelope — a URL or a pasted note becomes a summarised markdown
entry in the sender's own vault bucket.

Like DocumentPipeline, it does the work (no Matrix, no i18n) and returns
a CaptureOutcome the orchestrator renders. The one mid-flow message —
"fetching …" before a URL fetch — goes through a Notifier.

Capture classification is intentionally ontology-free: tags grow from
the existing capture-tag cache, which a later canonicalisation pass can
fold into the ontology.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import io
import mimetypes
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from urllib.parse import unquote, urlsplit

from loguru import logger

from .document_pipeline import utc_now_isoformat
from .extractors import SourceContent, email_to_source
from .matching import build_capture_event
from .git_mirror import code_commit
from .notifier import Notifier
from .clean_words import is_cleanable, kept, kept_rebuilt, starting_point
from .own_words import in_own_words
from .pdf_analysis import (
    DEFAULT_VISION_MAX_PDF_PAGES,
    has_ocr_text_layer,
    has_text_layer,
    pdf_page_count,
    should_attach_vision,
)
from .pdf_render import render_pages
from .pipeline import (
    ImageAttachment,
    LLMModelNotFoundError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from stack import media, resolve_model
from stack.cards.events import Correction, Event, in_order, name_of, text_of
from stack.cards.fields import FieldOp
from stack.cards.history import CardHistory
from stack.cards.lookup import renamed_topic
from stack.cards.sources import (
    NOTE, ORIGINAL, REPLY, Source, SourcesMismatch, corrections_of, new_id, now, read_sidecar,
    sidecar_path, with_quote_edits, words_of,
)
from stack.frontmatter import FrontmatterError
from stack.frontmatter import parse as parse_frontmatter
from stack.lists import list_path
from stack.vault import topic_of

# A note in the format before ADR-018 is the memory stacklet's to read.
from stacklets.memory.note_card import PROMPT_RESIDUE, card_sources

try:
    from pypdf import PdfReader  # type: ignore
except Exception:  # pragma: no cover - runtime dep at bot install time
    PdfReader = None  # type: ignore


IMAGE_MIMES = {
    "image/jpeg", "image/jpg", "image/png", "image/heic", "image/heif",
    "image/webp", "image/tiff",
}

# Files we treat as note-shaped content: the bytes themselves are the
# artifact worth keeping (a Markdown export, a hand-written text file),
# not a pointer or a scan. The body lands in the mirror verbatim so a
# later edit or search hits the actual words. PDFs and images, by
# contrast, stay as bookmarks: the binary lives in Matrix, the wiki
# entry just summarises and links.
TEXT_MIMES = {
    "text/plain", "text/markdown", "text/x-markdown",
}
TEXT_EXTS = {"md", "markdown", "txt"}

# Ratio that the reformatted body must be of the input for the
# reformat to count as a useful rewrite. A well-behaved reformat
# tightens whitespace and stitches broken lines but keeps essentially
# all the words; a result that's a fraction of the input length is
# the LLM losing content (or replying with a fragment like "ok"). The
# threshold is permissive enough to absorb legitimate compression
# (line joins, header normalisation) without letting a half-answer
# replace the raw OCR.
_REFORMAT_MIN_RATIO = 0.5


@dataclass(frozen=True)
class Rebuild:
    """What a note's clean version is rebuilt from (ADR-018 §7): who wrote
    the note, as a name; the clean version the events apply to; the
    corrections and a person's edits, in time order (`stack.cards.events`).

        Rebuild("Homer", "Camping trip next weekend: ...",
                (Correction("homer", "2026-10-20T09:00:00+00:00", "we leave at 5, not 4"),))
    """

    writer: str
    prior: str
    events: tuple[Event, ...]

    def said(self) -> str:
        """Each event's message, one per line, as the model reads it."""
        return "\n".join(e.message() for e in self.events)

    @property
    def edited(self) -> bool:
        """Whether a person edited the clean version by hand."""
        return any(e.edits_the_text for e in self.events)


@dataclass
class CaptureOutcome:
    """What the pipeline produces; the orchestrator renders the reply.

    status: captured | reclassified | extract_failed | no_mirror | empty

    ``transcript`` is populated for voice-memo captures so the reply
    renderer can quote what was heard back to the sender -- the only
    way a user can catch a bad transcription without opening the vault.
    Empty for every other capture shape (PDFs, images, URLs, notes).

    ``failure_reason`` qualifies an ``extract_failed`` status with
    where the failure originated: ``url`` (article body didn't extract),
    ``transcription`` (whisper or the cleanup LLM gave up on a voice
    memo), or ``binary`` (PDF/image couldn't be read). The reply layer
    renders a different message per reason so the user isn't told
    "couldn't read that link" when they sent a voice memo.
    """

    status: str
    classification: dict = field(default_factory=dict)
    source_title_hint: str | None = None
    display_link: str = ""
    vault_path: str | None = None
    envelope: dict | None = None
    transcript: str | None = None
    failure_reason: str | None = None
    # Set when a URL capture filed as a *link card* rather than a summary:
    # the gate's verdict name (`challenge`, `login`, `consent`, `paywall`,
    # `empty`). The entry exists either way -- this is what the reply layer
    # uses to say why there is a link but no summary. None on every other
    # capture, including a URL that read cleanly.
    blocked_reason: str | None = None
    # The vault bucket the capture filed under (`entity_slug`): a topic path
    # like `family/camping` or a bare personal bucket like `homer`. The reply
    # renderer uses it to build a `/go/topic/<scope>/todo` link, but only for
    # topic scopes (those carry a `/`) — a personal bucket has no todos page.
    scope: str | None = None
    # Why a note filed without the clean version the model wrote
    # (`clean_words.kept`), "" when it has one or needed none.
    clean_rejected: str = ""


def _link_card(url: str, user_hint: str | None) -> SourceContent:
    """A `SourceContent` for a page we could not read.

    The body is the sender's own words plus the link, and deliberately
    nothing else. It is tempting to write "this page was blocked" into
    it, but the body is the classifier's input: say that, and the entry
    comes back titled "Blocked page" instead of "camping gear list".
    Why it was blocked belongs in the chat reply, not in the vault.

    The title hint falls back through the same reasoning -- the sender's
    first line, then the URL's last path segment, then the host. Each is
    a worse guess than the one before it, and all three beat a summary
    of a cookie banner.
    """
    hint = (user_hint or "").strip()
    split = urlsplit(url)
    slug = [p for p in split.path.split("/") if p]
    title = None
    if hint:
        title = hint.splitlines()[0].strip()[:120]
    elif slug:
        title = unquote(slug[-1]).replace("-", " ").replace("_", " ").strip() or None
    if not title:
        title = split.netloc

    body = f"{hint}\n\n{url}" if hint else url
    return SourceContent(
        text=body, mime="text/markdown", title_hint=title, source_uri=url,
    )


class CapturePipeline:
    """Captures a URL (bookmark) or pasted text (note) into the vault."""

    def __init__(
        self, *,
        url_extractor,
        text_extractor,
        classifier,
        mirror,
        capture_tags,
        paperless,
        bot_name: str,
        classify_max_chars: int,
        capture_keep_body: bool,
        capture_tag_prompt_size: int,
        vision_max_pdf_pages: int = DEFAULT_VISION_MAX_PDF_PAGES,
        llm=None,
        vault=None,
    ):
        self._url_extractor = url_extractor
        # The memory vault, for the person files. None in callers
        # that run without one; the Person tags stand in for them then.
        self._vault = vault
        self._text_extractor = text_extractor
        self._classifier = classifier
        self._mirror = mirror
        self._capture_tags = capture_tags
        self._paperless = paperless
        self._name = bot_name
        self.classify_max_chars = classify_max_chars
        self.capture_keep_body = capture_keep_body
        self.capture_tag_prompt_size = capture_tag_prompt_size
        self.vision_max_pdf_pages = vision_max_pdf_pages
        self._llm = llm

    async def capture_url(
        self, *, url: str, sender_mxid: str, notifier: Notifier,
        capture_id: str | None = None,
        seed_topics: list[str] | None = None,
        bucket: str | None = None,
        user_hint: str | None = None,
        write_in: str | None = None,
    ) -> CaptureOutcome:
        """Fetch a URL and file it as a bookmark.

        The body is dropped by default (the URL + summary IS the entry)
        unless `capture_keep_body` is set.

        ``seed_topics`` are tags the caller guarantees on this capture
        regardless of what the classifier returns -- the topic-rooms
        contract (see docs/design/brain/topic-rooms.md). The pipeline
        prepends them to the classifier's tag list and dedupes; the
        mirror, the capture-tag cache, and the envelope all see the
        merged list.

        ``bucket`` overrides the sender-derived entity routing. Used
        by topic-room captures so a memo in #Thema:Camping files
        under ``camping/`` (shared) or ``homer/camping/`` (personal)
        instead of the sender's default personal bucket. The classifier
        still sees the sender's name in ``persons``; only the path
        changes.

        ``user_hint`` is the surrounding chat text that came with the
        URL ("Interesting facts:", "look at this gear list"). The
        classifier prompt's user-clarification block surfaces it so
        the generated title and summary reflect the framing the user
        actually wrote, not just whatever the article extractor pulled
        out. Empty/None leaves the prompt unchanged.

        ``write_in`` is the language the room chose for its captures
        (`!config language`); None is the household language. The same
        holds for every capture entry point below.
        """
        await notifier.acknowledge()
        outcome = await self._url_extractor.fetch(url)

        # A page we could not read still files. Dropping it on the floor
        # was the old behaviour and it lost two things worth keeping: the
        # link itself, and whatever the sender wrote around it -- which is
        # often the more useful half ("gear list for the camping trip").
        # The gate's verdict rides along so the reply can name the
        # obstacle instead of guessing.
        blocked_reason = None
        source = outcome.content
        if source is None:
            source = _link_card(outcome.url or url, user_hint)
            blocked_reason = outcome.verdict.name

        result = await self._publish(
            source=source, kind="bookmark", sender_mxid=sender_mxid,
            display_link=url, actor=sender_mxid,
            capture_id=capture_id, seed_topics=seed_topics,
            bucket=bucket, user_hint=user_hint, write_in=write_in,
        )
        return replace(result, blocked_reason=blocked_reason) if blocked_reason else result

    async def capture_text(
        self, *, text: str, sender_mxid: str,
        capture_id: str | None = None,
        seed_topics: list[str] | None = None,
        bucket: str | None = None,
        transcribed: bool = False,
        write_in: str | None = None,
        caption: str = "",
        sent_at: str = "",
    ) -> CaptureOutcome:
        """File a message body as a note. Nothing is fetched — the text is
        the source; TextExtractor surfaces any embedded URL as the link.
        The body is always kept (those are the exact words sent).

        ``seed_topics`` and ``bucket`` carry the topic-room guarantees
        through; see ``capture_url``.

        ``transcribed`` marks a body that whisper produced rather than a
        keyboard. It decides whether the reply quotes the words back, the
        sender's one chance to catch a mishearing, and counts the note as
        the speaker's own words, the only notes that become todos (see
        `own_words`).

        ``caption`` is what the sender wrote with a recording, handled as
        an upload's caption is (`capture_binary`).

        A note of up to `clean_words.CLEAN_MAX_CHARS` is filed as a clean
        version of its words, with the words after it; a longer one, which
        is usually pasted from elsewhere, gets a summary.

        ``sent_at`` is when the message was sent (ISO 8601), the time its
        source records; now when not given.
        """
        source = await self._text_extractor.extract(text)
        if source is None:
            return CaptureOutcome(status="empty")
        # The footer says where a capture came from. "(pasted text)" is a
        # lie about something somebody said out loud.
        origin = "(voice message)" if transcribed else "(pasted text)"
        return await self._publish(
            source=source, kind="note", sender_mxid=sender_mxid,
            display_link=source.source_uri or origin,
            actor=sender_mxid,
            capture_id=capture_id, seed_topics=seed_topics,
            bucket=bucket, transcribed=transcribed, write_in=write_in,
            extract_action_items=in_own_words(text, spoken=transcribed),
            user_hint=caption or None, caption=caption,
            clean_words=is_cleanable(source.text),
            source_kind="voice" if transcribed else "text", sent_at=sent_at,
        )

    async def capture_email(
        self, *,
        subject: str | None,
        body: str,
        message_id: str | None,
        sender_mxid: str,
        thread_root: str | None = None,
        from_addr: str | None = None,
        captured_at: str | None = None,
        bucket: str | None = None,
        capture_id: str | None = None,
        seed_topics: list[str] | None = None,
    ) -> CaptureOutcome:
        """Fold a fetched email into its thread file.

        Reuses the capture pipeline's classify/tag/envelope tail wholesale:
        the body is the source the classifier reads, the subject the title,
        the *thread root* the file identity (an RFC 2392 ``mid:`` URI). The
        write differs from a URL/note capture — email accumulates, so each
        message folds into one thread file (see `publish_email_message`)
        rather than replacing a single-shot entry. `from_addr` is surfaced
        to the classifier as a hint so it can resolve the correspondent,
        and stamps the message's section heading. Routing — which bucket,
        which room — is the caller's job; here we just fold. The `mail`
        container did the fetch; this is the in-pipeline handoff.
        """
        source = email_to_source(
            subject=subject, body=body, thread_root=thread_root or message_id,
        )
        if not source.text.strip():
            return CaptureOutcome(status="empty")
        user_hint = f"This is an email from {from_addr}." if from_addr else None
        return await self._publish(
            source=source, kind="email", sender_mxid=sender_mxid,
            display_link=source.source_uri or "(email)",
            user_hint=user_hint, actor=sender_mxid, captured_at=captured_at,
            capture_id=capture_id, bucket=bucket, seed_topics=seed_topics,
            email_meta={"message_id": message_id, "from_addr": from_addr},
            # The "sender" of an email is the mail bot, not a household
            # member — don't fall it in as the person.
            default_person=False,
        )

    async def capture_binary(
        self, *,
        file_data: bytes,
        mime: str,
        filename: str,
        source_uri: str | None,
        sender_mxid: str,
        display_link: str | None = None,
        capture_id: str | None = None,
        seed_topics: list[str] | None = None,
        bucket: str | None = None,
        default_person: bool = True,
        kept_media: dict | None = None,
        write_in: str | None = None,
        caption: str = "",
        sent_at: str = "",
    ) -> CaptureOutcome:
        """File a PDF as a bookmark, or a photo as a note.

        Vision-driven by default: render the page(s) and let the LLM
        do extraction + summary + tags in one prompt. Long text-layer
        PDFs (past the per-instance vision cap) bypass vision and
        ride the text layer through the existing text-capture path,
        so the household doesn't pay vision tokens for a 60-page
        research paper. ``source_uri`` is the Matrix mxc URL, so the
        entry links back to the message the upload came in on.

        ``kept_media`` is where the caller archived the bytes, when it
        did. The mxc URL is a pointer into the homeserver's media store
        and nothing promises that store outlives the entry; the archive
        is the copy the wiki serves itself. The caller keeps it because
        it is the one that has the room, the sender and the event this
        upload arrived on, which is what the archive records.

        ``caption`` is what the sender wrote with the upload. The model
        reads it alongside the file, as it reads a note on a filed
        document, and the entry keeps it as written.
        """
        source, images, kind = self._source_from_binary(
            file_data=file_data, mime=mime, filename=filename,
            source_uri=source_uri,
        )
        if source is None:
            return CaptureOutcome(
                status="extract_failed", failure_reason="binary",
            )
        source = self._cap_pdf_body(source)
        source = await self._maybe_reformat_pdf(source, kind)
        return await self._publish(
            source=source, kind=kind, sender_mxid=sender_mxid,
            display_link=display_link or source_uri or filename,
            images=images, actor=sender_mxid,
            user_hint=caption or None, caption=caption,
            capture_id=capture_id, seed_topics=seed_topics,
            bucket=bucket, default_person=default_person,
            kept_media=kept_media, write_in=write_in,
            source_kind="image" if mime.startswith("image/") else "file", sent_at=sent_at,
            media_hash=f"sha256:{hashlib.sha256(file_data).hexdigest()}",
        )

    def _cap_pdf_body(self, source: SourceContent) -> SourceContent:
        """Bound the PDF note body to ``classify_max_chars`` so the mirror
        size stays predictable for very long PDFs.

        Non-PDF sources pass through. PDFs whose text already fits
        below the cap pass through. Only oversized PDFs are truncated;
        the LLM-reformat pass downstream then sees the capped text and
        decides whether to clean it up or fall back to raw.
        """

        if (source.mime != "application/pdf"
                or not source.text
                or len(source.text) <= self.classify_max_chars):
            return source
        return SourceContent(
            text=source.text[: self.classify_max_chars],
            mime=source.mime,
            title_hint=source.title_hint,
            source_uri=source.source_uri,
        )

    async def _maybe_reformat_pdf(
        self, source: SourceContent, kind: str,
    ) -> SourceContent:
        """Clean PDF OCR/pypdf output into readable markdown.

        ``_cap_pdf_body`` has already truncated the body at
        ``classify_max_chars``, so the reformat sees the same window
        the classifier does. The reformat function's own ``max_chars``
        defaults to the same value, and the capture pipeline passes
        ``self.classify_max_chars`` through explicitly so the cap
        stays single-sourced even if the caller raised it.

        Best-effort: when the classifier isn't wired, when the body
        is empty, or when the LLM returns nothing useful, the source
        passes through unchanged. The reformat is a polish pass, not
        a precondition for filing.
        """

        if (kind != "note"
                or source.mime != "application/pdf"
                or not source.text
                or self._classifier is None):
            return source
        formatted = await self._classifier.reformat(
            source.text, max_chars=self.classify_max_chars,
        )
        if not formatted:
            return source
        # Sanity guard: reformatted body must keep most of the input
        # content. The reformat is allowed to compress whitespace and
        # rejoin broken lines, but a result that's a fraction of the
        # input length means the LLM lost content or replied with a
        # fragment -- fall back to the raw text in that case.
        if len(formatted) < len(source.text) * _REFORMAT_MIN_RATIO:
            return source
        return SourceContent(
            text=formatted,
            mime=source.mime,
            title_hint=source.title_hint,
            source_uri=source.source_uri,
        )

    def _source_from_binary(
        self, *, file_data: bytes, mime: str, filename: str,
        source_uri: str | None,
    ) -> tuple[SourceContent | None, list[ImageAttachment], str]:
        """Pick the extraction strategy for a PDF, image, or text file.

        Returns ``(source, images, kind)`` where ``source.text`` is
        whatever text we already have (decoded bytes for md/txt, pypdf
        for native-text PDFs, empty for scans and photos) and
        ``images`` is the page renders the classifier should see. The
        ``kind`` ("bookmark" or "note") drives whether the body is
        kept in the mirror: notes preserve the bytes verbatim, since
        a Markdown export or text file IS the artifact; bookmarks
        keep only the summary, since the binary stays in Matrix. A
        photo is a note with no body: the picture is the artifact.
        """
        title_hint = filename or None
        is_image = mime in IMAGE_MIMES
        # A photo the family posts is their own record, like a note they
        # type, not a pointer to something kept elsewhere.
        if is_image:
            return (
                SourceContent(
                    text="", mime=mime,
                    title_hint=title_hint, source_uri=source_uri,
                ),
                [ImageAttachment(data=file_data, mime=mime)],
                "note",
            )

        # md / txt uploads land as notes: the bytes themselves are the
        # content the user wants to keep, so we decode and pass them
        # straight to the text-only classify path. No vision call, no
        # PDF machinery -- just a SourceContent with the body.
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        is_text_file = mime in TEXT_MIMES or ext in TEXT_EXTS
        if is_text_file:
            try:
                text = file_data.decode("utf-8")
            except UnicodeDecodeError:
                text = file_data.decode("utf-8", errors="replace")
            if not text.strip():
                return (None, [], "note")
            return (
                SourceContent(
                    text=text,
                    mime=mime or ("text/markdown" if ext in {"md", "markdown"} else "text/plain"),
                    title_hint=title_hint, source_uri=source_uri,
                ),
                [],
                "note",
            )

        if mime != "application/pdf":
            # Anything else (audio, video, archives, ...) is out of
            # scope for v1; the capture path stays a text + image
            # surface.
            return (None, [], "bookmark")

        pages = pdf_page_count(file_data) if PdfReader is not None else 0
        text_layer = (
            has_text_layer(file_data) if PdfReader is not None else False
        )
        ocr_layer = (
            has_ocr_text_layer(file_data)
            if PdfReader is not None and text_layer else False
        )

        attach = should_attach_vision(
            has_text_layer=text_layer,
            has_ocr_text_layer=ocr_layer,
            page_count=pages if text_layer else 0,
            vision_max_pages=self.vision_max_pdf_pages,
        )

        text_body = ""
        if text_layer and PdfReader is not None:
            try:
                reader = PdfReader(io.BytesIO(file_data))
                text_body = "\n".join(
                    (p.extract_text() or "") for p in reader.pages
                ).strip()
            except Exception as e:
                logger.debug("[capture] pypdf extract failed: {}", e)

        images: list[ImageAttachment] = []
        if attach:
            # For scans (no text layer) we cap page renders to the
            # vision budget; partial coverage beats nothing.
            cap = self.vision_max_pdf_pages
            rendered = render_pages(file_data)
            for png in rendered[:cap]:
                images.append(ImageAttachment(data=png, mime="image/png"))

        if not images and not text_body:
            return (None, [], "bookmark")

        # PDFs file as notes (body preserved in the mirror) so a
        # downstream `?` query can grep the actual content -- e.g. a
        # UNO rules sheet, a toy manual, a household instruction
        # booklet. Bookmark mode (body dropped, summary IS the entry)
        # would lose the text the user dropped this in for. The body
        # gets capped + LLM-reformatted upstream in `capture_binary`.
        return (
            SourceContent(
                text=text_body, mime="application/pdf",
                title_hint=title_hint, source_uri=source_uri,
            ),
            images,
            "note",
        )

    async def reprocess(
        self, *, vault_path: str, user_hint: str, sender_mxid: str,
        initial_classification: dict | None = None,
        write_in: str | None = None,
        caption: str = "",
        reply_text: str = "",
        capture_id: str | None = None,
        reply_id: str = "",
        reply_at: str = "",
        sources: "list[Source] | None" = None,
        requested_by: str = "",
        history: CardHistory | None = None,
    ) -> CaptureOutcome:
        """Re-classify an already-filed capture using a human note.

        A note is refiled from its sources (ADR-018): the words it was
        sent as, the replies and notes added since, never its summary or
        its clean version (`_reprocess_note`). ``reply_text`` is the reply
        that asked for this refile, by the sender, sent as ``reply_id`` at
        ``reply_at``: it is appended as a source first. ``sources`` are
        the note's sources as a caller already gathered them (`stack
        memory reprocess`), used as they are. ``requested_by`` is who asked
        for the refile, the sender when not given. ``history`` is what the
        card's history says (`stack.cards.history`): the clean version
        before its first correction or edit, a person's edits of it and of
        its fields. A caller that read none (a reply in the chat) passes
        None, and the note's history is read through the mirror.

        A bookmark mirrors ``DocumentPipeline.reprocess``: the prior
        summary IS the source the LLM re-reads, with the reply chain as
        ``user_hint``; this stays cheap (no re-fetch of the page) at the
        cost of corrections working against the model's own paraphrase.
        The archived file cannot be recovered that way, so its reference
        is read back off the entry being replaced. ``caption`` is the
        caption on the upload it was filed from, when the caller could
        read it there; it wins over the entry's.

        ``capture_id`` names the capture for good; ``vault_path`` is where
        the filing message saw it, which a move makes stale. The capture
        is looked up by its id first (`_find_capture`).
        """
        if self._mirror is None:
            return CaptureOutcome(status="no_mirror")
        found = await self._find_capture(capture_id, vault_path)
        if found is None:
            return CaptureOutcome(status="capture_missing")
        vault_path, raw = found
        try:
            meta, summary_text = _parse_capture_markdown(raw)
        except FrontmatterError as e:
            # Re-rendering from a header that does not parse would refile
            # the capture under a guessed type, date and id.
            logger.warning(
                "[capture] {}: frontmatter does not parse ({}), reprocess skipped",
                vault_path, e,
            )
            return CaptureOutcome(status="capture_missing")
        if meta.get("type") == "note":
            return await self._reprocess_note(
                vault_path=vault_path, raw=raw, meta=meta, sender_mxid=sender_mxid,
                initial_classification=initial_classification, write_in=write_in,
                reply=Source(REPLY, _localpart(sender_mxid), reply_at or now(), "chat", "text",
                             reply_id or new_id("chat"), quote=reply_text.strip()),
                sources=sources, requested_by=requested_by or _localpart(sender_mxid),
                history=history,
            )
        caption = caption or _caption_from(raw)
        replies = _with_reply(_replies_from(raw), sender_mxid, reply_text)
        # The summary callout doesn't carry the frontmatter tag list,
        # so the classifier would otherwise have to re-derive tags
        # from prose alone -- they drift on each pass. Append them as
        # a short context line that the prompt naturally treats as
        # part of the entry's content, anchoring stable tags while
        # leaving the LLM free to add/remove via the hint.
        existing_tags = _tags_of(meta)
        body_parts: list[str] = []
        if summary_text:
            body_parts.append(summary_text)
        if caption:
            body_parts.append("The sender's caption: " + caption)
        if existing_tags:
            body_parts.append(
                "Existing tags on this entry: " + ", ".join(existing_tags)
            )
        source = SourceContent(
            text="\n\n".join(body_parts) or meta.get("title", "") or "",
            title_hint=meta.get("title") or None,
            source_uri=meta.get("resource") or None,
        )
        captured_at = meta.get("date") or _dt.date.today().isoformat()
        capture_id = meta.get("capture_id")
        return await self._publish(
            source=source, kind=str(meta.get("type") or "bookmark"), sender_mxid=sender_mxid,
            display_link=meta.get("resource") or "(capture)",
            user_hint=user_hint,
            existing_path=vault_path,
            captured_at=str(captured_at),
            reclassified=True,
            actor=sender_mxid,
            capture_id=str(capture_id) if capture_id else None,
            initial_classification=initial_classification,
            kept_media=_kept_media_from(raw),
            caption=caption, replies=replies,
            write_in=write_in,
        )

    async def _reprocess_note(
        self, *, vault_path: str, raw: str, meta: dict, sender_mxid: str,
        initial_classification: dict | None, write_in: str | None,
        reply: Source, sources: "list[Source] | None", requested_by: str,
        history: CardHistory | None = None,
    ) -> CaptureOutcome:
        """Refile a note from its sources, as it would be filed today.

        The sources are the card's and its sidecar's (`memory.note_card`),
        an old-format note read as the migration converts it, with a
        person's hand edits of a quote undone and appended as their notes
        (`with_quote_edits`, from the history), then the reply unless the
        note holds it already. Sources a caller gathered (`stack memory
        reprocess`, through `note_sources`) have that done. The classifier
        reads the original's words, the replies and notes as corrections,
        a photo from the media store, never the summary. A note whose
        words are typed or spoken is refiled as a clean version of them
        (`clean_words`); one with corrections or a person's edits is
        rebuilt from its events (`_rebuild_of`). Any other note reads its
        corrections as user context. Its tags and its topic are carried
        over as tags, and a person's field edits are applied again
        (`GitMirror.capture_entry`), so a reply in the chat keeps a tag
        somebody added in the card editor.
        """
        if history is None:
            history = await self._mirror.card_history(str(meta.get("card_id") or ""))
        if sources is None:
            side = await self._mirror.read_capture(sidecar_path(vault_path))
            try:
                sources = card_sources(raw, read_sidecar(side))
            except SourcesMismatch as e:
                logger.warning("[capture] {}: {}, reprocess skipped", vault_path, e)
                return CaptureOutcome(status="capture_missing")
            if history is not None:
                sources = with_quote_edits(sources, history.quotes_as_sent, history.quote_edits)
            if reply.quote and (reply.person, reply.words) not in {
                    (s.person, s.words) for s in sources}:
                sources = [*sources, reply]
        original = next((s for s in sources if s.role == ORIGINAL), None)
        if original is None:
            return CaptureOutcome(status="capture_missing")
        words = words_of(sources)
        clean_words = original.kind in ("text", "voice") and is_cleanable(words)
        corrections = corrections_of(sources)
        rebuild = _rebuild_of(original, sources, history=history) if clean_words else None
        found_topic = topic_of(vault_path)
        return await self._publish(
            source=SourceContent(text=words or str(meta.get("title") or ""),
                                 title_hint=meta.get("title") or None),
            kind="note", sender_mxid=_filer(sources, meta) or sender_mxid,
            display_link="(capture)",
            user_hint=None if clean_words else corrections or None,
            rebuild=rebuild,
            existing_path=vault_path,
            captured_at=str(meta.get("date") or _dt.date.today().isoformat()),
            reclassified=True,
            actor=sender_mxid,
            capture_id=original.id,
            initial_classification=initial_classification,
            images=self._images_of(original) if original.kind == "image" else None,
            write_in=write_in,
            clean_words=clean_words,
            seed_topics=[found_topic[1]] if found_topic else None,
            prefer_tags=_tags_of(meta),
            sources=sources,
            requested_by=requested_by,
            ops=history.ops if history else (),
        )

    @staticmethod
    def _images_of(original: Source) -> list[ImageAttachment] | None:
        """The photo a note was filed from, read back from the media store,
        so a refile looks at it again instead of at its own summary."""
        root = media.open_archive()
        link = original.media_link
        if root is None or not link:
            return None
        try:
            data = (root / link.removeprefix("/media/")).read_bytes()
        except OSError:
            return None
        mime = mimetypes.guess_type(link)[0] or "image/jpeg"
        return [ImageAttachment(data=data, mime=mime)]

    async def _publish(
        self, *, source, kind: str, sender_mxid: str, display_link: str,
        images: list[ImageAttachment] | None = None,
        user_hint: str | None = None,
        existing_path: str | None = None,
        captured_at: str | None = None,
        reclassified: bool = False,
        actor: str | None = None,
        capture_id: str | None = None,
        initial_classification: dict | None = None,
        seed_topics: list[str] | None = None,
        bucket: str | None = None,
        email_meta: dict | None = None,
        default_person: bool = True,
        transcribed: bool = False,
        kept_media: dict | None = None,
        write_in: str | None = None,
        extract_action_items: bool = False,
        caption: str = "",
        replies: "list[tuple[str, str]] | tuple" = (),
        clean_words: bool = False,
        rebuild: "Rebuild | None" = None,
        prefer_tags: list[str] | None = None,
        sources: "list[Source] | None" = None,
        ops: "Sequence[FieldOp]" = (),
        source_kind: str = "text",
        sent_at: str = "",
        media_hash: str = "",
        requested_by: str = "",
    ) -> CaptureOutcome:
        """Shared tail: classify, mirror, record tags, return the outcome.

        A note is written as its card and its sidecar (ADR-018): what it
        was made from, ``sources``, and how. A new note's one source is its
        original (`_original_of`): ``source_kind`` says what it is,
        ``sent_at`` when it was sent, ``media_hash`` the hash of its bytes. A
        refile passes the note's sources. ``requested_by`` is who asked for
        this interpretation, the sender when not given.

        ``clean_words`` marks a note in its writer's own words, filed as a
        clean version of them (`clean_words`). Only a caller holding the
        words can say so: a photo's note has none and is described by its
        summary. The classifier is asked for the clean version and no
        summary or facts, and a summary it writes anyway is dropped. A
        clean version that fails the check is dropped too, and the card
        shows the words alone.

        ``rebuild`` is what such a note's corrections and a person's edits
        rebuild it from (`Rebuild`). The clean version is then written
        first, by the card conversation (`_rebuilt`), and the classifier
        reads it as the note and is asked for no text, so the title and the
        tags are taken from what the card shows. When it is refused, the
        classifier reads the words, with the events as user context.

        ``prefer_tags`` are tags the classifier is shown first in its
        vocabulary: a refile's own, so it keeps them where they fit.

        ``extract_action_items`` asks the classifier for the todos in the
        source. Only a note in the writer's own words does (`capture_text`);
        links, files, emails and refiles never do, because their text is
        somebody else's, or the model's own summary of it.

        ``existing_path`` + ``captured_at`` are the reprocess inputs --
        when re-classifying an already-filed capture we keep its
        original capture date and hand the prior path to publish_capture,
        which rewrites the entry where it is.
        ``reclassified`` flips the envelope type to ``capture.reclassified``
        so the reply-chain walker treats it as a chain-link, not a
        boundary.
        """
        if self._mirror is None:
            return CaptureOutcome(status="no_mirror")

        # `sender_name` (capitalized) feeds the classifier prompt;
        # `entity_slug` (lowercased localpart) routes <entity>/<kind>s/...
        # ``bucket`` overrides entity routing for topic-room captures:
        # a memo in #Thema:Camping files under camping/ regardless of
        # who sent it. Sender still flows through as `persons:`.
        localpart = sender_mxid.split(":")[0].lstrip("@")
        sender_name = localpart.capitalize()
        entity_slug = bucket or localpart.lower()
        current_list = await self._current_list(entity_slug) if extract_action_items else ""

        clean, clean_rejected, rebuilt_by = None, "", {}
        events = rebuild.said() if rebuild else ""
        if rebuild:
            clean, clean_rejected, rebuilt_by = await self._rebuilt(source.text, rebuild)
        classification = await self._classify(
            source, sender_name, text=clean, images=images,
            user_hint=user_hint or (events if rebuild and not clean else None),
            initial_classification=initial_classification,
            default_person=default_person,
            current_list=current_list,
            write_in=write_in,
            extract_action_items=extract_action_items,
            kind=kind, clean_words=clean_words and not rebuild,
            classify_only=bool(rebuild), prefer_tags=prefer_tags,
        )
        if clean_words:
            classification["summary"], classification["facts"] = "", []
            answered = str(classification.pop("clean", "") or "")
            if not rebuild:
                clean, clean_rejected = kept(source.text, answered, corrections=user_hint or "")
        if clean_rejected:
            logger.warning(
                "[capture] clean version not filed, it {}: the card shows the words",
                clean_rejected)

        # Topic-room seed: prepend caller-guaranteed tags to whatever
        # the classifier returned. Mutates the classification dict so
        # everything downstream (mirror tags, capture-tag cache,
        # envelope's resolved_tags) sees the merged list.
        classification = self._merge_seed_topics(classification, seed_topics)

        try:
            model = resolve_model(f"{self._name}/classifier")
        except ValueError:
            model = None
        # Which prompt template made this; it goes into the note's sidecar.
        template = str(classification.pop("prompt_template", "") or "")

        tags = self._tag_list(classification)
        captured_at = captured_at or _dt.date.today().isoformat()

        # Email folds into a thread file (append a dated section) instead
        # of replacing a single-shot entry; `email_meta` carries the
        # per-message identity (Message-ID, sender) the fold needs.
        # Everything else above — classify, tags, the envelope below — is
        # shared with URL/note captures. Body is always kept: the
        # conversation IS the content.
        async def file_into(entity_slug: str, tags: list[str]):
            if kind == "email" and email_meta is not None:
                return await self._mirror.publish_email_message(
                    entity=entity_slug,
                    thread_uri=source.source_uri,
                    message_id=email_meta.get("message_id"),
                    from_addr=email_meta.get("from_addr"),
                    title_hint=source.title_hint,
                    body_text=source.text,
                    classification=classification,
                    captured_at=captured_at,
                    model=model,
                    tags=tags,
                    capture_id=capture_id,
                )
            else:
                # Bookmarks default to marker mode (body dropped, summary IS
                # the content); a note's words are its first source.
                keep_body = kind == "note" or self.capture_keep_body
                body_for_mirror = source.text if keep_body else ""
                note_sources = (sources or [self._original_of(
                    source, sender_mxid=sender_mxid, capture_id=capture_id, kind=source_kind,
                    kept_media=kept_media, caption=caption, sent_at=sent_at, media=media_hash,
                )]) if kind == "note" else []
                made = {"at": now(), "model": model or "", "code_commit": code_commit(),
                        "prompt_template": template, **rebuilt_by,
                        "requested_by": requested_by or localpart.lower()}
                return await self._mirror.publish_capture(
                    entity=entity_slug,
                    kind=kind,
                    source_uri=source.source_uri,
                    title_hint=source.title_hint,
                    body_text=body_for_mirror,
                    classification=classification,
                    captured_at=captured_at,
                    model=model,
                    tags=tags,
                    existing_path=existing_path,
                    capture_id=capture_id,
                    submitter=sender_mxid,
                    kept_media=kept_media,
                    caption=caption,
                    replies=replies,
                    clean=clean,
                    sources=note_sources,
                    made=made if kind == "note" else None,
                    ops=ops,
                    changed_by=actor if reclassified else None,
                )

        filed = await file_into(entity_slug, tags)
        # A topic renamed while this capture was classified: the curator
        # refuses the old folder (its card check), and the capture goes to
        # the topic where it is now, under its new id.
        if filed is None and (renamed := self._renamed(entity_slug)):
            old_id, new_id = entity_slug.rpartition("/")[2], renamed.rpartition("/")[2]
            tags = [new_id if t == old_id else t for t in tags]
            classification["tags"] = [new_id if t == old_id else t
                                      for t in classification.get("tags") or []]
            logger.info("[capture] {} was renamed while filing: filed under {}", entity_slug, renamed)
            entity_slug = renamed
            filed = await file_into(entity_slug, tags)
        vault_path = filed.path if filed else None

        # Feed the topic tags back into the
        # vocabulary cache so the next capture's prompt sees them.
        topic_tags = [
            t for t in (classification.get("tags") or [])
            if isinstance(t, str) and t.strip()
        ]
        if self._capture_tags and topic_tags:
            self._capture_tags.record(topic_tags, when=captured_at)
            self._capture_tags.save()

        envelope = None
        if vault_path:
            persons = classification.get("persons") or []
            if isinstance(persons, str):
                persons = [persons]
            envelope = build_capture_event(
                vault_path, classification,
                kind=kind,
                source_uri=source.source_uri,
                capture_id=capture_id,
                card_id=filed.card_id if filed else "",
                resolved_tags=topic_tags,
                resolved_persons=[p for p in persons if isinstance(p, str)],
                actor=actor,
                ts=utc_now_isoformat(),
            )
            if reclassified:
                envelope["type"] = "capture.reclassified"
                if user_hint or events:
                    envelope["data"]["user_hint"] = user_hint or events

        # Transcribed captures echo the words back to the sender so they
        # can spot a mishearing without opening the vault. The caller says
        # so; by the time the text reaches here it is an ordinary note,
        # indistinguishable from a typed one, which is the point.
        transcript = source.text if transcribed else None

        return CaptureOutcome(
            status="reclassified" if reclassified else "captured",
            classification=classification,
            source_title_hint=source.title_hint,
            display_link=display_link,
            vault_path=vault_path,
            envelope=envelope,
            transcript=transcript,
            scope=entity_slug,
            clean_rejected=clean_rejected,
        )

    @staticmethod
    def _original_of(source, *, sender_mxid: str, capture_id: str | None, kind: str,
                     kept_media: dict | None, caption: str, sent_at: str,
                     media: str) -> Source:
        """A new note's original: what was sent, by whom, when, on which channel.

        A chat message keeps its event id; a capture from the command line
        gets an id of its own (ADR-018 §3). A photo is embedded in its
        quote, a file linked, and either is followed by its text and the
        sender's caption.
        """
        channel = "chat" if capture_id and capture_id.startswith("$") else "cli"
        kept = kept_media or {}
        media_line = (f"![[{kept['embed']}]]" if kept.get("embed") else
                      f"[{kept.get('name') or 'original'}]({kept['original']})"
                      if kept.get("original") else "")
        text = source.text if kind != "image" else ""
        quote = "\n\n".join(p for p in (media_line, text.strip(), caption.strip()) if p)
        return Source(
            ORIGINAL, _localpart(sender_mxid), sent_at or now(), channel, kind,
            capture_id or new_id(channel), quote=quote or "(no words)", media=media,
            text={"form": "transcript"} if kind == "voice" else {},
        )

    async def _find_capture(
        self, capture_id: str | None, recorded_path: str,
    ) -> tuple[str, str] | None:
        """The path and text of a filed capture, by its id where it can be.

        The local copy of the family memory says where the id lives now.
        It can lag a write by minutes, so the path the filing message
        recorded is tried after it. The scan reads every card's header,
        so it runs off the event loop.
        """
        by_id = (await asyncio.to_thread(self._vault.card_path, "capture", capture_id)
                 if self._vault is not None and capture_id else None)
        for path in dict.fromkeys(p for p in (by_id, recorded_path) if p):
            raw = await self._mirror.read_capture(path)
            if raw is not None:
                return path, raw
        return None

    async def _current_list(self, entity_slug: str) -> str:
        """The list a topic room's note adds to, for the classifier to see.

        Extraction in isolation cannot tell a repeat from a new item, which
        is how re-posting one list six times grew it instead of leaving it
        alone. Only a topic room (`<bucket>/<topic>`) has a list to show.
        """
        bucket, _, topic = entity_slug.partition("/")
        if not topic:
            return ""
        return await self._mirror.read_capture(list_path(topic, bucket=bucket)) or ""

    async def _family_names(self) -> list[str]:
        """The family's first names: the person files, else the Person tags."""
        people = self._vault.people() if self._vault is not None else None
        if people is not None:
            return [p.first_name for p in people]
        person_tags = await self._paperless.get_tags()
        return [
            t.replace("Person: ", "") for t in person_tags
            if t.startswith("Person: ")
        ]

    async def _rebuilt(self, words: str, rebuild: "Rebuild") -> tuple[str | None, str, dict]:
        """A note's clean version rebuilt from its events, why there is none,
        and the template that wrote it, as the sidecar's `made` names it.

        A note a person edited by hand is asked as the card conversation
        (`Classifier.rebuild_note`), which keeps the edit: one that shows no
        clean version is first given one by the capture prompt, as it would
        be filed today, and the conversation starts from that
        (`clean_words.starting_point`). A note with corrections alone is
        asked the correction prompt (`Classifier.correct_note`), from its
        words and every correction: measured, it works in a correction that
        makes two points, which the conversation did not
        (`tests/e2e/eval/correction_fold.py`). The code then checks the
        answer (`clean_words.kept_rebuilt`). No answer, from a model that
        is down or not configured, is refused as an empty one.
        """
        answer: dict = {}
        if self._classifier is not None:
            try:
                answer = await self._asked(words, rebuild)
            except (LLMUnavailableError, LLMModelNotFoundError, LLMTimeoutError) as e:
                logger.warning("[archivist] rebuilding a note failed: {}", e)
        clean, why = kept_rebuilt(words, str(answer.get("clean") or ""),
                                  events=text_of(rebuild.events))
        key = "conversation_template" if rebuild.edited else "correction_template"
        return clean or None, why, {key: str(answer.get("prompt_template") or "")}

    async def _asked(self, words: str, rebuild: "Rebuild") -> dict:
        assert self._classifier is not None
        if not rebuild.edited:
            return await self._classifier.correct_note(words=words, corrections=rebuild.said())
        prior = rebuild.prior or await self._clean_pass(words)
        return await self._classifier.rebuild_note(
            writer=rebuild.writer, words=words, prior=prior,
            events=[e.message() for e in rebuild.events])

    async def _clean_pass(self, words: str) -> str:
        """The clean version the capture prompt writes for `words` today."""
        assert self._classifier is not None
        answer = await self._classifier.classify_capture(text=words, person_names=[],
                                                         existing_tags=[], clean_words=True)
        return starting_point(words, str((answer or {}).get("clean") or ""))

    async def _classify(
        self, source, sender_name: str,
        *, text: str | None = None,
        images: list[ImageAttachment] | None = None,
        user_hint: str | None = None,
        initial_classification: dict | None = None,
        default_person: bool = True,
        extract_action_items: bool = False,
        current_list: str = "",
        write_in: str | None = None,
        kind: str = "note",
        clean_words: bool = False,
        classify_only: bool = False,
        prefer_tags: list[str] | None = None,
    ) -> dict:
        """Capture-specific classify. Degrades to a minimal classification
        (sender as the only person, the extractor's title hint) on LLM
        failure — the capture is still useful without a digest.

        ``text`` is what the classifier reads in place of the source's
        text: a note's corrected clean version (`_corrected`).

        ``default_person`` falls the sender in as the person when the
        classifier names none — right for a human paste (Homer saved this),
        wrong for email (the "sender" is the mail bot, not a household
        member). Email passes False so an email with no named family member
        gets empty persons rather than the bot."""
        if self._classifier is None:
            # Bot was brought up without AI configured; fall through to the
            # minimal classification below so the capture still files.
            return {}
        person_names = await self._family_names()
        existing_tags = list(dict.fromkeys([
            *(prefer_tags or []),
            *(self._capture_tags.top(self.capture_tag_prompt_size)
              if self._capture_tags else []),
        ]))
        try:
            classification = await self._classifier.classify_capture(
                text=(text or source.text)[: self.classify_max_chars],
                person_names=person_names,
                existing_tags=existing_tags,
                images=images,
                user_hint=user_hint,
                initial_classification=initial_classification,
                extract_action_items=extract_action_items,
                current_list=current_list,
                write_in=write_in,
                kind=kind,
                clean_words=clean_words,
                classify_only=classify_only,
            )
        except (LLMUnavailableError, LLMModelNotFoundError, LLMTimeoutError) as e:
            logger.warning("[archivist] capture classify failed: {}", e)
            classification = {}

        if not classification:
            return {
                "title": source.title_hint or "Capture",
                "persons": [sender_name] if default_person else [],
                "tags": [],
            }
        if default_person and not classification.get("persons"):
            classification["persons"] = [sender_name]
        return classification

    def _renamed(self, bucket: str) -> str:
        """The bucket a renamed topic `bucket` (`family/nahprojekte`) is in now; "" when none.

        Read from the vault's topic cards (`renamed_topic`), so it is the
        answer the curator gives once the rename is committed.
        """
        root = self._vault.root() if self._vault is not None else None
        owner, _, topic = bucket.rpartition("/")
        found = renamed_topic(owner, topic, root=root) if root and owner else None
        return f"{owner}/{found[0]}" if found else ""

    @staticmethod
    def _merge_seed_topics(
        classification: dict, seed_topics: list[str] | None,
    ) -> dict:
        """Prepend topic-room seed tags to the classifier's tag list.

        The tag invariant from docs/design/brain/topic-rooms.md: a
        capture filed in a topic room always carries the topic's slug
        in its `topics:` frontmatter, regardless of the classifier's
        opinion. The room is the source of truth; the classifier is
        advisory.

        Seed tags appear first in the merged list (preserving the
        topic-as-primary-tag semantic), followed by classifier
        additions in their original order. Exact-string duplicates
        between seed and classifier collapse to a single entry; the
        seed's position wins. Empty / None seeds are a no-op.

        Returns the (mutated) classification dict for caller
        convenience -- the same object the classifier already shaped.
        """
        if not seed_topics:
            return classification
        existing = classification.get("tags")
        if isinstance(existing, str):
            existing = [existing]
        elif not isinstance(existing, list):
            existing = []
        merged: list[str] = []
        seen: set[str] = set()
        for tag in seed_topics:
            if not isinstance(tag, str):
                continue
            tag = tag.strip()
            if not tag or tag in seen:
                continue
            merged.append(tag)
            seen.add(tag)
        for tag in existing:
            if not isinstance(tag, str):
                continue
            tag = tag.strip()
            if not tag or tag in seen:
                continue
            merged.append(tag)
            seen.add(tag)
        classification["tags"] = merged
        return classification

    @staticmethod
    def _tag_list(classification: dict) -> list[str]:
        """Mirror `tags:`, the classifier's free-form tags. No `Person: X`
        tags: `persons` says who a capture is about (ADR-017 §4)."""
        raw_tags = classification.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        return [t.strip() for t in raw_tags if isinstance(t, str) and t.strip()]


# ── Parsing for reprocess ──────────────────────────────────────────────


def _parse_capture_markdown(raw: str) -> tuple[dict, str]:
    """Extract frontmatter dict and the `> [!summary]` callout body
    from a capture mirror entry.

    Reprocess needs both: the frontmatter to recover `capture_id`,
    `kind`, `captured_at`, and `source_uri`; the callout body to
    re-feed the classifier as the text source. The body returned is
    the prose + facts text (with the `> ` blockquote prefix stripped
    per line), suitable for use as a SourceContent.text.

    Tolerant of missing structure -- returns `({}, "")` when there is
    no frontmatter block. Frontmatter that is there but does not parse
    raises `FrontmatterError`.
    """
    if not raw or not raw.startswith("---\n"):
        return ({}, "")
    fm_end = raw.find("\n---\n", 4)
    if fm_end < 0:
        return ({}, raw)

    meta = parse_frontmatter(raw)
    body = raw[fm_end + len("\n---\n"):]
    summary = _extract_summary_callout(body)
    return (meta, summary)


# The two lines an entry uses to reach its archived file, as
# `vault_entry.render_capture` writes them.
_FILE_LINE = re.compile(r"^> \*\*File\*\* \[([^\]]*)\]\((/media/[^)]+)\)", re.M)
_EMBED_LINE = re.compile(r"^!\[\[(/media/[^\]]+)\]\]", re.M)


def _kept_media_from(raw: str) -> dict | None:
    """The archived file an entry already names, read back off the page.

    A correction re-renders the whole entry from the prior one and
    never sees the original bytes again. Without reading the reference
    back, replying to fix a tag would drop the file from an entry that
    had one.
    """
    named = _FILE_LINE.search(raw)
    if not named:
        return None
    shown = _EMBED_LINE.search(raw)
    return {
        "name": named.group(1),
        "original": named.group(2),
        "embed": shown.group(1) if shown else "",
    }


# The sections `vault_entry.render_capture` closes an entry with: the
# caption, then the replies, each reply an author line and its words
# quoted below it.
_CAPTION_SECTION = re.compile(r"^## Caption\n\n(.*?)(?=^## Replies\n|\Z)", re.M | re.S)
_REPLIES_SECTION = re.compile(r"^## Replies\n(.*)\Z", re.M | re.S)
_REPLY_AUTHOR = re.compile(r"^\*\*(.+)\*\*$")


def _caption_from(raw: str) -> str:
    """The sender's caption an entry keeps, read back off the page.

    A correction re-renders the entry from the prior one, so the caption
    has to be read back, or the first reply to fix a tag would drop it.
    """
    found = _CAPTION_SECTION.search(raw)
    return found.group(1).strip() if found else ""


def _replies_from(raw: str) -> list[tuple[str, str]]:
    """The ``(author, words)`` of each reply a bookmark keeps, in order."""
    found = _REPLIES_SECTION.search(raw)
    replies: list[tuple[str, list[str]]] = []
    for line in (found.group(1) if found else "").splitlines():
        if author := _REPLY_AUTHOR.match(line.strip()):
            replies.append((author.group(1), []))
        elif line.startswith(">") and replies:
            replies[-1][1].append(line[2:] if line.startswith("> ") else line[1:])
    return [(who, "\n".join(words).strip()) for who, words in replies]


def _localpart(mxid: str) -> str:
    """`@marge:simpson` → `marge`, a person's id."""
    return mxid.split(":")[0].lstrip("@").strip().lower()


def _rebuild_of(original: Source, sources: "list[Source]", *,
                history: CardHistory | None) -> Rebuild | None:
    """What a note in its writer's words is rebuilt from; None when nobody
    corrected or edited it, and it is filed again from its words alone.

    The events are every correction the sources hold and, from its history
    (`stack.cards.history`), every person's edit of the clean version, from
    the clean version before the first of them. Without the history
    (Forgejo could not be read) there are no edits, and the corrections
    alone are worked in. The clean version is "" for a note that shows
    none (`CapturePipeline._rebuilt` gives it one first).
    """
    said = [Correction(s.person, s.time, s.words) for s in sources
            if s.role in (REPLY, NOTE) and s.words]
    prior, edits = (history.prior, history.edits) if history else ("", ())
    events = in_order([*said, *edits])
    if not events:
        return None
    start = "" if prior.lstrip().startswith(">") else prior.strip()
    return Rebuild(name_of(original.person), start, tuple(events))


def _filer(sources: "list[Source]", meta: dict) -> str:
    """Who filed a note: its original's sender."""
    original = next((s for s in sources if s.role == ORIGINAL), None)
    person = original.person if original else str(meta.get("filed_by") or "")
    return "" if person == "unknown" else person


def _tags_of(meta: dict) -> list[str]:
    """A card's tags, without the `Person:` tags older cards carried."""
    return [t for t in meta.get("tags") or []
            if isinstance(t, str) and t.strip() and not t.startswith("Person: ")]


def words_refusal(words: str, *, has_file: bool = False) -> str:
    """Why a note cannot be refiled from `words`; "" when it can.

    A refile replays the note's words (ADR-018), never its summary, so a
    note without them, or with prompt text from an older reprocess in
    them, is refused. A photo or a file is its own source: without a
    caption it is refiled from the file alone. A quote holds the words as
    sent (ADR-018 §4), so the refusal says what a person can do instead:
    send the note again, or refile it once the archivist can read the
    message in the chat.

        >>> words_refusal("buy milk")
        ''
    """
    if not words.strip():
        return "" if has_file else (
            "the note keeps no words to refile it from, and its message in the chat "
            "cannot be read; send the note again")
    if found := next((r for r in PROMPT_RESIDUE if r in words), ""):
        return (f"the card's copy of its words carries prompt text from an older "
                f"reprocess ({found!r}), and its message in the chat cannot be read "
                "to replace it; send the note again, or refile it once the archivist "
                "can read the chat")
    return ""


def _with_reply(replies, sender_mxid: str, words: str) -> list[tuple[str, str]]:
    """``replies`` with this one added, unless the entry already has it,
    so a correction handled twice is kept once."""
    words = words.strip()
    reply = (sender_mxid.split(":")[0].lstrip("@").capitalize(), words)
    if not words or reply in replies:
        return list(replies)
    return [*replies, reply]


def _extract_summary_callout(body: str) -> str:
    """Pull the `> [!summary]` callout text (prose + facts) out of a
    capture mirror's body. Returns "" when no callout is present --
    older entries or a hand-edited file land in that branch."""
    captured: list[str] = []
    in_callout = False
    for line in body.splitlines():
        if not in_callout:
            if line.strip().startswith("> [!summary]"):
                in_callout = True
            continue
        if not line.startswith(">"):
            break
        captured.append(line[1:].lstrip(" "))
    return "\n".join(captured).strip()
