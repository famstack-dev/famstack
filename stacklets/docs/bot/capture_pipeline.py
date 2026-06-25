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

import datetime as _dt
import io
from dataclasses import dataclass, field

from loguru import logger

from document_pipeline import utc_now_isoformat
from extractors import SourceContent, email_to_source
from matching import build_capture_event
from notifier import Notifier
from pdf_analysis import (
    DEFAULT_VISION_MAX_PDF_PAGES,
    has_ocr_text_layer,
    has_text_layer,
    pdf_page_count,
    should_attach_vision,
)
from pdf_render import render_pages
from pipeline import (
    ImageAttachment,
    LLMModelNotFoundError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from stack import resolve_model
from stack.ai.client import LLMError

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

# Voice memos: when whisper is wired, the transcript IS the note body.
# Element and most Matrix clients upload Opus-in-OGG; the other formats
# show up when people attach a file picker rather than the in-app recorder.
AUDIO_MIME_PREFIX = "audio/"

# Ratio that the reformatted body must be of the input for the
# reformat to count as a useful rewrite. A well-behaved reformat
# tightens whitespace and stitches broken lines but keeps essentially
# all the words; a result that's a fraction of the input length is
# the LLM losing content (or replying with a fragment like "ok"). The
# threshold is permissive enough to absorb legitimate compression
# (line joins, header normalisation) without letting a half-answer
# replace the raw OCR.
_REFORMAT_MIN_RATIO = 0.5


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
        transcriber=None,
        llm=None,
    ):
        self._url_extractor = url_extractor
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
        # Optional: when None, audio uploads soft-skip with extract_failed
        # so the bot tells the sender it can't transcribe right now.
        self._transcriber = transcriber
        # Optional: when present, the transcriber uses it to polish raw
        # whisper output (add punctuation + sentence breaks). The same
        # LLM the classifier already runs on -- no extra HTTP client.
        # Missing -> raw transcript falls through, never a hard failure.
        self._llm = llm

    async def capture_url(
        self, *, url: str, sender_mxid: str, notifier: Notifier,
        capture_id: str | None = None,
        seed_topics: list[str] | None = None,
        bucket: str | None = None,
        user_hint: str | None = None,
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
        """
        await notifier.acknowledge()
        source = await self._url_extractor.extract(url)
        if source is None:
            return CaptureOutcome(
                status="extract_failed", failure_reason="url",
            )
        return await self._publish(
            source=source, kind="bookmark", sender_mxid=sender_mxid,
            display_link=url, actor=sender_mxid,
            capture_id=capture_id, seed_topics=seed_topics,
            bucket=bucket, user_hint=user_hint,
        )

    async def capture_text(
        self, *, text: str, sender_mxid: str,
        capture_id: str | None = None,
        seed_topics: list[str] | None = None,
        bucket: str | None = None,
    ) -> CaptureOutcome:
        """File a pasted body as a note. Nothing is fetched — the text is
        the source; TextExtractor surfaces any embedded URL as the link.
        The body is always kept (the user typed those exact bytes).

        ``seed_topics`` and ``bucket`` carry the topic-room guarantees
        through; see ``capture_url``.
        """
        source = await self._text_extractor.extract(text)
        if source is None:
            return CaptureOutcome(status="empty")
        return await self._publish(
            source=source, kind="note", sender_mxid=sender_mxid,
            display_link=source.source_uri or "(pasted text)",
            actor=sender_mxid,
            capture_id=capture_id, seed_topics=seed_topics,
            bucket=bucket,
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

    async def capture_voice_batch(
        self, *,
        transcripts: list[str],
        primary_mxc: str | None,
        sender_mxid: str,
        capture_id: str | None = None,
        seed_topics: list[str] | None = None,
        bucket: str | None = None,
    ) -> CaptureOutcome:
        """File N voice memos as a single combined note.

        Used by the `( ... )` batch flow: each voice memo has already
        been transcribed (and LLM-cleaned) on arrival, so this just
        concatenates them with paragraph breaks and ships the result
        through the standard publish path. ``primary_mxc`` is the
        first memo's media URL -- the vault note links back to the
        start of the conversation; other memos stay discoverable via
        chat scrollback.

        Marking the SourceContent as ``audio/ogg`` makes ``_publish``
        echo the combined transcript in the capture reply, same as
        single-memo captures, so the sender sees the whole batch's
        text quoted back to them.
        """
        bodies = [t.strip() for t in transcripts if t and t.strip()]
        if not bodies:
            return CaptureOutcome(status="empty")
        combined = "\n\n".join(bodies)
        source = SourceContent(
            text=combined,
            mime="audio/ogg",
            title_hint=None,
            source_uri=primary_mxc,
        )
        return await self._publish(
            source=source, kind="note", sender_mxid=sender_mxid,
            display_link=primary_mxc or "(voice batch)",
            actor=sender_mxid,
            capture_id=capture_id, seed_topics=seed_topics,
            bucket=bucket,
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
    ) -> CaptureOutcome:
        """File a PDF or image as a bookmark.

        Vision-driven by default: render the page(s) and let the LLM
        do extraction + summary + tags in one prompt. Long text-layer
        PDFs (past the per-instance vision cap) bypass vision and
        ride the text layer through the existing text-capture path,
        so the household doesn't pay vision tokens for a 60-page
        research paper. ``source_uri`` is the Matrix mxc URL so the
        wiki entry links back to the original binary -- we don't
        re-store the bytes; Matrix already has them.
        """
        is_audio = bool(mime and mime.startswith(AUDIO_MIME_PREFIX))
        if is_audio:
            source, images, kind = await self._source_from_audio(
                file_data=file_data, mime=mime, filename=filename,
                source_uri=source_uri,
            )
        else:
            source, images, kind = self._source_from_binary(
                file_data=file_data, mime=mime, filename=filename,
                source_uri=source_uri,
            )
        if source is None:
            return CaptureOutcome(
                status="extract_failed",
                failure_reason="transcription" if is_audio else "binary",
            )
        source = self._cap_pdf_body(source)
        source = await self._maybe_reformat_pdf(source, kind)
        return await self._publish(
            source=source, kind=kind, sender_mxid=sender_mxid,
            display_link=display_link or source_uri or filename,
            images=images, actor=sender_mxid,
            capture_id=capture_id, seed_topics=seed_topics,
            bucket=bucket, default_person=default_person,
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

    async def _source_from_audio(
        self, *, file_data: bytes, mime: str, filename: str,
        source_uri: str | None,
    ) -> tuple[SourceContent | None, list[ImageAttachment], str]:
        """Transcribe a voice memo into a note-shaped SourceContent.

        The transcript IS the note body: same kind ("note") that md/txt
        uploads use, so the classifier sees the transcribed text and the
        mirror writes the words verbatim. Returning ``None`` signals the
        capture as extract_failed -- the orchestrator already renders a
        friendly reply for that case, so a missing/down whisper service
        surfaces uniformly across PDFs and audio.
        """
        if self._transcriber is None:
            logger.info(
                "[capture] audio dropped: no transcriber configured "
                "(WHISPER_URL unset?)"
            )
            return (None, [], "note")
        try:
            transcript = await self._transcriber.transcribe(
                file_data, filename=filename or "voice.ogg",
                cleanup_with=self._llm,
            )
        except LLMError as e:
            # Same failure shape the LLM client uses for chat outages;
            # the orchestrator already maps extract_failed to a user-
            # visible "I couldn't process that" reply.
            logger.warning("[capture] transcription failed: {}", e)
            return (None, [], "note")
        if not transcript.strip():
            return (None, [], "note")
        return (
            SourceContent(
                text=transcript,
                mime=mime,
                title_hint=filename or None,
                source_uri=source_uri,
            ),
            [],
            "note",
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
        keep only the summary, since the binary stays in Matrix.
        """
        title_hint = filename or None
        is_image = mime in IMAGE_MIMES
        if is_image:
            return (
                SourceContent(
                    text="", mime=mime,
                    title_hint=title_hint, source_uri=source_uri,
                ),
                [ImageAttachment(data=file_data, mime=mime)],
                "bookmark",
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
    ) -> CaptureOutcome:
        """Re-classify an already-filed capture using a human note.

        Mirrors ``DocumentPipeline.reprocess`` -- read the prior entry,
        rebuild a SourceContent from its persisted summary, run the
        classifier again with the reply chain as ``user_hint``, and
        rewrite the mirror in place (renaming the slug when the new
        title shifts). Captures don't have a separate Paperless
        backing store, so the prior summary IS the source the LLM
        re-reads; this stays cheap (no vision round-trip, no re-fetch
        of the original binary) at the cost of corrections having to
        work against the model's own paraphrase rather than the raw
        page. Pure text corrections (Homer's "It is a Mac Studio")
        compose cleanly under that constraint.
        """
        if self._mirror is None:
            return CaptureOutcome(status="no_mirror")
        raw = await self._mirror.read_capture(vault_path)
        if raw is None:
            return CaptureOutcome(status="capture_missing")
        meta, summary_text = _parse_capture_markdown(raw)
        # The summary callout doesn't carry the frontmatter tag list,
        # so the classifier would otherwise have to re-derive tags
        # from prose alone -- they drift on each pass. Append them as
        # a short context line that the prompt naturally treats as
        # part of the entry's content, anchoring stable tags while
        # leaving the LLM free to add/remove via the hint.
        raw_tags = meta.get("tags") or []
        existing_tags = [
            t for t in raw_tags
            if isinstance(t, str) and t.strip() and not t.startswith("Person: ")
        ]
        body_parts: list[str] = []
        if summary_text:
            body_parts.append(summary_text)
        if existing_tags:
            body_parts.append(
                "Existing tags on this entry: " + ", ".join(existing_tags)
            )
        source_text = "\n\n".join(body_parts) or meta.get("title", "") or ""
        source = SourceContent(
            text=source_text,
            title_hint=meta.get("title") or None,
            source_uri=meta.get("resource") or None,
        )
        kind = meta.get("type") or "bookmark"
        captured_at = meta.get("date") or _dt.date.today().isoformat()
        capture_id = meta.get("capture_id")
        return await self._publish(
            source=source, kind=str(kind), sender_mxid=sender_mxid,
            display_link=meta.get("resource") or "(capture)",
            user_hint=user_hint,
            existing_path=vault_path,
            captured_at=str(captured_at),
            reclassified=True,
            actor=sender_mxid,
            capture_id=str(capture_id) if capture_id else None,
            initial_classification=initial_classification,
        )

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
    ) -> CaptureOutcome:
        """Shared tail: classify, mirror, record tags, return the outcome.

        ``existing_path`` + ``captured_at`` are the reprocess inputs --
        when re-classifying an already-filed capture we keep its
        original capture date and hand the prior path to publish_capture
        so the rewrite either updates in place or renames the slug.
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
        classification = await self._classify(
            source, sender_name, images=images, user_hint=user_hint,
            initial_classification=initial_classification,
            default_person=default_person,
        )

        # Topic-room seed: prepend caller-guaranteed tags to whatever
        # the classifier returned. Mutates the classification dict so
        # everything downstream (mirror tags, capture-tag cache,
        # envelope's resolved_tags) sees the merged list.
        classification = self._merge_seed_topics(classification, seed_topics)

        try:
            model = resolve_model(f"{self._name}/classifier")
        except ValueError:
            model = None

        tags = self._tag_list(classification)
        captured_at = captured_at or _dt.date.today().isoformat()

        # Email folds into a thread file (append a dated section) instead
        # of replacing a single-shot entry; `email_meta` carries the
        # per-message identity (Message-ID, sender) the fold needs.
        # Everything else above — classify, tags, the envelope below — is
        # shared with URL/note captures. Body is always kept: the
        # conversation IS the content.
        if kind == "email" and email_meta is not None:
            vault_path = await self._mirror.publish_email_message(
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
            # the content); notes always keep the body.
            keep_body = kind == "note" or self.capture_keep_body
            body_for_mirror = source.text if keep_body else ""
            vault_path = await self._mirror.publish_capture(
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
            )

        # Feed topic tags (not the derived Person: X) back into the
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
                resolved_tags=topic_tags,
                resolved_persons=[p for p in persons if isinstance(p, str)],
                actor=actor,
                ts=utc_now_isoformat(),
            )
            if reclassified:
                envelope["type"] = "capture.reclassified"
                if user_hint:
                    envelope["data"]["user_hint"] = user_hint

        # Voice captures echo the transcript back to the sender so they
        # can spot a mistranscription without opening the vault. Other
        # capture shapes (PDFs, images, URLs, notes) leave it None.
        source_mime = getattr(source, "mime", None) or ""
        is_audio_source = source_mime.startswith(AUDIO_MIME_PREFIX)
        transcript = source.text if is_audio_source else None

        return CaptureOutcome(
            status="reclassified" if reclassified else "captured",
            classification=classification,
            source_title_hint=source.title_hint,
            display_link=display_link,
            vault_path=vault_path,
            envelope=envelope,
            transcript=transcript,
        )

    async def _classify(
        self, source, sender_name: str,
        *, images: list[ImageAttachment] | None = None,
        user_hint: str | None = None,
        initial_classification: dict | None = None,
        default_person: bool = True,
    ) -> dict:
        """Capture-specific classify. Degrades to a minimal classification
        (sender as the only person, the extractor's title hint) on LLM
        failure — the capture is still useful without a digest.

        ``default_person`` falls the sender in as the person when the
        classifier names none — right for a human paste (Homer saved this),
        wrong for email (the "sender" is the mail bot, not a household
        member). Email passes False so an email with no named family member
        gets empty persons rather than the bot."""
        if self._classifier is None:
            # Bot was brought up without AI configured; fall through to the
            # minimal classification below so the capture still files.
            return {}
        person_tags = await self._paperless.get_tags()
        person_names = [
            t.replace("Person: ", "") for t in person_tags
            if t.startswith("Person: ")
        ]
        existing_tags = (
            self._capture_tags.top(self.capture_tag_prompt_size)
            if self._capture_tags else []
        )
        try:
            classification = await self._classifier.classify_capture(
                text=source.text[: self.classify_max_chars],
                person_names=person_names,
                existing_tags=existing_tags,
                images=images,
                user_hint=user_hint,
                initial_classification=initial_classification,
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
        """Mirror `tags:` — free-form capture tags + `Person: X` per person,
        so Dataview queries on `Person: …` span documents and captures."""
        raw_tags = classification.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        persons = classification.get("persons") or []
        if isinstance(persons, str):
            persons = [persons]
        out: list[str] = [
            t.strip() for t in raw_tags if isinstance(t, str) and t.strip()
        ]
        out.extend(
            f"Person: {p}" for p in persons if isinstance(p, str) and p.strip()
        )
        return out


# ── Parsing for reprocess ──────────────────────────────────────────────


def _parse_capture_markdown(raw: str) -> tuple[dict, str]:
    """Extract frontmatter dict and the `> [!summary]` callout body
    from a capture mirror entry.

    Reprocess needs both: the frontmatter to recover `capture_id`,
    `kind`, `captured_at`, and `source_uri`; the callout body to
    re-feed the classifier as the text source. The body returned is
    the prose + facts text (with the `> ` blockquote prefix stripped
    per line), suitable for use as a SourceContent.text.

    Tolerant of partial or missing structure -- returns `({}, "")`
    rather than raising so a malformed mirror entry surfaces as
    "couldn't reprocess" instead of crashing the bot.
    """
    if not raw or not raw.startswith("---\n"):
        return ({}, "")
    fm_end = raw.find("\n---\n", 4)
    if fm_end < 0:
        return ({}, raw)

    meta = _parse_yaml_frontmatter(raw[4:fm_end])
    body = raw[fm_end + len("\n---\n"):]
    summary = _extract_summary_callout(body)
    return (meta, summary)


def _parse_yaml_frontmatter(text: str) -> dict:
    """Same stdlib subset memory/lib's `_parse_frontmatter` uses --
    keeps the bot stdlib-only for this path so we don't pull
    python-frontmatter into the reprocess hot loop."""
    data: dict = {}
    current_list: list[str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("  - ") or line.startswith("- "):
            if current_list is not None:
                token = line.split("- ", 1)[1].strip().strip("'\"")
                current_list.append(token)
            continue
        if line.startswith(" ") or line.startswith("\t"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value == "":
            current_list = []
            data[key] = current_list
        else:
            data[key] = value.strip("'\"")
            current_list = None
    return data


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
