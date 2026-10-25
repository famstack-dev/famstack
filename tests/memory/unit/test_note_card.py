"""A note card and its sidecar (ADR-018): what it is about, and where it came from.

The card keeps one blockquote per source under `## Sources`; the sidecar
keeps where each came from, in the same order. A refile reads exactly
these (`note_sources`), never the interpretation, and a note filed in the
format before reads the way migration memory/0008 converts it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from stacklets.memory.note_card import is_legacy_note, legacy_note, note_sources  # noqa: E402
from stack.cards.sources import (  # noqa: E402
    NOTE, ORIGINAL, REPLY, Source, SourcesMismatch, corrections_of, quotes_of, read_sidecar,
    render_sources, sidecar_path, sidecar_text, sources_of, words_of,
)
from stack.frontmatter import parse  # noqa: E402

CARD = """---
type: note
card_id: note-7f3a9c21e4
title: Trip
---

# Trip

Homer calls the hotel on Thursday.

## Sources

> homer calls the hotel thu

> and my swimsuit
"""
SIDECAR = {"card_id": "note-7f3a9c21e4", "filed_by": "marge", "sources": [
    {"role": "original", "person": "marge", "time": "2026-10-20T08:00:00+00:00",
     "channel": "chat", "kind": "text", "id": "$n1:simpson"},
    {"role": "reply", "person": "bart", "time": "2026-10-20T09:00:00+00:00",
     "channel": "chat", "kind": "text", "id": "$r1:simpson"}]}

OLD = """---
type: note
card_id: note-7f3a9c21e4
title: Trip
date: "2026-10-20"
persons:
  - homer
filed_by: marge
tags:
  - trip
capture_id: "$n1:simpson"
model: Qwen
timestamp: "2026-10-20T08:00:00Z"
---

# Trip

> **About** [Homer](../../people/homer.md)
> **Captured** 2026-10-20 · **Kind** note

Homer calls the hotel on Thursday.

> [!quote]- Original paste
> homer calls the hotel thu

## Replies

