"""capture_reading: the archivist's reading of a capture, the filing core's `Reader` port.

    reader = CaptureReader(source, classifier=classifier, vault=vault, paperless=paperless,
                           mirror=mirror, bot_name="archivist", max_chars=12000,
                           kind="note", clean_words=True)
    reading = await reader.read(entity="marge", topics=["Camping", "Health"])
    reading.classification["title"], reading.clean    # ('Grill charcoal', 'Buy charcoal ...')

The filing core (`stack.filing.file`) files a capture and asks its intake
for the model's answer through one call, `read`. This module is that answer
for the archivist and `stack memory capture`: the household's names and
the topic list go into the capture prompt (`Classifier.classify_capture`),
and the answer comes back as a `stack.filing.file.Reading`.

A note in its writer's own words (``clean_words``) is asked for a clean
version of them and no summary or facts; a summary it writes anyway is
dropped, and a clean version that fails the check (`clean_words.kept`) is
dropped with why. A corrected or edited note (``rebuild``) gets its clean
version from the card conversation or the correction prompt first
(`_rebuilt`); the capture prompt then reads that version and is asked for
its title and tags only.

The model may be down or not set up. The reading then degrades to a
minimal answer (the extractor's title hint, no tags) and says why in
`Reading.degraded` (`no_model`, `model_silent`), so the core never writes
a correction from it.

Built once per capture by `CapturePipeline._publish`, with the pipeline's
classifier and its cap on the text it reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from .clean_words import kept, kept_rebuilt, starting_point
from .pipeline import ImageAttachment, LLMModelNotFoundError, LLMTimeoutError, LLMUnavailableError
from stack import resolve_model
from stack.cards.events import text_of
from stack.filing.file import Reading
from stack.lists import list_path

if TYPE_CHECKING:
    from stack.filing.refile import Rebuild

__all__ = ["DEGRADED", "CaptureReader"]

# The key under which `_classify` says its answer is not the model's, with
# why as a code (`no_model`, `model_silent`); `read` pops it into `Reading.degraded`.
DEGRADED = "_degraded"


class CaptureReader:
    """The model's reading of one capture, for the filing core.

    Holds what the archivist knows of the capture beyond the core's
    `Capture`: the images the model sees, the person's hint, the answer a
    correction starts from, the language to write in, whether to ask for
    to-dos and a clean version, a note's rebuild and the tags to prefer.

        reader = CaptureReader(source, classifier=classifier, vault=vault, paperless=paperless,
                               mirror=mirror, bot_name="archivist", max_chars=12000, kind="bookmark")
        (await reader.read(entity="marge", topics=None)).classification["title"]   # 'Two-person tent'
    """

    def __init__(self, source, *, classifier, vault, paperless, mirror, bot_name: str,
                 max_chars: int, kind: str, images: list[ImageAttachment] | None = None,
                 user_hint: str | None = None, initial_classification: dict | None = None,
                 write_in: str | None = None, extract_action_items: bool = False,
                 clean_words: bool = False, rebuild: "Rebuild | None" = None,
                 prefer_tags: list[str] | None = None):
        self._source = source
        self._classifier, self._vault, self._paperless, self._mirror = classifier, vault, paperless, mirror
        self._name, self._max_chars, self._kind = bot_name, max_chars, kind
        self._images, self._user_hint = images, user_hint
        self._initial, self._write_in = initial_classification, write_in
        self._extract, self._clean_words = extract_action_items, clean_words
        self._rebuild, self._prefer_tags = rebuild, prefer_tags

    async def read(self, *, entity: str, topics: list[str] | None) -> Reading:
        """The model's answer for the capture at `entity`, shown `topics`
        (`stack.filing.file.Reader`). Never raises for the model: a model
        that fails gives a degraded reading."""
        source, rebuild, clean_words = self._source, self._rebuild, self._clean_words
        current_list = await self._current_list(entity) if self._extract else ""
        clean, clean_rejected, rebuilt_by = None, "", {}
        events = rebuild.said() if rebuild else ""
        if rebuild:
            clean, clean_rejected, rebuilt_by = await self._rebuilt(source.text, rebuild)
        classification = await self._classify(
            text=clean, user_hint=self._user_hint or (events if rebuild and not clean else None),
            current_list=current_list, clean_words=clean_words and not rebuild,
            classify_only=bool(rebuild), topics=topics)
        if clean_words:
            classification["summary"], classification["facts"] = "", []
            answered = str(classification.pop("clean", "") or "")
            if not rebuild:
                clean, clean_rejected = kept(source.text, answered, corrections=self._user_hint or "")
        degraded = str(classification.pop(DEGRADED, "") or "")
        # Which prompt template made this; it goes into the note's sidecar.
        template = str(classification.pop("prompt_template", "") or "")
        try:
            model = resolve_model(f"{self._name}/classifier")
        except ValueError:
            model = None
        return Reading(classification, clean, clean_rejected, rebuilt_by, model, template, degraded)

    # ── What the model is shown ──────────────────────────────────────

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

    # ── A rebuilt note ───────────────────────────────────────────────

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

    # ── The capture prompt ───────────────────────────────────────────

    async def _classify(self, *, text: str | None, user_hint: str | None, current_list: str,
                        clean_words: bool, classify_only: bool, topics: list[str] | None) -> dict:
        """Capture-specific classify. Degrades to a minimal classification
        (no person, the extractor's title hint) on LLM failure: the capture
        is still useful without a digest. A degraded answer carries why
        under `DEGRADED`, which `read` pops: a correction is never written
        from one.

        ``text`` is what the classifier reads in place of the source's
        text: a note's rebuilt clean version. ``topics`` are the household's
        topics it may choose from (`note_topics`). Who the capture is about
        is placed afterwards (`stack.filing.people`)."""
        source = self._source
        if self._classifier is None:
            # Bot was brought up without AI configured; a first filing still
            # files, about its sender (`stack.filing.people`), and says it degraded.
            return {DEGRADED: "no_model"}
        person_names = await self._family_names()
        # A refile's own tags only: no tags of other captures, which may be
        # another person's (no bot-wide tag cache).
        existing_tags = list(dict.fromkeys(self._prefer_tags or []))
        try:
            classification = await self._classifier.classify_capture(
                text=(text or source.text)[: self._max_chars],
                person_names=person_names,
                existing_tags=existing_tags,
                images=self._images,
                user_hint=user_hint,
                initial_classification=self._initial,
                extract_action_items=self._extract,
                current_list=current_list,
                write_in=self._write_in,
                kind=self._kind,
                clean_words=clean_words,
                classify_only=classify_only,
                topics=topics,
            )
        except (LLMUnavailableError, LLMModelNotFoundError, LLMTimeoutError) as e:
            logger.warning("[archivist] capture classify failed: {}", e)
            classification = {}

        if not classification:
            return {
                "title": source.title_hint or "Capture",
                "tags": [],
                DEGRADED: "model_silent",
            }
        return classification
