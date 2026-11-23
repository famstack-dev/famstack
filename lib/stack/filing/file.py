"""Filing a capture: the steps from what a person sent to its card in the memory.

    filing = await file(capture, policy, reader=reading, writer=mirror,
                        household=vault)
    filing.status        # 'filed'
    filing.filed.path    # 'marge/notes/2026/11/grill-charcoal-0c401e.md'

Every capture files through these steps, whichever intake took it in (the
archivist in a room, `stack memory capture` on the command line), and a
correction files through them again (`Refile`):

    capture, policy
      │ 1 the card's place      policy.bucket, else the sender's own bucket
      │ 2 the topics shown      the place's vocabulary, as the room may read it
      │ 3 the reading           Reader: the model's answer (the intake's port)
      │ 4 the people            who the card is about, by the household's ids
      │ 5 the tags              the room's topic, the keywords, the topics chosen
      │ 6 the write             Writer: one curator commit, then the after-steps
      │ 7 a renamed topic       written again under the topic's new id
      ▼
    Filing: filed, not filed (why), or not changed

Why the steps live here: the archivist, the command line, email and the
refile of a card all filed through one method of the archivist that knew
rooms and Matrix events. The steps are the memory's rules for a card, so
they are in the framework and read only what an intake hands them: a
`Capture`, a `Policy` and the ports. The intake keeps what it alone knows:
building the capture, the reading (its model and prompts), and telling the
person what happened.

What it does not do: decide that a capture is complete, ask the model, or
reply. A correction whose reading degraded (the model failed, or none is set
up) writes nothing and comes back `not_changed`, because refiling from the
minimal answer would strip the card's people and tags.

Email keeps its own write: a message folds into its thread's card, and a
correction rewrites the card's head (`Writer.publish_email_message`,
`Writer.correct_email`). It is shown no topics and keeps its names as
written, because its sender is the mail bot.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from loguru import logger

from ..cards.lookup import renamed_topic
from ..cards.sources import Source, now
from .capture import Capture, Refile, original_of
from .card_tags import tag_list, with_chosen_topics, with_room_topic
from .cards import code_commit
from .people import People
from .policy import Policy
from .topic_list import topics_to_choose
from .vocabulary import Vocabulary
from .write import Filed, NotFiled

__all__ = ["Filing", "Household", "Reader", "Reading", "Writer", "file"]


# ── The ports ─────────────────────────────────────────────────────────


@dataclass
class Reading:
    """The model's answer for a capture, as the intake's reader gives it.

    ``classification`` is the answer (title, summary, tags, topics, persons,
    action items), without the reader's own keys. ``clean`` is a note's
    clean version, None when it has none; ``clean_rejected`` why one was
    dropped. ``made_by`` is the sidecar's `made` entries the reader adds
    (the template that rebuilt a note). ``model`` and ``template`` name what
    made the answer. ``degraded`` says why it is not the model's ("" when it is).

        Reading({"title": "Grill charcoal", "tags": ["grill"]}, model="qwen3")
    """

    classification: dict
    clean: str | None = None
    clean_rejected: str = ""
    made_by: dict = field(default_factory=dict)
    model: str | None = None
    template: str = ""
    degraded: str = ""


class Reader(Protocol):
    """The intake's reading of one capture: asks its model, returns a `Reading`.

    ``entity`` is the card's place (`marge`, `family/camping`); ``topics``
    the topic titles the model may choose from, None for none (email).

        reading = await reader.read(entity="family/camping", topics=["Camping", "Health"])
    """

    async def read(self, *, entity: str, topics: list[str] | None) -> Reading: ...


class Household(Protocol):
    """The household a card is filed for: its topics, its people, its language and its memory.

        household.vocabulary("family/camping", read_in="family")   # what the room may see
        household.known_people()                                    # People, by their ids
    """

    language: str

    def vocabulary(self, path: str, *, read_in: str | None = None) -> Vocabulary: ...

    def known_people(self) -> People: ...

    def root(self) -> Path | None: ...


class Writer(Protocol):
    """Where a capture's card is written (`stack.filing.write`, through the
    intake's mirror). Each call returns `Filed` or `NotFiled`, never raises."""

    async def publish_capture(self, **card) -> Filed | NotFiled: ...

    async def publish_email_message(self, **message) -> Filed | NotFiled: ...

    async def correct_email(self, **correction) -> Filed | NotFiled: ...


# ── The result ────────────────────────────────────────────────────────


