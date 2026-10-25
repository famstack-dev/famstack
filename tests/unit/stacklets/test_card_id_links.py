"""A card is reachable by its `card_id` for good (ADR-016 §2).

`/go/<card_id>` opens any card, on the page the older link of its kind
opens. A card whose type changed (a photo filed as a bookmark and refiled
as a note) carries a new id and lists the old one in `former_card_ids`,
and the old id still opens it. An id no card carries any more answers
with the commit that removed it; an id no card ever carried is a 404.

The family memory here is a real git repository with commits made as the
people who made them, and the wiki's copy of it (the brain) is the same
tree, so the resolver and `stack memory card` read what they read on an
instance: frontmatter for where a card is, git history for when it went.
"""

from __future__ import annotations

import json

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "tools-server"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))

from stacklets.docs.bot.matching import build_capture_event  # noqa: E402
from stacklets.docs.bot.migration_photos_to_notes import as_note  # noqa: E402
from resolver import build_redirect, card_link_answer  # noqa: E402
from stacklets.docs.bot.search_format import memory_hit_url  # noqa: E402
from stack.cards.lookup import find_by_card_id, find_card, removal_of  # noqa: E402
from stack.frontmatter import parse  # noqa: E402
from stack.links import go_card, public  # noqa: E402


def _load_card_cli():
    path = _REPO_ROOT / "stacklets" / "memory" / "cli" / "card.py"
    from stack.stacklet_code import load
    module = load(_REPO_ROOT / "stacklets" / "memory", path)
    return module


card_cli = _load_card_cli()

WIKI = "http://wiki.example"
LINKS = dict(docs_base="http://docs.example", wiki_base=WIKI, shared_bucket="family")

CAPTURE_ID = "$Efjml6ySCYyWM6xsNwRCSSEund-u2CIywGpgC8u6tvY"
NOTE_PATH = "family/camping/notes/2026/08/campsite-3a338e.md"
NOTE = f"""---
type: note
card_id: note-7f3a9c21e4
title: Campsite booked at Lake Springfield
capture_id: "{CAPTURE_ID}"
---

# Campsite booked at Lake Springfield
"""

DIARY_PATH = "family/diary/entries/2026/08/2026-08-14-3f9a0c1b2d.md"
DIARY = """---
type: diary
card_id: diary-0b6e2d91aa
title: Bart's first bike ride
date: "2026-08-14"
entry_id: 3f9a0c1b2d
---

Bart rode to the end of Evergreen Terrace.
"""

PHOTO_PATH = "marge/notes/2026/10/bart-s-drawing-454811.md"
PHOTO = """---
type: note
card_id: note-5d0c3e8a71
former_card_ids:
  - bookmark-a1b2c3d4e5
title: Bart's drawing
---
"""


class Memory:
    """The family memory on disk: a git repository whose commits name their people."""

    def __init__(self, root: Path):
        self.data_dir = root
        self.vault = root / "memory" / "vault"
        self.vault.mkdir(parents=True)
        self._git("init", "-q", "-b", "main")

    def write(self, files: dict[str, str], *, by: str, message: str,
              when: str = "2026-10-01T10:00:00") -> None:
        for path, text in files.items():
            (self.vault / path).parent.mkdir(parents=True, exist_ok=True)
            (self.vault / path).write_text(text, encoding="utf-8")
        self._git("add", "-A")
        self._commit(by, message, when)

    def remove(self, path: str, *, by: str, message: str,
               when: str = "2026-10-19T09:30:00") -> str:
        self._git("rm", "-q", path)
        self._commit(by, message, when)
        return self._git("rev-parse", "--short", "HEAD")

    def _commit(self, by: str, message: str, when: str) -> None:
        env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        subprocess.run(["git", "-C", str(self.vault), "-c", f"user.name={by}",
                        "-c", f"user.email={by}@famstack", "commit", "-q", "-m", message],
                       check=True, env=env)

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.vault), *args], check=True,
                              capture_output=True, text=True).stdout.strip()


