"""Every note gets its sources and a sidecar: migration memory/0008.

ADR-018, step 1. A note filed before kept where it came from in its
frontmatter and its words, caption and replies in sections of their own.
The migration converts each into a card with `## Sources` and a sidecar
beside it, without a model call, per the ADR's migration table: the words
become the `original`, a photo's embed and caption join it with the hash
the media store recorded, each reply becomes a `reply` whose time and id
nobody recorded. What the old card did not record is kept, marked and
reported. Everything else stays byte for byte.

The memory repo is a real git repository and Forgejo a real HTTP server
that applies each commit to it (`test_memory_card_links`), so a second
run reads what the first one wrote.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).parent))

from stacklets.memory.format_check import format_report  # noqa: E402
from stacklets.memory.graph import load_graph  # noqa: E402
from stacklets.memory.lib import BOT_USERNAME, give_notes_their_sources  # noqa: E402
from stack.cards.sources import quotes_of, sources_of  # noqa: E402
from stack.frontmatter import parse  # noqa: E402
from test_memory_card_links import _clone, _commits, _git, _read, _instance, curator# noqa: E402, F401

TRIP = "marge/notes/2026/10/trip-3a338e.md"
PHOTO = "bart/notes/2026/10/drawing-454811.md"
UNRECORDED = "bart/notes/2026/10/sketch-7f3a9c.md"
TAINTED = "homer/notes/2026/10/tires-0b6e2d.md"
OWN = "homer/notes/grill.md"
DONE = "lisa/notes/2026/10/sax-1a2b3c.md"
BOOKMARK = "marge/bookmarks/2026/10/recipe-c0ffee.md"
DIARY = "family/diary/entries/2026/10/2026-10-12-b48902f590.md"

TRIP_TEXT = """---
type: note
card_id: note-3f9a0c1b70
title: Trip to Itchy & Scratchy Land
date: "2026-10-12"
persons:
  - homer
filed_by: marge
tags:
  - trip
capture_id: "$trip:simpson"
model: Qwen3.6-35B-A3B-UD-MLX-4bit
timestamp: "2026-10-12T08:55:00Z"
---

# Trip to Itchy & Scratchy Land

> **About** [Homer](../../../../family/people/homer.md)
> **Captured** 2026-10-12 · **Kind** note

Homer calls the hotel about the late check-in.

> [!quote]- Original paste
> homer calls the hotel abt the late checkin

## Replies

**Bart**
> and my swimsuit

**Marge**
> it is Friday, not Thursday
"""

PHOTO_TEXT = """---
type: note
card_id: note-3f9a0c1b71
title: Bart's drawing
date: "2026-10-13"
filed_by: bart
tags:
  - drawing
resource: "mxc://simpson/p1"
capture_id: "$p1:simpson"
timestamp: "2026-10-13T16:00:00Z"
---

# Bart's drawing

> **Captured** 2026-10-13 · **Kind** note
> **Source** <mxc://simpson/p1>
> **File** [IMG_1.png](/media/2026/10/p1.png)

> [!summary]
> A crayon drawing of Santa's Little Helper.

![[/media/2026/10/p1.jpg]]

## Caption

Bart drew this at school
"""

UNRECORDED_TEXT = PHOTO_TEXT.replace("note-3f9a0c1b71", "note-3f9a0c1b72").replace(
    "p1", "p2").replace("Bart's drawing", "Bart's sketch")

TAINTED_TEXT = """---
type: note
card_id: note-3f9a0c1b73
title: Winter tires
date: "2026-10-11"
filed_by: homer
capture_id: "$tires:simpson"
timestamp: "2026-10-11T07:00:00Z"
---

# Winter tires