@dataclass
class Filing:
    """What filing a capture did.

    ``status`` is `filed`, `not_filed` (nothing written, ``reason`` says
    why), or `not_changed` (a correction that wrote nothing). ``filed`` is
    where the card is. ``classification`` is what the card now holds: the
    answer with the card's people and tags as written. ``entity`` is the
    card's place, the topic's new one after a rename. A not-changed
    correction from a degraded reading carries no classification.

        filing.status, filing.entity, filing.filed.path
        # ('filed', 'family/camping', 'family/camping/notes/2026/11/tent-3a338e.md')
    """

    status: str
    entity: str
    classification: dict | None = None
    filed: Filed | None = None
    reason: str = ""
    clean_rejected: str = ""
    captured_at: str = ""


# ── Filing ────────────────────────────────────────────────────────────


async def file(capture: Capture, policy: Policy, *, reader: Reader, writer: Writer,
               household: Household | None, refile: Refile | None = None,
               base: str | None = None, sidecar_base: str | None = None) -> Filing:
    """File `capture` into the memory, as `policy` places it; `refile` files it again as its card.

    Runs the steps of the module's diagram. ``base`` and ``sidecar_base``
    are the versions a note's or a bookmark's card and sidecar are written
    on ("" for a new file); None writes on the ones read at write time
    (`stack.filing.write.write_capture`). ``household`` is None when the intake has no memory
    to read (no topics are shown, nobody is placed by id).

    Returns a `Filing`. Never raises for the memory or the model: a write
    the curator refused is `not_filed` (`not_changed` for a correction),
    with the reason.

        filing = await file(Capture(kind="note", content=words, origin=marge),
                            Policy(bucket="family/camping", room_topics=("camping",)),
                            reader=reading, writer=mirror, household=vault)
        filing.filed.path   # 'family/camping/notes/2026/11/tent-3a338e.md'
    """
    origin = capture.origin
    entity = policy.bucket or origin.person
    resolver, shown = _vocabularies(capture, policy, household, folder=(refile.existing_path if refile else "") or entity)

    reading = await reader.read(
        entity=entity,
        topics=topics_to_choose(shown, household.language) if shown is not None and household else None)
    classification = reading.classification
    unplaced = _place_people(capture, classification, household)
    if reading.clean_rejected:
        logger.warning("[capture] clean version not filed, it {}: the card shows the words",
                       reading.clean_rejected)
    if refile and reading.degraded:
        logger.warning("[capture] correction not written: {}", reading.degraded)
        return Filing("not_changed", entity, reason=reading.degraded)

    # The tags everything downstream sees (the writer, the reply, the
    # event): a topic room's ids, the keywords, the topics chosen.
    classification = with_room_topic(classification, list(policy.room_topics) or None)
    if resolver is not None:
        classification = with_chosen_topics(classification, resolver)
    if refile and refile.own_tags is not None:
        classification["tags"] = refile.own_tags.restored(tag_list(classification))
    tags = tag_list(classification)
    captured_at = capture.captured_at or dt.date.today().isoformat()

    async def write(entity: str, tags: list[str]) -> Filed | NotFiled:
        return await _write(capture, policy, writer, refile, reading, classification,
                            entity=entity, tags=tags, captured_at=captured_at,
                            base=base, sidecar_base=sidecar_base)

    filed = await write(entity, tags)
    # A topic renamed while this capture was read: the curator refuses the
    # old folder (its card check), and the capture goes to the topic where
    # it is now, under its new id.
    if not isinstance(filed, Filed) and (renamed := _renamed(entity, household)):
        old_id, new_id = entity.rpartition("/")[2], renamed.rpartition("/")[2]
        tags = [new_id if t == old_id else t for t in tags]
        classification["tags"] = [new_id if t == old_id else t for t in classification.get("tags") or []]
        logger.info("[capture] {} was renamed while filing: filed under {}", entity, renamed)
        entity = renamed
        filed = await write(entity, tags)

    if not isinstance(filed, Filed):
        return Filing("not_changed" if refile else "not_filed", entity, classification,
                      reason=filed.reason, clean_rejected=reading.clean_rejected,
                      captured_at=captured_at)
    if unplaced:
        logger.info("[capture] {}: nobody in the household goes by {}; left off the card",
                    filed.card_id, ", ".join(unplaced))
    # The writer says what the card now holds (its people by id, its tags
    # as written): the reply, the event and the next correction's anchor
    # show that, not the model's answer.
    if filed.card is not None:
        classification = {**classification, **filed.card}
    return Filing("filed", entity, classification, filed, clean_rejected=reading.clean_rejected,
                  captured_at=captured_at)


# ── The place and the people ──────────────────────────────────────────