@pytest.fixture
def memory(tmp_path) -> Memory:
    m = Memory(tmp_path)
    m.write({NOTE_PATH: NOTE, DIARY_PATH: DIARY, PHOTO_PATH: PHOTO,
             "family/people/homer.md": "---\ntype: person\ncard_id: person-homer\ntitle: Homer\n---\n",
             "family/lists/markt.md": "---\ntype: list\ncard_id: list-markt\ntitle: Markt\n---\n"},
            by="marge", message="feat(memory): marge files the summer")
    return m


def _open(memory: Memory, card_id: str):
    """What `/go/<card_id>` answers, as core's tools server asks it."""
    return card_link_answer(
        card_id, wiki_base=WIKI,
        find=lambda cid: find_by_card_id(cid, root=memory.vault),
        removal=lambda cid: removal_of(cid, repo=memory.vault),
    )


def _old_link(memory: Memory, kind: str, *rest: str) -> str | None:
    """What one of the links in use before card ids answers."""
    return build_redirect(kind, list(rest), **LINKS,
                          find_card=lambda k, cid: find_card(k, cid, root=memory.vault))


# ── /go/<card_id> ────────────────────────────────────────────────────────

class TestACardOpensByItsId:
    def test_a_note_lands_where_its_capture_link_lands(self, memory):
        assert _open(memory, "note-7f3a9c21e4") == (302, _old_link(memory, "capture", CAPTURE_ID))

    def test_a_diary_card_lands_on_its_month_at_the_card(self, memory):
        assert _open(memory, "diary-0b6e2d91aa") == (
            302, _old_link(memory, "diary", "3f9a0c1b2d"))
        assert _open(memory, "diary-0b6e2d91aa")[1].endswith("family/diary/2026/08#3f9a0c1b2d")

    def test_a_person_and_a_list_land_where_their_named_links_land(self, memory):
        assert _open(memory, "person-homer") == (302, _old_link(memory, "person", "homer"))
        assert _open(memory, "list-markt") == (302, _old_link(memory, "list", "markt"))

    def test_the_links_in_use_today_keep_working(self, memory):
        """A card with a `card_id` still answers to the link posted before it had one."""
        assert _old_link(memory, "capture", CAPTURE_ID) == \
            f"{WIKI}/{NOTE_PATH.removesuffix('.md')}"
        assert _old_link(memory, "docs", "247") == "http://docs.example/documents/247/details"
        assert _old_link(memory, "topic", "camping") == f"{WIKI}/family/camping/about"

    def test_a_card_that_moved_is_still_found(self, memory):
        moved = "family/camping/notes/2026/09/campsite-3a338e.md"
        (memory.vault / moved).parent.mkdir(parents=True)
        memory._git("mv", NOTE_PATH, moved)
        memory._commit("homer", "move the note", "2026-10-02T10:00:00")
        assert _open(memory, "note-7f3a9c21e4") == (302, f"{WIKI}/{moved.removesuffix('.md')}")


class TestAFormerId:
    def test_opens_the_card_that_had_it(self, memory):
        assert _open(memory, "bookmark-a1b2c3d4e5") == (302, _open(memory, "note-5d0c3e8a71")[1])

    def test_says_which_id_it_was_asked_for(self, memory):
        found = find_by_card_id("bookmark-a1b2c3d4e5", root=memory.vault)
        assert (found.path, found.card_id, found.former) == (PHOTO_PATH, "note-5d0c3e8a71", True)

    def test_loses_to_a_card_that_carries_it_now(self, memory):
        memory.write({"marge/bookmarks/2026/10/x.md":
                      "---\ntype: bookmark\ncard_id: bookmark-a1b2c3d4e5\ntitle: X\n---\n"},
                     by="marge", message="a bookmark")
        assert find_by_card_id("bookmark-a1b2c3d4e5", root=memory.vault).former is False