> [!quote]- Original paste
> new tires for the plymouth b4 winter
>
> Existing tags on this entry: car, winter
"""

OWN_TEXT = "---\ntype: note\ncard_id: note-3f9a0c1b74\ntitle: Grill\n---\n\n# Grill\n\nBuy charcoal.\n"
DONE_TEXT = ("---\ntype: note\ncard_id: note-3f9a0c1b75\ntitle: Sax lesson\n---\n\n# Sax lesson\n\n"
             "Thursday, 4 pm.\n\n## Sources\n\n> sax thu 4pm\n")
DONE_SIDECAR = json.dumps({"card_id": "note-3f9a0c1b75", "filed_by": "lisa", "sources": [
    {"role": "original", "person": "lisa", "time": "2026-10-14T09:00:00+00:00", "channel": "chat",
     "kind": "text", "id": "$sax:simpson"}], "made": {}}, indent=2) + "\n"
BOOKMARK_TEXT = ("---\ntype: bookmark\ncard_id: bookmark-3f9a0c1b76\ntitle: Krusty's chili\n"
                 "capture_id: \"$recipe:simpson\"\nresource: \"https://example.org/chili\"\n"
                 "timestamp: \"2026-10-10T10:00:00Z\"\n---\n\n# Krusty's chili\n\n"
                 "## Replies\n\n**Homer**\n> too spicy\n")
DIARY_TEXT = ("---\ntype: diary\ncard_id: diary-3f9a0c1b77\ntitle: Skateboard\ndate: \"2026-10-12\"\n"
              "entry_id: b48902f590\ntimestamp: \"2026-10-12T08:00:00Z\"\n---\n\n# Skateboard\n\n"
              "## Transcript\n\n> Bart rode his skateboard to school.\n")

PEOPLE = {f"family/people/{who}.md": f"---\ntype: person\ncard_id: person-{who}\n"
          f"title: {who.capitalize()} Simpson\n---\n\n# {who.capitalize()} Simpson\n"
          for who in ("homer", "marge", "bart", "lisa")}

VAULT = {**PEOPLE, TRIP: TRIP_TEXT, PHOTO: PHOTO_TEXT, UNRECORDED: UNRECORDED_TEXT, TAINTED: TAINTED_TEXT,
         OWN: OWN_TEXT, DONE: DONE_TEXT, DONE.removesuffix(".md") + ".json": DONE_SIDECAR,
         BOOKMARK: BOOKMARK_TEXT, DIARY: DIARY_TEXT}
LEFT_ALONE = (OWN, DONE, DONE.removesuffix(".md") + ".json", BOOKMARK, DIARY, *PEOPLE)
PHOTO_SHA = "4168fe9973e0031ca1a1b8c445d653bc3f7c56a985eea2093c070ccc5cd8f008"
TRAILER = "Migration: memory/0008"


def _side(path: str) -> str:
    return path.removesuffix(".md") + ".json"


@pytest.fixture
def memory(tmp_path) -> Path:
    """The family/memory repository on Forgejo, holding the fabricated vault."""
    repo = tmp_path / "forgejo" / "memory"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    for path, text in VAULT.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed the vault")
    _git(repo, "update-server-info")
    return repo


@pytest.fixture
def media_root(tmp_path) -> Path:
    """The media store, with the record `keep` wrote for Bart's drawing only."""
    root = tmp_path / "media"
    (root / "2026" / "10").mkdir(parents=True)
    (root / "2026" / "10" / "p1.json").write_text(json.dumps(
        {"event_id": "$p1:simpson", "sha256": PHOTO_SHA, "mime": "image/png"}))
    return root


