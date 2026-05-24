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
from dataclasses import dataclass, field

from loguru import logger

from notifier import Notifier
from pipeline import (
    LLMModelNotFoundError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from stack import resolve_model


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

    async def _publish(
        self, *, source, kind: str, sender_mxid: str, display_link: str,
    ) -> CaptureOutcome:
        """Shared tail: classify, mirror, record tags, return the outcome."""
        if self._mirror is None:
            return CaptureOutcome(status="no_mirror")

        # `sender_name` (capitalized) feeds the classifier prompt;
        # `entity_slug` (lowercased localpart) routes <entity>/<kind>s/...
        localpart = sender_mxid.split(":")[0].lstrip("@")
        sender_name = localpart.capitalize()
        entity_slug = localpart.lower()
        classification = await self._classify(source, sender_name)

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

    async def _classify(self, source, sender_name: str) -> dict:
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