class TestACardThatWasRemoved:
    def test_answers_that_it_is_gone_with_the_commit_and_the_day(self, memory):
        sha = memory.remove(NOTE_PATH, by="homer", message="remove: the old campsite")
        assert _open(memory, "note-7f3a9c21e4") == (
            410, f"The card note-7f3a9c21e4 is gone: homer removed it on 2026-10-19 (commit {sha}).")

    def test_an_id_no_card_ever_carried_is_a_404(self, memory):
        memory.remove(NOTE_PATH, by="homer", message="remove: the old campsite")
        assert _open(memory, "note-0000000000") is None

    def test_a_card_written_again_after_its_removal_is_found(self, memory):
        memory.remove(NOTE_PATH, by="homer", message="remove")
        memory.write({NOTE_PATH: NOTE}, by="marge", message="put it back", when="2026-10-20T10:00:00")
        assert _open(memory, "note-7f3a9c21e4")[0] == 302

    def test_a_list_removed_is_not_mistaken_for_one_whose_id_it_starts(self, memory):
        """`list-markt` must not answer for a removed `list-markt-2`."""
        memory.write({"homer/lists/markt.md": "---\ntype: list\ncard_id: list-markt-2\n---\n"},
                     by="homer", message="homer's own markt list")
        memory.remove("homer/lists/markt.md", by="homer", message="remove it")
        memory.remove("family/lists/markt.md", by="marge", message="remove the shared one",
                      when="2026-10-21T10:00:00")
        assert _open(memory, "list-markt")[1].endswith("on 2026-10-21 (commit "
                                                       f"{memory._git('rev-parse', '--short', 'HEAD')}).")

    def test_text_that_is_no_card_id_is_a_404_without_reading_history(self, memory):
        assert _open(memory, "capture") is None
        assert removal_of("../../etc", repo=memory.vault) is None


class TestOpeningALinkWritesNothing:
    def test_the_memory_is_as_it_was(self, memory):
        memory.remove(NOTE_PATH, by="homer", message="remove")
        head = memory._git("rev-parse", "HEAD")
        for card_id in ("note-7f3a9c21e4", "bookmark-a1b2c3d4e5", "note-0000000000"):
            _open(memory, card_id)
        assert memory._git("rev-parse", "HEAD") == head
        assert memory._git("status", "--porcelain") == ""


# ── A card that changes its type ─────────────────────────────────────────

class TestAPhotoRefiledAsANote:
    BOOKMARK = ("---\ntype: bookmark\ncard_id: bookmark-a1b2c3d4e5\ntitle: Bart's drawing\n"
                "resource: mxc://home.test/x\n---\n\n> **Kind** bookmark\n\n![[/media/x.jpg]]\n")

    def test_gets_a_notes_id_and_lists_the_bookmarks(self):
        card, sidecar = as_note(self.BOOKMARK, PHOTO_PATH)
        fields = parse(card)
        assert fields["type"] == "note" and fields["card_id"].startswith("note-")
        assert fields["former_card_ids"] == ["bookmark-a1b2c3d4e5"]
        assert "resource" not in fields, "where it came from is in its sidecar (ADR-018)"
        assert json.loads(sidecar)["card_id"] == fields["card_id"]

    def test_its_old_link_still_opens_it(self, memory):
        card, sidecar = as_note(self.BOOKMARK, PHOTO_PATH)
        memory.write({PHOTO_PATH: card, PHOTO_PATH.removesuffix(".md") + ".json": sidecar},
                     by="archivist-bot", message="notes: file the photos filed as bookmarks as notes")
        assert _open(memory, "bookmark-a1b2c3d4e5") == (
            302, f"{WIKI}/{PHOTO_PATH.removesuffix('.md')}")


# ── What the bots post ───────────────────────────────────────────────────

class TestTheLinkABotPosts:
    def test_a_capture_filing_links_its_card_id(self):
        data = build_capture_event(NOTE_PATH, {"title": "Campsite"}, kind="note", source_uri=None,
                                   capture_id=CAPTURE_ID, card_id="note-7f3a9c21e4")["data"]
        assert public(go_card(data), "https://home.example/go") == \
            "https://home.example/go/note-7f3a9c21e4"

    def test_a_filing_from_before_card_ids_keeps_its_capture_link(self):
        data = build_capture_event(NOTE_PATH, {"title": "Campsite"}, kind="note", source_uri=None,
                                   capture_id="$abc")["data"]
        assert go_card(data) == "/capture/%24abc"

    def test_a_search_hit_links_its_card_id(self):
        hit = {"rel": DIARY_PATH, "entry_id": "3f9a0c1b2d", "card_id": "diary-0b6e2d91aa"}
        assert memory_hit_url(hit, link_base_url="https://home.example/go") == \
            "https://home.example/go/diary-0b6e2d91aa"