class TestNotesGetTheirSourcesOnce:

    @pytest.fixture
    def found(self, memory, curator, media_root, tmp_path):  # noqa: F811
        found, sha = give_notes_their_sources(curator.api, curator.clone,
                                              media_root=media_root, trailer=TRAILER)
        assert sha == _git(memory, "rev-parse", "HEAD")
        return found

    def test_one_commit_by_the_memory_bot_with_every_card_and_sidecar(self, found, memory):
        assert _commits(memory) == 1
        assert _git(memory, "log", "-1", "--format=%an|%s").strip() == \
            f"{BOT_USERNAME}|cards: give 4 note(s) their sources and a sidecar"
        assert TRAILER in _git(memory, "log", "-1", "--format=%B")
        changed = set(_git(memory, "show", "--name-only", "--format=", "HEAD").split())
        assert changed == {p for n in (TRIP, PHOTO, UNRECORDED, TAINTED) for p in (n, _side(n))}

    def test_a_notes_card_keeps_only_what_it_is_about(self, found, memory):
        card = _read(memory, TRIP)
        assert set(parse(card)) == {"type", "card_id", "title", "date", "persons", "tags"}
        assert "Homer calls the hotel about the late check-in." in card
        assert "Original paste" not in card and "## Replies" not in card
        assert quotes_of(card) == ["homer calls the hotel abt the late checkin",
                                   "and my swimsuit", "it is Friday, not Thursday"]

    def test_its_words_become_the_original_from_its_old_fields(self, found, memory):
        sidecar = json.loads(_read(memory, _side(TRIP)))
        assert sidecar["card_id"] == "note-3f9a0c1b70" and sidecar["filed_by"] == "marge"
        assert sidecar["sources"][0] == {"role": "original", "person": "marge",
                                         "time": "2026-10-12T08:55:00Z", "channel": "chat",
                                         "kind": "text", "id": "$trip:simpson"}
        assert sidecar["made"] == {"at": "2026-10-12T08:55:00Z",
                                   "model": "Qwen3.6-35B-A3B-UD-MLX-4bit", "requested_by": "marge"}

    def test_its_replies_become_sources_without_a_time_marked_and_reported(self, found, memory):
        replies = json.loads(_read(memory, _side(TRIP)))["sources"][1:]
        assert [(r["role"], r["person"], r["time"], r["id"], r["reconstructed"]) for r in replies] == [
            ("reply", "bart", "unknown", "unknown", True), ("reply", "marge", "unknown", "unknown", True)]
        assert any(TRIP in line and "without a time" in line for line in found.left)

    def test_quote_n_belongs_to_sources_n(self, found, memory):
        for note in (TRIP, PHOTO, UNRECORDED, TAINTED):
            sources_of(_read(memory, note), json.loads(_read(memory, _side(note))))

    def test_a_photo_is_embedded_in_its_quote_with_its_caption_and_its_hash(self, found, memory):
        card = _read(memory, PHOTO)
        assert quotes_of(card) == ["![[/media/2026/10/p1.jpg]]\n\nBart drew this at school"]
        assert "> [!summary]\n> A crayon drawing of Santa's Little Helper." in card
        assert "**File**" not in card and "## Caption" not in card and "resource" not in parse(card)
        [original] = json.loads(_read(memory, _side(PHOTO)))["sources"]
        assert (original["kind"], original["media"]) == ("image", f"sha256:{PHOTO_SHA}")

    def test_a_photo_the_store_has_no_record_of_is_reported(self, found, memory):
        [original] = json.loads(_read(memory, _side(UNRECORDED)))["sources"]
        assert "media" not in original
        assert any(UNRECORDED in line and "media store" in line for line in found.left)

    def test_words_with_prompt_text_are_kept_marked_and_reported(self, found, memory):
        assert "Existing tags on this entry: car, winter" in quotes_of(_read(memory, TAINTED))[0]
        assert json.loads(_read(memory, _side(TAINTED)))["sources"][0]["reconstructed"] is True
        assert any(TAINTED in line and "prompt text" in line for line in found.left)

    def test_everything_else_stays_byte_for_byte(self, found, memory):
        for path in LEFT_ALONE:
            assert _read(memory, path) == VAULT[path], path

    def test_afterwards_the_format_check_finds_no_note_out_of_shape(self, found, memory):
        assert format_report(load_graph(memory))["note_sources"] == []

    def test_run_again_changes_nothing_and_makes_no_commit(self, found, memory, curator,  # noqa: F811
                                                          media_root, tmp_path):
        head = _git(memory, "rev-parse", "HEAD")
        again, sha = give_notes_their_sources(curator.api, curator.clone,
                                              media_root=media_root, trailer=TRAILER)
        assert (again.changes, sha) == ({}, None)
        assert _commits(memory) == 1 and _git(memory, "rev-parse", "HEAD") == head


def test_stack_up_memory_runs_it_once_and_records_its_commit(memory, curator, media_root,  # noqa: F811
                                                             tmp_path):
    """`0008_give_notes_their_sources.py`, run by the framework's runner the
    way `stack up memory` runs it: through the curator, one commit."""
    from stack.migrations import MigrationContext, Migrations

    stck = _instance(tmp_path, curator)
    migrations = Migrations("memory", _REPO_ROOT / "stacklets" / "memory", tmp_path / "data" / "memory")
    outcome = migrations.run(
        lambda m: MigrationContext(m, stack=stck, stacklet_id="memory",
                                   env={"MEDIA_ARCHIVE_DIR": str(media_root)}),
        lambda msg: None)

    assert "memory/0008" in outcome["applied"]
    assert migrations.record().applied["0008"]["commit"] == _git(
        memory, "log", "-1", "--format=%H", "--grep=Migration: memory/0008")
    assert json.loads(_read(memory, _side(PHOTO)))["sources"][0]["media"] == f"sha256:{PHOTO_SHA}"
