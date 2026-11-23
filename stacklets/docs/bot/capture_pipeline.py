"""CapturePipeline — URL/text capture → classify → mirror → outcome.

The capture flow, lifted out of ArchivistBot. Far leaner than the
document pipeline: no Paperless write, no entity reconciliation. A URL
or a pasted note becomes a summarised markdown entry in the sender's
own vault bucket, and the outcome carries the event envelope that tells
the other bots about it.

Like DocumentPipeline, it does the work (no Matrix, no i18n) and returns
a CaptureOutcome the orchestrator renders. The one mid-flow message —
"fetching …" before a URL fetch — goes through a Notifier.

A capture's tags are written with the topics of the card's place
(`GitMirror.capture_entry`, `stack.filing.vocabulary`). The prompt offers
no tags of earlier captures: a bot-wide tag cache once did, and it carried
one person's private tags into everyone's prompt, so it is gone. A
`capture-tags.json` a bot left in its data dir is not read.

The steps that file a capture are the filing core's (`stack.filing.file`):
this pipeline reads what was sent, builds the capture, hands it over with
its reading (`capture_reading.CaptureReader`) and its mirror, and turns
the result into the outcome and the event.

Debt: a caller of `_publish` that names no channel gets the one the
capture's id shows (`_channel_of`: an id starting with `$` is `chat`).
Email and the refiles reach it, and none of them writes the channel.
Trigger: the next change to `stack.filing.capture.Origin`; then `channel`
becomes required and `_channel_of` goes.
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
from .capture_reading import CaptureReader
from .notifier import Notifier
from .note_topics import CorrectionView, OwnTags
from .clean_words import is_cleanable
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
)
from stack import media
from stack.filing.capture import Capture, Origin, Refile
from stack.filing.file import Filing, file
from stack.filing.policy import Policy
from stack.filing.refile import NoteRead, refile, refile_sources, refiling_of
from stack.memory import CuratorDown, File, Refused
from stack.cards.sources import (
    REPLY, Source, SourcesMismatch, new_id, now, read_sidecar,
    sidecar_path,
)
from stack.frontmatter import FrontmatterError
from stack.frontmatter import parse as parse_frontmatter
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


class NoteUnreadable(Exception):
    """A correction cannot start because a read of the note failed.

    ``reason`` is the outcome's failure reason (`sidecar_unreadable` or
    `history_unreadable`) and ``cause`` the curator's own error. A sidecar
    that does not exist is no failure. Nothing is written, because
    refiling without the sidecar's messages or a person's edits would lose
    them.
    """

    def __init__(self, reason: str, cause: Exception):
        super().__init__(reason)
        self.reason, self.cause = reason, cause


@dataclass
class CaptureOutcome:
    """What the pipeline produces; the orchestrator renders the reply.

    status: captured | reclassified | not_filed | not_changed |
            capture_missing | extract_failed | no_mirror | empty

    ``captured`` and ``reclassified`` mean the memory wrote the capture
    or its correction. ``not_filed`` (a first filing) and ``not_changed``
    (a correction) mean it did not, and ``failure_reason`` says why as a
    code, so a refused write is never answered "Captured".

    ``transcript`` is populated for voice-memo captures so the reply
    renderer can quote what was heard back to the sender -- the only
    way a user can catch a bad transcription without opening the vault.
    Empty for every other capture shape (PDFs, images, URLs, notes).

    ``failure_reason`` qualifies a ``not_filed``, ``not_changed`` or
    ``capture_missing`` status with why the memory did not write it, as a
    code the reply layer words in the stack's language: the memory's
    (``stack.memory.why_not_written``, ``unknown`` included), the mirror's
    (``card_missing``, ``unreadable``), or the pipeline's own
    (``sources_mismatch``, ``no_original``, and ``no_model`` or
    ``model_silent`` for a correction without the model's answer). It
    qualifies an ``extract_failed`` status with
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
    # like `family/camping` or a bare personal bucket like `homer`. `stack
    # docs capture` prints it.
    scope: str | None = None
    # Why a note filed without the clean version the model wrote
    # (`clean_words.kept`), "" when it has one or needed none.
    clean_rejected: str = ""
    # The card's day (`YYYY-MM-DD`): when it was captured, or the day the
    # email was sent. The chat's answer shows it beside the people.
    captured_at: str | None = None
    # What a correction changed on the card, as the memory wrote it
    # (`Filed.changes`); None when the writer does not say, and the answer
    # then compares the classifications.
    changes: "tuple | None" = None


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
        paperless,
        bot_name: str,
        classify_max_chars: int,
        capture_keep_body: bool,
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
        self._paperless = paperless
        self._name = bot_name
        self.classify_max_chars = classify_max_chars
        self.capture_keep_body = capture_keep_body
        self.vision_max_pdf_pages = vision_max_pdf_pages
        self._llm = llm

    async def capture_url(
        self, *, url: str, sender_mxid: str, notifier: Notifier,
        capture_id: str | None = None,
        channel: str = "",
        seed_topics: list[str] | None = None,
        bucket: str | None = None,
        user_hint: str | None = None,
        write_in: str | None = None,
        read_in: str | None = None,
    ) -> CaptureOutcome:
        """Fetch a URL and file it as a bookmark.

        The body is dropped by default (the URL + summary IS the entry)
        unless `capture_keep_body` is set.

        ``seed_topics`` are tags the caller guarantees on this capture
        regardless of what the classifier returns -- the topic-rooms
        contract. The pipeline
        prepends them to the classifier's tag list and dedupes; the
        mirror and the envelope both see the merged list.

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
            capture_id=capture_id, channel=channel, seed_topics=seed_topics,
            bucket=bucket, user_hint=user_hint, write_in=write_in, read_in=read_in,
        )
        return replace(result, blocked_reason=blocked_reason) if blocked_reason else result

    async def capture_text(
        self, *, text: str, sender_mxid: str,
        capture_id: str | None = None,
        channel: str = "",
        seed_topics: list[str] | None = None,
        bucket: str | None = None,
        transcribed: bool = False,
        write_in: str | None = None,
        caption: str = "",
        sent_at: str = "",
        read_in: str | None = None,
    ) -> CaptureOutcome:
        """File a message body as a note. Nothing is fetched — the text is
        the source; TextExtractor surfaces any embedded URL as the link.
        The body is always kept (those are the exact words sent).

        ``seed_topics`` and ``bucket`` carry the topic-room guarantees
        through; see ``capture_url``. ``read_in`` is the bucket of the room
        the note came from: the model is shown only the topics its people
        may read too (`Vocabulary.of`). None, for the CLI, shows the card's.

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
            capture_id=capture_id, channel=channel, seed_topics=seed_topics,
            bucket=bucket, transcribed=transcribed, write_in=write_in, read_in=read_in,
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
        from_name: str | None = None,
    ) -> CaptureOutcome:
        """Fold a fetched email into its thread file.

        Reuses the capture pipeline's classify/tag/envelope tail wholesale:
        the body is the source the classifier reads, the subject the title,
        the *thread root* the file identity (an RFC 2392 ``mid:`` URI). The
        write differs from a URL/note capture — email accumulates, so each
        message folds into one thread file (see `publish_email_message`)
        rather than replacing a single-shot entry. `from_addr` is surfaced
        to the classifier as a hint so it can resolve the correspondent,
        and stamps the message's section heading; with `from_name` it is
        the sender the card keeps when the message starts its thread. Routing — which bucket,
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
            email_meta={"message_id": message_id, "from_addr": from_addr,
                        "from_name": from_name},
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
        channel: str = "",
        seed_topics: list[str] | None = None,
        bucket: str | None = None,
        default_person: bool = True,
        kept_media: dict | None = None,
        write_in: str | None = None,
        caption: str = "",
        sent_at: str = "",
        read_in: str | None = None,
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
            capture_id=capture_id, channel=channel, seed_topics=seed_topics,
            bucket=bucket, default_person=default_person, read_in=read_in,
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
        read_in: str | None = None,
        caption: str = "",
        reply_text: str = "",
        capture_id: str | None = None,
        reply_id: str = "",
        reply_at: str = "",
        note: NoteRead | None = None,
        chat: "Sequence[Source]" = (),
        added: "Sequence[Source]" = (),
        requested_by: str = "",
    ) -> CaptureOutcome:
        """Re-classify an already-filed capture using a human note.

        A note is refiled from its sources (ADR-018): the words it was
        sent as, the replies and notes added since, never its summary or
        its clean version (`_reprocess_note`). ``reply_text`` is the reply
        that asked for this refile, by the sender, sent as ``reply_id`` at
        ``reply_at``: it is added as a source. ``note`` is the note as a
        caller already read it (`stack memory reprocess`: the card, its
        sidecar and its history, with their versions); without one it is
        read here, once, through the mirror. ``chat`` are the note's
        messages as the chat has them, ``added`` the replies and notes the
        caller adds (`stack.filing.refile.refile_sources`).
        ``requested_by`` is who asked for the refile, the sender when not
        given.
        ``read_in`` is the bucket of the room the correction came from, as
        for ``capture_text``; None, for the CLI, shows the card's topics.

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
        card = File(note.path, note.text, note.version) if note else \
            await self._find_capture(capture_id, vault_path)
        if card is None:
            return CaptureOutcome(status="capture_missing",
                                  failure_reason="card_missing")
        vault_path, raw = card.path, card.text
        try:
            meta, summary_text = _parse_capture_markdown(raw)
        except FrontmatterError as e:
            # Re-rendering from a header that does not parse would refile
            # the capture under a guessed type, date and id.
            logger.warning(
                "[capture] {}: frontmatter does not parse ({}), reprocess skipped",
                vault_path, e,
            )
            return CaptureOutcome(status="capture_missing",
                                  failure_reason="unreadable")
        view = CorrectionView.of(self._vault.vocabulary if self._vault else None, vault_path, read_in=read_in)
        initial_classification, own_tags = view.anchor(initial_classification), view.own_tags(meta.get("tags"))
        if meta.get("type") == "note":
            if note is None:
                try:
                    note = await self._note_read(card, meta)
                except NoteUnreadable as e:
                    logger.warning("[capture] {}: {} ({}), reprocess skipped", vault_path, e.reason, e.cause)
                    return CaptureOutcome(status="not_changed", failure_reason=e.reason)
            reply = Source(REPLY, _localpart(sender_mxid), reply_at or now(), "chat", "text",
                           reply_id or new_id("chat"), quote=reply_text.strip())
            return await self._reprocess_note(
                note, meta=meta, sender_mxid=sender_mxid, chat=chat, added=[*added, reply],
                initial_classification=initial_classification, write_in=write_in, read_in=read_in,
                own_tags=own_tags, requested_by=requested_by or _localpart(sender_mxid))
        caption = caption or _caption_from(raw)
        replies = _with_reply(_replies_from(raw), sender_mxid, reply_text)
        # The summary callout doesn't carry the frontmatter tag list,
        # so the classifier would otherwise have to re-derive tags
        # from prose alone -- they drift on each pass. Append them as
        # a short context line that the prompt naturally treats as
        # part of the entry's content, anchoring stable tags while
        # leaving the LLM free to add/remove via the hint.
        body_parts: list[str] = []
        if summary_text:
            body_parts.append(summary_text)
        if caption:
            body_parts.append("The sender's caption: " + caption)
        if own_tags.shown:
            body_parts.append("Existing tags on this entry: " + ", ".join(own_tags.shown))
        source = SourceContent(
            text="\n\n".join(body_parts) or meta.get("title", "") or "",
            title_hint=meta.get("title") or None,
            source_uri=meta.get("resource") or None,
        )
        captured_at = meta.get("date") or _dt.date.today().isoformat()
        capture_id = meta.get("capture_id")
        kind = str(meta.get("type") or "bookmark")
        return await self._publish(
            # Filed under its first sender, as a note is: the one correcting
            # is not who a bookmark naming nobody is about.
            source=source, kind=kind, sender_mxid=_filer(meta) or sender_mxid,
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
            write_in=write_in, read_in=read_in, own_tags=own_tags,
            correction_words=reply_text or user_hint,
        )

    async def _note_read(self, card: File, meta: dict) -> NoteRead:
        """The note's card, its sidecar and its history, read once, with
        their versions; the refile is written on them. Raises `NoteUnreadable`
        when the sidecar (a missing one is no failure) or the history cannot be read."""
        try:
            side = await self._mirror.file_at(sidecar_path(card.path))
        except (Refused, CuratorDown) as e:
            raise NoteUnreadable("sidecar_unreadable", e) from e
        try:
            history = await self._mirror.card_history(str(meta.get("card_id") or ""))
        except (Refused, CuratorDown) as e:
            raise NoteUnreadable("history_unreadable", e) from e
        return NoteRead(card.path, card.text, card.version, side.text if side else "",
                        side.version if side else "", history)

    async def _reprocess_note(
        self, note: NoteRead, *, meta: dict, sender_mxid: str, chat: "Sequence[Source]",
        added: "Sequence[Source]", initial_classification: dict | None, write_in: str | None,
        read_in: str | None, own_tags: OwnTags, requested_by: str,
    ) -> CaptureOutcome:
        """Refile a note from all of its sources, as it would be filed today
        (`stack.filing.refile`).

        The sources are the card's and its sidecar's (`memory.note_card`), an
        old-format note read as the migration converts it, with ``chat``
        and ``added`` (`refile_sources`). The model reads every original's
        words and photos, never the summary, and the replies and notes as
        corrections. A note whose card shows a clean version is filed as one
        again, rebuilt from its corrections and a person's edits; one that
        shows a summary reads its corrections as user context. Its tags and
        its topic are carried over, a person's field edits are applied
        again, and it is written on the versions ``note`` was read at.
        """
        try:
            held = card_sources(note.text, read_sidecar(note.sidecar))
        except SourcesMismatch as e:
            logger.warning("[capture] {}: {}, reprocess skipped", note.path, e)
            return CaptureOutcome(status="capture_missing", failure_reason="sources_mismatch")
        refiling = refiling_of(note, refile_sources(held, history=note.history, chat=chat, added=added))
        if refiling.refused:
            return CaptureOutcome(status="capture_missing", failure_reason=refiling.refused)
        title = meta.get("title") or None
        content = SourceContent(text=refiling.words or str(title or ""), title_hint=title)
        user_hint = None if refiling.clean else refiling.corrections or None
        reader = CaptureReader(
            content, classifier=self._classifier, vault=self._vault, paperless=self._paperless,
            mirror=self._mirror, bot_name=self._name, max_chars=self.classify_max_chars,
            kind="note", images=self._images_of(refiling.images), user_hint=user_hint,
            initial_classification=initial_classification, write_in=write_in,
            clean_words=refiling.clean, rebuild=refiling.rebuild, prefer_tags=list(own_tags.shown))
        found_topic = topic_of(note.path)
        policy = Policy(room_topics=(found_topic[1],) if found_topic else (), read_in=read_in,
                        keep_body=self.capture_keep_body)
        filing = await refile(note, refiling, policy, reader=reader, writer=self._mirror,
                              directory=self._vault, by=sender_mxid, content=content,
                              requested_by=requested_by, own_tags=own_tags,
                              channel=_channel_of(refiling.capture_id))
        return self._outcome(filing, source=content, display_link="(capture)", kind="note",
                             reclassified=True, actor=sender_mxid, capture_id=refiling.capture_id,
                             user_hint=user_hint or (refiling.rebuild.said() if refiling.rebuild else ""),
                             transcribed=False)

    @staticmethod
    def _images_of(links: "Sequence[str]") -> list[ImageAttachment] | None:
        """Every photo a note was filed from, read back from the media store,
        so a refile looks at them again instead of at its own summary; None
        when there is none to read."""
        root = media.open_archive()
        if root is None:
            return None
        images = []
        for link in links:
            try:
                data = (root / link.removeprefix("/media/")).read_bytes()
            except OSError:
                continue
            images.append(ImageAttachment(data=data, mime=mimetypes.guess_type(link)[0] or "image/jpeg"))
        return images or None

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
        own_tags: OwnTags | None = None,
        read_in: str | None = None,
        sources: "list[Source] | None" = None,
        source_kind: str = "text",
        sent_at: str = "",
        media_hash: str = "",
        correction_words: str = "",
        channel: str = "",
    ) -> CaptureOutcome:
        """Shared tail: build the capture, file it through the filing core
        (`stack.filing.file`), return the outcome.

        The core places the card, asks this bot's reading
        (`capture_reading.CaptureReader`), names its people and tags,
        writes it through the mirror and files it again under a renamed
        topic. ``channel`` is the intake's (`chat`, `cli`); a caller that
        names none gets the one its id shows (`_channel_of`).

        A note is written as its card and its sidecar (ADR-018): what it
        was made from, ``sources``, and how. A new note's one source is its
        original (`stack.filing.capture.original_of`): ``source_kind`` says what it is,
        ``sent_at`` when it was sent, ``media_hash`` the hash of its bytes.
        Only a caller that builds a note of several messages (a test, until
        the chat files one) passes ``sources``; a refile of a note does not
        come here (`stack.filing.refile`).

        ``clean_words`` marks a note in its writer's own words, filed as a
        clean version of them (`clean_words`). Only a caller holding the
        words can say so: a photo's note has none and is described by its
        summary. The classifier is asked for the clean version and no
        summary or facts, and a summary it writes anyway is dropped. A
        clean version that fails the check is dropped too, and the card
        shows the words alone.

        ``own_tags`` are a refile's own tags as its room may see them
        (`note_topics.CorrectionView`); the card keeps those held back.

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
        boundary. A correction whose classification degraded (the model
        failed, or none is set up) writes nothing and comes back
        ``not_changed``: refiling from the minimal classification would
        strip the card's people and tags.
        ``correction_words`` are what the person wrote; an email's
        correction removes only the tags and people they name
        (`email_correction`).
        """
        if self._mirror is None:
            return CaptureOutcome(status="no_mirror")
        reader = CaptureReader(
            source, classifier=self._classifier, vault=self._vault, paperless=self._paperless,
            mirror=self._mirror, bot_name=self._name, max_chars=self.classify_max_chars, kind=kind,
            images=images, user_hint=user_hint, initial_classification=initial_classification,
            write_in=write_in, extract_action_items=extract_action_items, clean_words=clean_words)
        origin = Origin(channel=channel or _channel_of(capture_id), person=_localpart(sender_mxid),
                        account=sender_mxid, capture_id=capture_id, sent_at=sent_at)
        capture = Capture(kind=kind, content=source, origin=origin, source_kind=source_kind,
                          kept_media=kept_media, caption=caption, replies=replies,
                          media_hash=media_hash, sources=sources, default_person=default_person,
                          email=email_meta, captured_at=captured_at)
        # ``bucket`` overrides the sender's bucket for a topic room's capture:
        # a memo in #Thema:Camping files under camping/ whoever sent it.
        policy = Policy(bucket=bucket, room_topics=tuple(seed_topics or ()), read_in=read_in,
                        keep_body=self.capture_keep_body)
        refile = Refile(existing_path or "", by=actor or "", own_tags=own_tags,
                        words=correction_words) if reclassified else None
        filing = await file(capture, policy, reader=reader, writer=self._mirror,
                            directory=self._vault, refile=refile)
        return self._outcome(filing, source=source, display_link=display_link, kind=kind,
                             reclassified=reclassified, actor=actor, capture_id=capture_id,
                             user_hint=user_hint or "", transcribed=transcribed)

    @staticmethod
    def _outcome(filing: Filing, *, source, display_link: str, kind: str, reclassified: bool,
                 actor: str | None, capture_id: str | None, user_hint: str,
                 transcribed: bool) -> CaptureOutcome:
        """What filing a capture did, as the reply and the event need it."""
        if filing.filed is None:
            # What the memory did not write is not reported as written.
            return CaptureOutcome(
                status=filing.status, classification=filing.classification,
                source_title_hint=source.title_hint, display_link=display_link,
                failure_reason=filing.reason,
                scope=filing.entity if filing.classification is not None else None,
            )
        filed, classification = filing.filed, filing.classification or {}
        # The tags the event names. They are not kept for later prompts: a
        # cache for the whole bot carried one person's private tags into
        # everyone's prompt.
        topic_tags = [t for t in (classification.get("tags") or []) if isinstance(t, str) and t.strip()]
        envelope = build_capture_event(
            filed.path, classification, kind=kind, source_uri=source.source_uri,
            capture_id=capture_id, card_id=filed.card_id, resolved_tags=topic_tags,
            resolved_persons=list(classification.get("persons") or []),
            actor=actor, ts=utc_now_isoformat(),
        ) if filed.path else None
        if envelope is not None and reclassified:
            envelope["type"] = "capture.reclassified"
            if user_hint:
                envelope["data"]["user_hint"] = user_hint
        return CaptureOutcome(
            status="reclassified" if reclassified else "captured",
            classification=classification,
            source_title_hint=source.title_hint,
            display_link=display_link,
            vault_path=filed.path,
            envelope=envelope,
            # Transcribed captures echo the words back to the sender so they
            # can spot a mishearing without opening the memory.
            transcript=source.text if transcribed else None,
            scope=filing.entity,
            clean_rejected=filing.clean_rejected,
            changes=filed.changes,
            captured_at=filing.captured_at,
        )

    async def _find_capture(
        self, capture_id: str | None, recorded_path: str,
    ) -> File | None:
        """A filed capture's file, with its path and version, by its id where it can be.

        The local copy of the family memory says where the id lives now.
        It can lag a write by minutes, so the path the filing message
        recorded is tried after it. The scan reads every card's header,
        so it runs off the event loop.
        """
        by_id = (await asyncio.to_thread(self._vault.card_path, "capture", capture_id)
                 if self._vault is not None and capture_id else None)
        for path in dict.fromkeys(p for p in (by_id, recorded_path) if p):
            if (found := await self._mirror.read_file(path)) is not None:
                return found
        return None

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


def _channel_of(capture_id: str | None) -> str:
    """The channel of a capture whose intake named none: a chat message's
    event id starts with `$`, anything else came from the command line."""
    return "chat" if capture_id and capture_id.startswith("$") else "cli"


def _localpart(mxid: str) -> str:
    """`@marge:simpson` → `marge`, a person's id."""
    return mxid.split(":")[0].lstrip("@").strip().lower()


def _filer(meta: dict) -> str:
    """Who filed a bookmark: its `filed_by`."""
    person = str(meta.get("filed_by") or "")
    return "" if person == "unknown" else person


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