**Marge**
> it is Friday
"""


def _event(event_id, sender, body, ts=1_761_000_000_000):
    return {"type": "m.room.message", "event_id": event_id, "sender": sender,
            "origin_server_ts": ts, "content": {"msgtype": "m.text", "body": body}}


class TestQuoteNBelongsToSourcesN:

    def test_each_record_is_paired_with_its_quote(self):
        sources = sources_of(CARD, SIDECAR)
        assert [(s.role, s.person, s.quote) for s in sources] == [
            (ORIGINAL, "marge", "homer calls the hotel thu"), (REPLY, "bart", "and my swimsuit")]

    def test_a_different_count_is_refused_rather_than_guessed(self):
        with pytest.raises(SourcesMismatch):
            sources_of(CARD.replace("\n> and my swimsuit\n", ""), SIDECAR)

    def test_what_is_written_reads_back_the_same(self):
        sources = sources_of(CARD, SIDECAR)
        text = sidecar_text(card_id="note-7f3a9c21e4", filed_by="marge", sources=sources,
                            made={"at": "2026-10-20T08:00:05+00:00", "model": ""})
        assert read_sidecar(text)["sources"] == SIDECAR["sources"]
        assert read_sidecar(text)["made"] == {"at": "2026-10-20T08:00:05+00:00"}
        assert quotes_of("# T\n\n" + render_sources(sources) + "\n") == [s.quote for s in sources]

    def test_a_quote_keeps_its_blank_lines_and_its_embed(self):
        photo = Source(ORIGINAL, "marge", "t", "chat", "image", "$p",
                       quote="![[/media/2026/10/p.jpg]]\n\nBart drew this")
        assert render_sources([photo]) == "## Sources\n\n> ![[/media/2026/10/p.jpg]]\n>\n> Bart drew this"
        assert (photo.words, photo.media_link) == ("Bart drew this", "/media/2026/10/p.jpg")

    def test_the_sidecar_sits_beside_its_card(self):
        assert sidecar_path("family/notes/2026/10/trip-7903f2.md") == \
            "family/notes/2026/10/trip-7903f2.json"


class TestWhatARefileReads:

    def test_the_cards_sources_never_its_interpretation(self):
        sources = note_sources(CARD, SIDECAR)
        assert words_of(sources) == "homer calls the hotel thu"
        assert corrections_of(sources) == "Bart: and my swimsuit"
        assert "Thursday" not in " ".join(s.quote for s in sources)

    def test_a_reply_in_the_thread_the_card_lacks_is_appended(self):
        thread = [_event("$f", "@archivist-bot:simpson", "Filed: Trip"),
                  _event("$r1:simpson", "@bart:simpson", "and my swimsuit"),
                  _event("$r2", "@lisa:simpson", "> <@marge:simpson> homer\n\nme too")]
        added = note_sources(CARD, SIDECAR, thread=thread)[2:]
        assert [(s.role, s.person, s.id, s.quote, s.channel) for s in added] == [
            (REPLY, "lisa", "$r2", "me too", "chat")]
        assert added[0].time == "2025-10-20T22:40:00+00:00"

    def test_the_chat_never_replaces_the_original(self):
        sources = note_sources(CARD, SIDECAR, chat=_event("$n1:simpson", "@marge:simpson", "edited"))
        assert words_of(sources) == "homer calls the hotel thu"

    def test_a_correction_given_with_the_refile_is_a_note_by_the_person(self):
        last = note_sources(CARD, SIDECAR, msg="it is Friday", by="@marge:simpson",
                            at="2026-10-21T09:00:00+00:00")[-1]
        assert (last.role, last.person, last.time, last.channel, last.quote) == (
            NOTE, "marge", "2026-10-21T09:00:00+00:00", "cli", "it is Friday")
        assert last.id.startswith("cli:")


class TestANoteInTheOlderFormat:
    """Converted without a model call, per ADR-018's migration table."""

    def test_its_words_become_the_original_from_its_old_fields(self):
        legacy = legacy_note(OLD, path="family/trip/notes/2026/10/trip.md")
        original = legacy.sources[0].record()
        assert original == {"role": "original", "person": "marge", "time": "2026-10-20T08:00:00Z",
                            "channel": "chat", "kind": "text", "id": "$n1:simpson"}

    def test_its_replies_become_sources_with_no_time_or_id_marked_and_reported(self):
        legacy = legacy_note(OLD, path="trip.md")
        reply = legacy.sources[1].record()
        assert reply == {"role": "reply", "person": "marge", "time": "unknown", "channel": "chat",
                         "kind": "text", "id": "unknown", "reconstructed": True}
        assert any("without a time" in p for p in legacy.problems)

    def test_the_card_keeps_what_it_is_about_and_its_sources(self):
        card = legacy_note(OLD).card
        assert set(parse(card)) == {"type", "card_id", "title", "date", "persons", "tags"}
        assert "Homer calls the hotel on Thursday." in card
        assert card.endswith("## Sources\n\n> homer calls the hotel thu\n\n> it is Friday\n")
        assert "Original paste" not in card and "**Captured**" not in card
        assert "> **About** [Homer]" in card

    def test_where_it_was_made_goes_to_made(self):
        made = json.loads(legacy_note(OLD).sidecar)["made"]
        assert made == {"at": "2026-10-20T08:00:00Z", "model": "Qwen", "requested_by": "marge"}

    def test_a_photo_note_becomes_one_quote_with_its_embed_caption_and_hash(self):
        photo = OLD.replace("> [!quote]- Original paste\n> homer calls the hotel thu\n", "").replace(
            "Homer calls the hotel on Thursday.\n", "> [!summary]\n> A drawing.\n\n"
            "![[/media/2026/10/p.jpg]]\n").replace(
            "> **Captured** 2026-10-20 · **Kind** note",
            "> **Captured** 2026-10-20 · **Kind** note\n> **File** [IMG.png](/media/2026/10/p.png)"
        ).replace("## Replies", "## Caption\n\nBart drew this\n\n## Replies")
        legacy = legacy_note(photo, media_sha={"/media/2026/10/p.png": "abc"}.get)
        original = legacy.sources[0]
        assert (original.kind, original.media) == ("image", "sha256:abc")
        assert original.quote == "![[/media/2026/10/p.jpg]]\n\nBart drew this"
        assert "**File**" not in legacy.card and "## Caption" not in legacy.card

    def test_prompt_text_in_its_words_is_kept_marked_and_reported(self):
        tainted = OLD.replace("> homer calls the hotel thu",
                              "> homer calls the hotel thu\n>\n> Existing tags on this entry: trip")
        legacy = legacy_note(tainted, path="trip.md")
        assert "Existing tags on this entry: trip" in legacy.sources[0].quote
        assert legacy.sources[0].reconstructed
        assert any("prompt text" in p for p in legacy.problems)

    def test_a_note_written_in_the_memory_is_no_old_format_note(self):
        assert is_legacy_note(OLD)
        assert not is_legacy_note("---\ntype: note\ntitle: Own\n---\n\n# Own\n\nMy words.\n")
        assert not is_legacy_note(CARD)
