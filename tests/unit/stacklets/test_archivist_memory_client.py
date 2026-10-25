"""The archivist's client of the memory: what it reads, and what it writes as whom.

The archivist files into the memory through the curator's API, as every
writer does (ADR-019). These tests run that client against the real path:
the curator's API over its card store, which pushes to a bare repository
as Forgejo (`memory_git.running_memory`). The data is the Simpsons'.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))

from stacklets.docs.bot.memory_client import CuratorDown, MemoryClient, Refused, put, remove  # noqa: E402
from memory_git import TOKEN, running_memory, version_of  # noqa: E402

NOTE = "marge/notes/2026/10/tent-3f2a1b.md"
TENT = "---\ntype: note\ncard_id: note-3f2a1b9c0d\ntitle: Tent\n---\n\n# Tent\n\nBring the big tent.\n"


@pytest.fixture
def memory(tmp_path):
    with running_memory(tmp_path / "data", {NOTE: TENT}, seeded_by="marge",
                        seeded_as="capture: Tent") as memory:
        yield memory


@pytest.fixture
def archivist(memory) -> MemoryClient:
    return MemoryClient.from_environ(memory.environ)


def test_a_file_and_the_paths_read_as_the_curator_has_them(archivist):
    note = archivist.file(NOTE)

    assert (note.path, note.text, note.version) == (NOTE, TENT, version_of(TENT))
    assert archivist.file("marge/notes/2026/10/gone.md") is None
    assert archivist.paths("marge") == [NOTE]
    assert NOTE in archivist.paths()


def test_a_cards_history_comes_with_its_id_and_path_and_none_for_no_card(archivist, memory):
    """A refile reads the card's history to keep a person's edits (ADR-018 §7)."""
    memory.edited_in_forgejo({NOTE: TENT.replace("big", "blue")}, by="marge", subject="edit: Tent")

    history = archivist.versions("note-3f2a1b9c0d")

    assert (history.card_id, history.path) == ("note-3f2a1b9c0d", NOTE)
    assert [v["subject"] for v in history.versions] == ["capture: Tent", "edit: Tent"]
    assert archivist.versions("note-0000000000") is None


def test_a_filing_is_by_the_person_it_is_for_and_committed_by_the_archivist(archivist, memory):
    """Given Marge's note, when the archivist files a reply to it for Homer,
    then the commit is Homer's and the archivist's account wrote it: a
    filing never reads as an edit Homer made in Forgejo himself (ADR-015)."""
    note = archivist.file(NOTE)

    written = archivist.commit([put(NOTE, TENT + "\nAnd the stakes.\n", base=note.version)],
                               by="homer", kind="reply", title="Tent",
                               trailers={"Source": "(paste)"})

    assert written.changed and not written.pending
    assert memory.forgejo.new_commits() == [
        "homer <homer@simpson> | archivist-bot <archivist-bot@local> | reply: Tent"]
    assert memory.forgejo.message() == "reply: Tent\n\nSource: (paste)"


def test_a_change_on_a_file_that_moved_is_refused_naming_it(archivist, memory):
    """Given Marge's edit of the note in Forgejo after the archivist read it,
    when the archivist writes on what it read, then nothing is written and
    the refusal names the note, so the filing can be read again."""
    note = archivist.file(NOTE)
    memory.edited_in_forgejo({NOTE: TENT.replace("big", "blue")})

    with pytest.raises(Refused) as refused:
        archivist.commit([remove(NOTE, base=note.version)], by="marge", kind="forget", title="Tent")

    assert (refused.value.reason, refused.value.paths) == ("changed", (NOTE,))
    assert memory.text(NOTE) == TENT.replace("big", "blue")


def test_without_the_write_token_the_refusal_says_how_to_get_one(memory):
    environ = {**memory.environ, "CARDS_WRITE_TOKEN": ""}

    with pytest.raises(Refused, match="stack up memory"):
        MemoryClient.from_environ(environ).commit(
            [put("marge/notes/new.md", "x\n", base="")], by="marge", kind="capture", title="x")


def test_no_curator_says_what_to_start():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    with pytest.raises(CuratorDown, match="stack up memory"):
        MemoryClient(f"http://127.0.0.1:{port}", token=TOKEN).file(NOTE)
