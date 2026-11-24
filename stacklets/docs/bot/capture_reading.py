"""capture_reading: the archivist's reading of a capture, the filing core's `Reader` port.

    reader = CaptureReader(source, classifier=classifier, vault=vault, paperless=paperless,
                           mirror=mirror, bot_name="archivist", max_chars=12000,
                           kind="note", clean_words=True)
    reading = await reader.read(entity="marge", topics=["Camping", "Health"])
    reading.classification["title"], reading.clean    # ('Grill charcoal', 'Buy charcoal ...')

The filing core (`stack.filing.file`) files a capture. It gets the
model's answer through one call, `read`. This module gives that answer
for the archivist and for `stack memory capture`. It puts the names of
the people and the topic list into the capture prompt
(`Classifier.classify_capture`). It returns the answer as a
`stack.filing.file.Reading`.

A note in its writer's own words (``clean_words``) is asked for a clean
version, without a summary or facts. The module removes a summary that
the model writes anyway. It gives that summary to the core as
`Reading.said`, because the core finds the people of the note in it. A
clean version that fails the check (`clean_words.kept`) is dropped with
its reason.

A corrected or edited note (``rebuild``) is asked as the card
conversation instead (`_corrected`, ADR-018 §7). `stack.filing.correction`
builds the conversation and reads the answer. The capture prompt is not
asked again.

The model can be down or not set up. The reading then has a minimal
answer: the extractor's title hint and no tags. `Reading.degraded` gives
the reason as a code: `no_model`, `model_silent` or `model_unreadable`.
The core never writes a correction from a degraded reading.

`CapturePipeline._publish` makes one reader per capture, with the
pipeline's classifier and its limit on the text length.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from stack.filing.correction.conversation import build_conversation
from stack.filing.clean_words import kept, starting_point
from stack.filing.correction.note import card_of, reading_after
from .pipeline import ImageAttachment, LLMModelNotFoundError, LLMTimeoutError, LLMUnavailableError
from stack import resolve_model
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
                 prefer_tags: list[str] | None = None, place: str = ""):
        self._source = source
        self._classifier, self._vault, self._paperless, self._mirror = classifier, vault, paperless, mirror
        self._name, self._max_chars, self._kind = bot_name, max_chars, kind
        self._images, self._user_hint = images, user_hint
        self._initial, self._write_in = initial_classification, write_in
        self._extract, self._clean_words = extract_action_items, clean_words
        self._rebuild, self._prefer_tags = rebuild, prefer_tags
        # A refiled note's place, for example `family/camping`. A correction
        # adds to-dos to the list of that topic, whoever sends it.
        self._place = place

    async def read(self, *, entity: str, topics: list[str] | None) -> Reading:
        """The model's answer for the capture at `entity`, shown `topics`
        (`stack.filing.file.Reader`). Never raises for the model: a model
        that fails gives a degraded reading."""
        if self._rebuild:
            return await self._corrected(self._rebuild, entity)
        source, clean_words = self._source, self._clean_words
        current_list = await self._current_list(entity) if self._extract else ""
        clean, clean_rejected, said = None, "", ""
        classification = await self._classify(
            text=None, user_hint=self._user_hint, current_list=current_list,
            clean_words=clean_words, topics=topics)
        if clean_words:
            said = str(classification.get("summary") or "")
            classification["summary"], classification["facts"] = "", []
            answered = str(classification.pop("clean", "") or "")
            clean, clean_rejected = kept(source.text, answered, corrections=self._user_hint or "")
        degraded = str(classification.pop(DEGRADED, "") or "")
        # Which prompt template made this; it goes into the note's sidecar.
        template = str(classification.pop("prompt_template", "") or "")
        return Reading(classification, clean, clean_rejected, {}, self._model(), template, degraded, said)

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

    # ── A corrected note ─────────────────────────────────────────────

    async def _corrected(self, rebuild: "Rebuild", entity: str) -> Reading:
        """The note after its corrections and edits, asked as the card
        conversation (ADR-018 §7, `stack.filing.correction.conversation`).

        The model sees the note's words first. Then it sees the card's
        fields and clean version as its own answer. Then it sees each
        event in time order. It answers what the events change.

        `stack.filing.correction.note` makes the reading from the answer.
        The capture prompt is not asked after it, because it answers a
        whole new filing. A model that fails degrades the reading
        (`no_model`, `model_silent`, `model_unreadable`). The core then
        writes nothing.

        A note in a topic with a list sees the list. The same call asks
        for the new to-dos. The list's merge adds each item at most once
        and removes none.
        """
        words = self._source.text
        if self._classifier is None:
            return Reading({}, degraded="no_model")
        card = card_of(rebuild, self._prefer_tags)
        names = self._tag_names(entity, card["tags"])
        shown = {**card, "tags": [(names.get(t) or [t])[0] for t in card["tags"]]}
        try:
            prior = rebuild.prior or await self._clean_pass(words)
            # Only a topic room (`<bucket>/<topic>`) has a list. A note in
            # another place is not asked for to-dos.
            place = self._place or entity
            current_list = await self._current_list(place) if self._extract and "/" in place else None
            answer = await self._classifier.correct_card(build_conversation(
                writer=rebuild.writer, kind="note", original=words,
                filed={**shown, "clean": prior}, events=[e.message() for e in rebuild.events],
                current_list=current_list), text=True, todos=current_list is not None)
        except (LLMUnavailableError, LLMModelNotFoundError, LLMTimeoutError) as e:
            logger.warning("[archivist] correcting a note failed: {}", e)
            return Reading({}, degraded="model_silent")
        if not answer:
            return Reading({}, degraded="model_unreadable")
        return reading_after(answer, rebuild, card, words=words, prior=prior,
                             people=self._people(), tag_names=names,
                             todos=current_list is not None, model=self._model())

    def _tag_names(self, entity: str, tags: list[str]) -> dict[str, list[str]]:
        """The names of each of the card's tags.

        A topic's name in the instance's language comes first, then its
        names in the other languages. A keyword has no names. The model
        sees the first name, because the people read that name."""
        if self._vault is None:
            return {}
        vocabulary = self._vault.vocabulary(entity)
        names: dict[str, list[str]] = {}
        for tag in tags:
            topic = vocabulary.ontology.topic(vocabulary.current(tag))
            if topic is not None:
                names[tag] = list(dict.fromkeys([topic.name(self._vault.language),
                                                 *topic.names.values()]))
        return names

    def _people(self) -> list:
        """The person files. A person that a correction adds must be one of them."""
        return list(self._vault.people() or []) if self._vault is not None else []

    def _model(self) -> str | None:
        """The model that reads for this bot, as the sidecar names it."""
        try:
            return resolve_model(f"{self._name}/classifier")
        except ValueError:
            return None

    async def _clean_pass(self, words: str) -> str:
        """The clean version the capture prompt writes for `words` today,
        for a note whose card shows none."""
        assert self._classifier is not None
        answer = await self._classifier.classify_capture(text=words, person_names=[],
                                                         existing_tags=[], clean_words=True)
        return starting_point(words, str((answer or {}).get("clean") or ""))

    # ── The capture prompt ───────────────────────────────────────────

    async def _classify(self, *, text: str | None, user_hint: str | None, current_list: str,
                        clean_words: bool, topics: list[str] | None) -> dict:
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