# ── stack memory card ────────────────────────────────────────────────────

def _card(memory: Memory, *args: str):
    return card_cli.run(list(args), None, {"data_dir": str(memory.data_dir)})


class TestStackMemoryCard:
    def test_opens_a_card_by_its_card_id(self, memory, capsys):
        result = _card(memory, "note-7f3a9c21e4")
        assert "Campsite booked at Lake Springfield" in capsys.readouterr().out
        assert "asked" not in result

    def test_answers_a_former_id_with_the_card_and_says_which_id_it_was_asked_for(
            self, memory, capsys):
        result = _card(memory, "bookmark-a1b2c3d4e5")
        out = capsys.readouterr().out
        assert "Bart's drawing" in out
        assert "Asked for bookmark-a1b2c3d4e5: an id this card had before; " \
               "it is note-5d0c3e8a71 now." in out
        assert result["asked"] == {"bookmark-a1b2c3d4e5": "Asked for bookmark-a1b2c3d4e5: an id "
                                   "this card had before; it is note-5d0c3e8a71 now."}

    def test_says_a_removed_card_is_gone_and_when(self, memory, capsys):
        sha = memory.remove(NOTE_PATH, by="homer", message="remove: the old campsite")
        with pytest.raises(SystemExit) as exit_:
            _card(memory, "note-7f3a9c21e4")
        assert exit_.value.code == 1
        assert capsys.readouterr().out.strip() == (
            f"Card note-7f3a9c21e4 is gone: homer removed it on 2026-10-19 "
            f"(@{sha}: remove: the old campsite).")

    def test_tells_no_one_of_a_removed_card_they_could_not_read(self, memory, capsys):
        """Marge's removed note is hers: Bart hears of no such card, not who removed it."""
        memory.write({"marge/notes/x.md": "---\ntype: note\ncard_id: note-1234567890\n---\n"},
                     by="marge", message="a note of her own")
        memory.remove("marge/notes/x.md", by="marge", message="remove: the birthday plan")
        with pytest.raises(SystemExit):
            _card(memory, "note-1234567890", "--audience", "bart")
        out = capsys.readouterr().out
        assert "gone" not in out and "No card 'note-1234567890'" in out


# ── The card graph ───────────────────────────────────────────────────────

class TestTheCardGraph:
    """The card browser and `stack memory find` name a card by its `card_id`."""

    def _graph(self, memory: Memory):
        from stacklets.memory.graph import load_graph
        return load_graph(memory.vault)

    def test_a_card_is_named_by_its_card_id_and_still_opens_by_its_old_name(self, memory):
        graph = self._graph(memory)
        note = graph.card("note-7f3a9c21e4")
        assert note is not None and note.id == "note-7f3a9c21e4" and note.path == NOTE_PATH
        assert graph.card("capture/Efjml6ySCY") is note

    def test_a_card_naming_a_person_links_the_persons_card(self, memory):
        memory.write({"family/notes/plant.md": "---\ntype: note\ncard_id: note-2b2b2b2b2b\n"
                      "title: Plant shift\npersons:\n  - Homer\n---\n"},
                     by="marge", message="a note about homer")
        graph = self._graph(memory)
        assert "person-homer" in graph.card("note-2b2b2b2b2b").links_out
        assert graph.person_id("homer") == "person-homer"

    def test_a_list_archive_links_its_list(self, memory):
        memory.write({"family/lists/archive/markt/2026-10.md":
                      "---\ntype: list\ncard_id: list-markt-2026-10\ntitle: Markt, 2026-10\n---\n"},
                     by="marge", message="archive")
        assert "list-markt" in self._graph(memory).card("list-markt-2026-10").links_out
