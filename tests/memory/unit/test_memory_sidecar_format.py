"""A note's sidecar, as `stack memory check-format` reads it.

A note keeps where each of its sources came from in its sidecar,
`<stem>.json` beside the card (ADR-018 §3), and the precise definition of
that file is `stacklets/memory/schemas/sidecar.json`. Somebody reading
their memory without the stack relies on that schema, so the format check
holds every sidecar to it: what the writers produce passes, and a value
outside the schema is named, with the values or the form the field takes.

Marge runs the command as `./stack memory check-format` does, through
`Stack.run_cli_command`, over the memory stacklet's clone of a fabricated
memory repo. What to assert: what she was told. The data is the Simpsons'.
"""

from __future__ import annotations

import json

import pytest

from tests.springfield import make_stack, write_instance
from tests.memory.unit.dsl import Person

NOTE = "marge/notes/2026/10/sax-lesson-7903f2"
VOICE = "homer/notes/2026/10/bart-cast-1c2d3e"
OLD = "marge/notes/2026/03/pretzel-wagon-a1b2c3"

HASH = "sha256:4168fe9973e0031ca1a1b8c445d653bc3f7c56a985eea2093c070ccc5cd8f008"

OLD_NOTE = """---
type: note
card_id: note-a1b2c3d4e5
title: Pretzel wagon
filed_by: marge
timestamp: "2026-03-14T10:22:00+00:00"
capture_id: $Pz1AbCdEfGhIjKlMnOpQrStUvWxYz012345678901
model: qwen3
---

# Pretzel wagon

> [!quote]- Original paste
> pretzel wagon franchise costs 5000

## Replies

**homer**
> it's a lot of money
"""


def _card(card_id: str, title: str, quotes: list[str]) -> str:
    sources = "\n\n".join("\n".join(f"> {line}" if line else ">" for line in q.split("\n"))
                          for q in quotes)
    return (f"---\ntype: note\ncard_id: {card_id}\ntitle: {title}\n---\n\n# {title}\n\n"
            f"{title}.\n\n## Sources\n\n{sources}\n")


@pytest.fixture(scope="module")
def as_written() -> dict[str, str]:
    """Three notes and their sidecars, written by the note writer itself
    (`stack.cards.sources.sidecar_text`) and by migration memory/0008's reading of a
    note in the format before (`legacy_note`): a chat message with a reply
    and a correction from the command line, a voice message with its media
    hash and text form, and a converted note whose reply nobody timed."""
    from stack.cards.sources import NOTE as CORRECTION
    from stack.cards.sources import ORIGINAL, REPLY, Source, sidecar_text
    from stacklets.memory.note_card import legacy_note

    chat = [Source(ORIGINAL, "marge", "2026-10-21T08:55:00+00:00", "chat", "text",
                   "$Jn4qmFFnVqBMyoUJ3vcmPRc7SoqwUf_uMZfp7zq297M",
                   quote="lisas sax lesson moved to thu 4pm, 25$ pay mr largo cash"),
            Source(REPLY, "homer", "2026-10-21T09:10:00+00:00", "chat", "text",
                   "$Hk2pQ9xLmW0aZr3T5yUvBn8cDeFgHiJkLmNoPqRsTu", quote="its 30 now, he raised it"),
            Source(CORRECTION, "marge", "2026-10-21T19:02:41+00:00", "cli", "text",
                   "cli:9f2c41d07a6b3e58", quote="in cash, not by check")]
    voice = [Source(ORIGINAL, "homer", "2025-03-03T08:12:00+01:00", "chat", "voice",
                    "$9ppnpSTkuiY9CsuUNyx73bGrhXcUmddM0KjFbpvpfqE",
                    quote="![[/media/2025/03/9ppnpSTkuiY9.m4a]]\n\nHey Bart, the cast is off.",
                    media=HASH, text={"form": "transcript"})]
    made = {"at": "2026-10-21T19:03:12+00:00", "model": "qwen3", "code_commit": "50efe696",
            "prompt_template": "7c2e91a4", "correction_template": "e1d2c3b4",
            "requested_by": "marge"}
    old = legacy_note(OLD_NOTE, path=f"{OLD}.md")
    return {
        f"{NOTE}.md": _card("note-967b020f7e", "Lisa's sax lesson", [s.quote for s in chat]),
        f"{NOTE}.json": sidecar_text(card_id="note-967b020f7e", filed_by="marge",
                                     sources=chat, made=made),
        f"{VOICE}.md": _card("note-1c2d3e4f5a", "Bart's cast is off", [s.quote for s in voice]),
        f"{VOICE}.json": sidecar_text(card_id="note-1c2d3e4f5a", filed_by="homer",
                                      sources=voice, made={"at": "2026-10-08T20:46:38Z"}),
        f"{OLD}.md": old.card,
        f"{OLD}.json": old.sidecar,
    }


