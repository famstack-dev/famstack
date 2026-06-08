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

from extractors import SourceContent
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

try:
    from pypdf import PdfReader  # type: ignore
except Exception:  # pragma: no cover - runtime dep at bot install time
    PdfReader = None  # type: ignore


IMAGE_MIMES = {
    "image/jpeg", "image/jpg", "image/png", "image/heic", "image/heif",
    "image/webp", "image/tiff",
}


@dataclass
class CaptureOutcome:
    """What the pipeline produces; the orchestrator renders the reply.

    status: captured | extract_failed | no_mirror | empty
    """

    status: str
    classification: dict = field(default_factory=dict)
    source_title_hint: str | None = None
    display_link: str = ""


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

    async def capture_url(
        self, *, url: str, sender_mxid: str, notifier: Notifier,
    ) -> CaptureOutcome:
        """Fetch a URL and file it as a bookmark.

        The body is dropped by default (the URL + summary IS the entry)
        unless `capture_keep_body` is set.
        """
        await notifier.status("capture_fetching", url=url)
        source = await self._url_extractor.extract(url)
        if source is None:
            return CaptureOutcome(status="extract_failed")
        return await self._publish(
            source=source, kind="bookmark", sender_mxid=sender_mxid,
            display_link=url,
        )

    async def capture_text(
        self, *, text: str, sender_mxid: str,
    ) -> CaptureOutcome:
        """File a pasted body as a note. Nothing is fetched — the text is
        the source; TextExtractor surfaces any embedded URL as the link.
        The body is always kept (the user typed those exact bytes)."""
        source = await self._text_extractor.extract(text)
        if source is None:
            return CaptureOutcome(status="empty")
        return await self._publish(
            source=source, kind="note", sender_mxid=sender_mxid,
            display_link=source.source_uri or "(pasted text)",
        )

    async def capture_binary(
        self, *,
        file_data: bytes,
        mime: str,
        filename: str,
        source_uri: str | None,
        sender_mxid: str,
        display_link: str | None = None,
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
        source, images = self._source_from_binary(
            file_data=file_data, mime=mime, filename=filename,
            source_uri=source_uri,
        )
        if source is None:
            return CaptureOutcome(status="extract_failed")
        return await self._publish(
            source=source, kind="bookmark", sender_mxid=sender_mxid,
            display_link=display_link or source_uri or filename,
            images=images,
        )

    def _source_from_binary(
        self, *, file_data: bytes, mime: str, filename: str,
        source_uri: str | None,
    ) -> tuple[SourceContent | None, list[ImageAttachment]]:
        """Pick the extraction strategy for a PDF or image.

        Returns ``(source, images)`` where ``source.text`` is whatever
        text we already have (pypdf for native-text PDFs, empty for
        scans and photos) and ``images`` is the page renders the
        classifier should see. The classifier's vision pass produces
        the summary; pypdf text rides alongside as supplementary
        context when both are available.
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
            )

        if mime != "application/pdf":
            # Anything else (audio, video, archives, ...) is out of
            # scope for v1; the capture path stays a text + image
            # surface.
            return (None, [])

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
            return (None, [])

        return (
            SourceContent(
                text=text_body, mime="application/pdf",
                title_hint=title_hint, source_uri=source_uri,
            ),
            images,
        )

    async def _publish(
        self, *, source, kind: str, sender_mxid: str, display_link: str,
        images: list[ImageAttachment] | None = None,
    ) -> CaptureOutcome:
        """Shared tail: classify, mirror, record tags, return the outcome."""
        if self._mirror is None:
            return CaptureOutcome(status="no_mirror")

        # `sender_name` (capitalized) feeds the classifier prompt;
        # `entity_slug` (lowercased localpart) routes <entity>/<kind>s/...
        localpart = sender_mxid.split(":")[0].lstrip("@")
        sender_name = localpart.capitalize()
        entity_slug = localpart.lower()
        classification = await self._classify(source, sender_name, images=images)

        try:
            model = resolve_model(f"{self._name}/classifier")
        except ValueError:
            model = None

        tags = self._tag_list(classification)
        captured_at = _dt.date.today().isoformat()

        # Bookmarks default to marker mode (body dropped, summary IS the
        # content); notes always keep the body.
        keep_body = kind == "note" or self.capture_keep_body
        body_for_mirror = source.text if keep_body else ""

        await self._mirror.publish_capture(
            entity=entity_slug,
            kind=kind,
            source_uri=source.source_uri,
            title_hint=source.title_hint,
            body_text=body_for_mirror,
            classification=classification,
            captured_at=captured_at,
            model=model,
            tags=tags,
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

        return CaptureOutcome(
            status="captured",
            classification=classification,
            source_title_hint=source.title_hint,
            display_link=display_link,
        )

    async def _classify(
        self, source, sender_name: str,
        *, images: list[ImageAttachment] | None = None,
    ) -> dict:
        """Capture-specific classify. Degrades to a minimal classification
        (sender as the only person, the extractor's title hint) on LLM
        failure — the capture is still useful without a digest."""
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
            )
        except (LLMUnavailableError, LLMModelNotFoundError, LLMTimeoutError) as e:
            logger.warning("[archivist] capture classify failed: {}", e)
            classification = {}

        if not classification:
            return {
                "title": source.title_hint or "Capture",
                "persons": [sender_name],
                "tags": [],
            }
        if not classification.get("persons"):
            classification["persons"] = [sender_name]
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