def _vocabularies(capture: Capture, policy: Policy, household: Household | None, *,
                  folder: str) -> tuple[Vocabulary | None, Vocabulary | None]:
    """The vocabulary that resolves the model's topic choice, and the one it is shown.

    SAFETY: the topics of the card's place, never the sender's: a person's
    private topic is shown only for a card in their bucket, and only in a
    room its people may read it in (`read_in`). The card resolves the
    model's choice; the room limits what it is shown. Email asks for none,
    as documents do.
    """
    if household is None or capture.kind == "email":
        return None, None
    return household.vocabulary(folder), household.vocabulary(folder, read_in=policy.read_in)


def _place_people(capture: Capture, classification: dict, household: Household | None) -> list[str]:
    """Put who the card is about into ``classification["persons"]``, by
    the household's ids; returns the names nobody goes by. A card naming
    nobody is about its sender (`default_person`). An email keeps its names
    as written, because its sender is the mail bot."""
    if capture.kind == "email":
        return []
    sender = capture.origin.person.capitalize() if capture.default_person else ""
    placed = (household.known_people() if household else People()).place_answer(
        classification, note=capture.kind == "note", sender=sender)
    classification["persons"] = placed.names
    return placed.unplaced


def _renamed(bucket: str, household: Household | None) -> str:
    """The bucket a renamed topic `bucket` (`family/nahprojekte`) is in now; "" when none.

    Read from the memory's topic cards (`renamed_topic`), so it is the
    answer the curator gives once the rename is committed.
    """
    root = household.root() if household is not None else None
    owner, _, topic = bucket.rpartition("/")
    found = renamed_topic(owner, topic, root=root) if root and owner else None
    return f"{owner}/{found[0]}" if found else ""


# ── The write ─────────────────────────────────────────────────────────


async def _write(capture: Capture, policy: Policy, writer: Writer, refile: Refile | None,
                 reading: Reading, classification: dict, *, entity: str, tags: list[str],
                 captured_at: str, base: str | None, sidecar_base: str | None) -> Filed | NotFiled:
    """Write the card once, at `entity`: an email's message or correction,
    else a note's or a bookmark's card (`stack.filing.write.write_capture`)."""
    origin, content = capture.origin, capture.content
    # An email folds into its thread's card (a dated section per message);
    # `capture.email` is the per-message identity the fold needs. The body
    # is always kept: the conversation is the content.
    if capture.kind == "email" and capture.email is not None:
        return await writer.publish_email_message(
            entity=entity, thread_uri=content.source_uri,
            message_id=capture.email.get("message_id"), from_addr=capture.email.get("from_addr"),
            from_name=capture.email.get("from_name"), title_hint=content.title_hint,
            body_text=content.text, classification=classification, captured_at=captured_at,
            model=reading.model, tags=tags, capture_id=origin.capture_id)
    # A correction of an email rewrites its card's head and keeps the
    # thread; it has no message of its own to fold.
    if capture.kind == "email" and refile is not None:
        return await writer.correct_email(
            path=refile.existing_path, classification=classification, tags=tags,
            words=refile.words, model=reading.model, changed_by=refile.by or origin.account)
    # A bookmark keeps its link and summary, its page text only when the
    # policy says so; a note's words are its first source.
    keep_body = capture.kind == "note" or policy.keep_body
    return await writer.publish_capture(
        entity=entity, kind=capture.kind, source_uri=content.source_uri,
        title_hint=content.title_hint, body_text=content.text if keep_body else "",
        classification=classification, captured_at=captured_at, model=reading.model, tags=tags,
        existing_path=refile.existing_path if refile else None, capture_id=origin.capture_id,
        submitter=origin.account, kept_media=capture.kept_media, caption=capture.caption,
        replies=capture.replies, clean=reading.clean,
        sources=_note_sources(capture), made=_made(capture, refile, reading),
        ops=refile.ops if refile else (), changed_by=(refile.by or None) if refile else None,
        base=base, sidecar_base=sidecar_base)


def _note_sources(capture: Capture) -> "Sequence[Source]":
    """A note's sources: a refile's, else its one original; none for a bookmark."""
    if capture.kind != "note":
        return []
    return list(capture.sources or []) or [original_of(capture)]


def _made(capture: Capture, refile: Refile | None, reading: Reading) -> dict | None:
    """How a note's interpretation was made, for its sidecar: when, by which
    model, code and prompt, and who asked for it (the sender when nobody did)."""
    if capture.kind != "note":
        return None
    return {"at": now(), "model": reading.model or "", "code_commit": code_commit(),
            "prompt_template": reading.template, **reading.made_by,
            "requested_by": (refile.requested_by if refile else "") or capture.origin.person}