@pytest.fixture
def marge(tmp_path) -> Person:
    return Person("marge", make_stack(write_instance(tmp_path)), tmp_path)


def _memory_holds(marge: Person, files: dict[str, str]) -> None:
    """The memory stacklet's clone of the memory repo, holding `files`."""
    vault = marge.stack.data / "memory" / "vault"
    for path, text in files.items():
        (vault / path).parent.mkdir(parents=True, exist_ok=True)
        (vault / path).write_text(text, encoding="utf-8")


def _changed(files: dict[str, str], path: str, change) -> dict[str, str]:
    """`files` with the sidecar at `path` changed by `change(sidecar)`."""
    sidecar = json.loads(files[path])
    change(sidecar)
    return {**files, path: json.dumps(sidecar, indent=2)}


def test_sidecars_as_the_writers_write_them_meet_the_schema(marge, as_written):
    """Given notes whose sidecars the archivist's writer and the 0008
    migration produced, when Marge checks the format, there is nothing to
    fix: the schema describes the files the stack writes."""
    _memory_holds(marge, as_written)

    marge.checks_the_format()

    assert marge.was_told("Nothing to fix")


def test_an_unknown_role_is_named_with_the_roles_there_are(marge, as_written):
    """A source's role is one of three. A role the schema does not list is
    named, at the source it is on, with the three it may be."""
    _memory_holds(marge, _changed(as_written, f"{NOTE}.json",
                                  lambda s: s["sources"][1].update(role="teacher")))

    marge.checks_the_format()

    assert marge.was_told("Sidecars that do not meet their schema:", f"{NOTE}.json",
                          "sources[1].role", "'teacher'", "original, reply, note")


def test_a_channel_or_kind_outside_its_set_is_named_with_the_set(marge, as_written):
    def change(sidecar):
        sidecar["sources"][0].update(channel="fax", kind="telegram")
    _memory_holds(marge, _changed(as_written, f"{NOTE}.json", change))

    marge.checks_the_format()

    assert marge.was_told("sources[0].channel", "chat, email, cli, agent, cards, paperless",
                          "sources[0].kind", "text, voice, image, video, file, link")


def test_a_time_that_is_no_iso_time_is_named_with_the_form_it_takes(marge, as_written):
    _memory_holds(marge, _changed(as_written, f"{NOTE}.json",
                                  lambda s: s["sources"][0].update(time="Thursday 4pm")))

    marge.checks_the_format()

    assert marge.was_told("sources[0].time", "'Thursday 4pm'", "ISO 8601")


def test_an_unknown_time_needs_a_source_marked_reconstructed(marge, as_written):
    """`unknown` says nobody recorded the time. Only a source the
    migration rebuilt from an old card may say so: a writer that has the
    message has its time."""
    _memory_holds(marge, _changed(as_written, f"{NOTE}.json",
                                  lambda s: s["sources"][1].update(time="unknown")))

    marge.checks_the_format()

    assert marge.was_told("sources[1]", "reconstructed")


def test_media_without_its_hash_prefix_is_named(marge, as_written):
    """A binary is named by its hash, `sha256:<hex>`, never by a path."""
    _memory_holds(marge, _changed(as_written, f"{VOICE}.json",
                                  lambda s: s["sources"][0].update(media="/media/2025/03/x.m4a")))

    marge.checks_the_format()

    assert marge.was_told("sources[0].media", "'/media/2025/03/x.m4a'", "sha256:")


def test_a_key_the_schema_does_not_name_is_refused(marge, as_written):
    _memory_holds(marge, _changed(as_written, f"{NOTE}.json",
                                  lambda s: s["made"].update(mood="cheerful")))

    marge.checks_the_format()

    assert marge.was_told("made.mood", "requested_by")
