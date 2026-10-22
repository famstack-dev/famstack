"""A note's card and its sidecar, written to a real Forgejo.

Every write of a note's card and sidecar goes through the memory
stacklet's `commit_card`: the archivist's filing and its refile from the
chat in the bot runner, `stack memory reprocess --apply` on the host.
What it promises rests on what Forgejo answers: several files in one
commit, a write against a blob hash a file is no longer at refused whole,
and a move as a delete and a create in the same commit. Only Forgejo can
say that, so these write to the Forgejo the code stacklet ships
(`forgejo_container`), through the real `ForgejoClient`, as the
archivist does: its account writes, the person is the author (ADR-015).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from .forgejo_container import OWNER, REPO, Forgejo, needs_docker, running_forgejo

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "memory"))

from card_write import CardChanged, commit_card  # noqa: E402

from stack.cards.sources import sidecar_path  # noqa: E402
from stack.forgejo import ForgejoClient  # noqa: E402

pytestmark = needs_docker

CONTAINER = "stack-test-card-write-forgejo"
ARCHIVIST = ("archivist-bot", "archivist-bot@local")
MARGE, HOMER = ("marge", "marge@simpson"), ("homer", "homer@simpson")


@pytest.fixture(scope="module")
def forgejo() -> Iterator[Forgejo]:
    with running_forgejo(CONTAINER) as forgejo:
        yield forgejo


# ── The archivist's client, and what Forgejo holds ──────────────────────

def _archivist(forgejo: Forgejo) -> ForgejoClient:
    """The client the archivist writes with: its account commits."""
    return ForgejoClient(url=forgejo.url, token=forgejo.token, committer=ARCHIVIST)


def _held(forgejo: Forgejo, path: str) -> dict:
    """The file at `path` as Forgejo holds it now: `content` and `sha`, {} when none."""
    return _archivist(forgejo).get_file(OWNER, REPO, path) or {}


def _head(forgejo: Forgejo) -> str:
    [newest] = _archivist(forgejo).list_commits(OWNER, REPO, limit=1)
    return newest["sha"]


def _last_commit(forgejo: Forgejo) -> dict:
    """The newest commit: who it is by, who wrote it, its subject, its
    parents and what it did to each file."""
    found = _archivist(forgejo).get_commit(OWNER, REPO, _head(forgejo), files=True) or {}
    commit = found["commit"]
    return {"author": (commit["author"]["name"], commit["author"]["email"]),
            "committer": (commit["committer"]["name"], commit["committer"]["email"]),
            "subject": commit["message"].splitlines()[0],
            "parents": [p["sha"] for p in found["parents"]],
            "files": {f["filename"]: f["status"] for f in found["files"]}}


# ── A note ───────────────────────────────────────────────────────────────

def _card(card_id: str, clean: str) -> str:
    return (f"---\ntype: note\ncard_id: {card_id}\ntitle: Tent is torn\n---\n\n"
            f"# Tent is torn\n\n{clean}\n\n## Sources\n\n> the tent is torn\n")


def _sidecar(card_id: str, *people: str) -> str:
    return json.dumps({"card_id": card_id, "sources": [
        {"role": "original" if n == 0 else "reply", "person": person,
         "time": f"2026-10-2{n}T08:00:00+00:00", "channel": "chat", "kind": "text",
         "id": f"${person}{n}:simpson"} for n, person in enumerate(people)]}) + "\n"


def _files_a_note(forgejo: Forgejo, path: str, card_id: str) -> None:
    """Marge's note, filed as the archivist files a new one."""
    commit_card(_archivist(forgejo), OWNER, REPO, path, _card(card_id, "- The tent is torn."),
                _sidecar(card_id, "marge"), card_sha="", sidecar_sha="",
                author=MARGE, message="capture: Tent is torn\n\nSource: (paste)")


# ── Writing ──────────────────────────────────────────────────────────────

def test_a_new_note_is_its_card_and_sidecar_in_one_commit_by_the_person(forgejo):
    """Given no note at a path, when the archivist files Marge's note there,
    then Forgejo holds its card and its sidecar as written, added in one
    commit whose author is Marge and whose committer is the archivist.
    Why: a card never exists without its sidecar, and `git log --author`
    names the person who sent it, not the bot that filed it."""
    path, before = "marge/notes/2026/10/tent-1a2b3c.md", _head(forgejo)

    _files_a_note(forgejo, path, "note-1a2b3c4d5e")

    assert _held(forgejo, path)["content"] == _card("note-1a2b3c4d5e", "- The tent is torn.")
    assert _held(forgejo, sidecar_path(path))["content"] == _sidecar("note-1a2b3c4d5e", "marge")
    assert _last_commit(forgejo) == {
        "author": MARGE, "committer": ARCHIVIST, "subject": "capture: Tent is torn",
        "parents": [before], "files": {path: "added", sidecar_path(path): "added"}}


def test_a_refile_replaces_card_and_sidecar_in_one_commit(forgejo):
    """Given Marge's note, when Homer's reply refiles it from the blobs it was
    read at, then both files change in one commit by Homer. Why: the card
    and the sources it was made from move together, or not at all."""
    path = "marge/notes/2026/10/tent-2b3c4d.md"
    _files_a_note(forgejo, path, "note-2b3c4d5e6f")
    card, side, before = _held(forgejo, path), _held(forgejo, sidecar_path(path)), _head(forgejo)
    refiled = _card("note-2b3c4d5e6f", "- The tent is torn.\n- Bring the big tarp.")

    commit_card(_archivist(forgejo), OWNER, REPO, path, refiled,
                _sidecar("note-2b3c4d5e6f", "marge", "homer"),
                card_sha=card["sha"], sidecar_sha=side["sha"],
                author=HOMER, message="reply: Tent is torn")

    assert _held(forgejo, path)["content"] == refiled
    assert _held(forgejo, sidecar_path(path))["content"] == _sidecar(
        "note-2b3c4d5e6f", "marge", "homer")
    assert _last_commit(forgejo) == {
        "author": HOMER, "committer": ARCHIVIST, "subject": "reply: Tent is torn",
        "parents": [before], "files": {path: "modified", sidecar_path(path): "modified"}}


def test_a_move_removes_the_old_files_and_adds_the_new_ones_in_one_commit(forgejo):
    """Given Marge's note filed in her bucket, when a refile writes it into
    the camping topic's folder, then the card and sidecar are gone from the
    old path and at the new one, in one commit. Why: the card's history
    follows it across the move by that one commit, which removes a file
    with the same card id (`stack.cards.history`)."""
    filed_at = "marge/notes/2026/10/tent-3c4d5e.md"
    in_its_topic = "family/camping/notes/2026/10/tent-3c4d5e.md"
    _files_a_note(forgejo, filed_at, "note-3c4d5e6f7a")
    card, side, before = (_held(forgejo, filed_at), _held(forgejo, sidecar_path(filed_at)),
                          _head(forgejo))

    commit_card(_archivist(forgejo), OWNER, REPO, in_its_topic,
                _card("note-3c4d5e6f7a", "- The tent is torn."),
                _sidecar("note-3c4d5e6f7a", "marge", "homer"),
                card_sha=card["sha"], sidecar_sha=side["sha"], moved_from=filed_at,
                author=HOMER, message="reply: Tent is torn")

    assert _held(forgejo, filed_at) == {} and _held(forgejo, sidecar_path(filed_at)) == {}
    assert _held(forgejo, in_its_topic)["content"] == _card("note-3c4d5e6f7a", "- The tent is torn.")
    assert _last_commit(forgejo)["parents"] == [before]
    assert _last_commit(forgejo)["files"] == {
        filed_at: "removed", sidecar_path(filed_at): "removed",
        in_its_topic: "added", sidecar_path(in_its_topic): "added"}


# ── Refusing ─────────────────────────────────────────────────────────────

def _refile_over(forgejo: Forgejo, path: str, card_id: str, *, card_sha: str,
                 sidecar_sha: str) -> None:
    """Homer's refile of the note at `path`, made from the blobs at `card_sha`
    and `sidecar_sha` ("" for none)."""
    commit_card(_archivist(forgejo), OWNER, REPO, path, _card(card_id, "- Bring the big tarp."),
                _sidecar(card_id, "marge", "homer"), card_sha=card_sha, sidecar_sha=sidecar_sha,
                author=HOMER, message="reply: Tent is torn")


def test_a_write_over_a_card_edited_since_is_refused_and_writes_nothing(forgejo):
    """Given Marge's note read for a refile, and Marge's edit of the card in
    Forgejo's editor after that, when the refile is written, then it is
    refused, naming the card, and Forgejo holds Marge's edit and nothing
    new. Why: a refile made from the card before her edit would undo it."""
    path = "marge/notes/2026/10/tent-4d5e6f.md"
    _files_a_note(forgejo, path, "note-4d5e6f7a8b")
    card, side = _held(forgejo, path), _held(forgejo, sidecar_path(path))
    edited = card["content"].replace("is torn.", "is torn at the seam.")
    _archivist(forgejo).put_file(OWNER, REPO, path, content=edited, message=f"Update {path}",
                                 sha=card["sha"], author_name="marge", author_email="marge@simpson")
    after_her_edit = _head(forgejo)

    with pytest.raises(CardChanged) as refused:
        _refile_over(forgejo, path, "note-4d5e6f7a8b", card_sha=card["sha"], sidecar_sha=side["sha"])

    assert refused.value.paths == (path,)
    assert _head(forgejo) == after_her_edit
    assert _held(forgejo, path)["content"] == edited
    assert _held(forgejo, sidecar_path(path))["sha"] == side["sha"]


def test_a_write_over_a_sidecar_changed_since_is_refused_and_writes_nothing(forgejo):
    """Given Marge's note read for a refile, and its sidecar changed after
    that, when the refile is written, then it is refused, naming the
    sidecar, and nothing is written. Why: the sidecar is what the next
    refile reads the note's sources from; one overwritten loses a source."""
    path = "marge/notes/2026/10/tent-5e6f7a.md"
    _files_a_note(forgejo, path, "note-5e6f7a8b9c")
    card, side = _held(forgejo, path), _held(forgejo, sidecar_path(path))
    _archivist(forgejo).put_file(OWNER, REPO, sidecar_path(path),
                                 content=_sidecar("note-5e6f7a8b9c", "marge", "lisa"),
                                 message="reply: Tent is torn", sha=side["sha"])
    after_it = _head(forgejo)

    with pytest.raises(CardChanged) as refused:
        _refile_over(forgejo, path, "note-5e6f7a8b9c", card_sha=card["sha"], sidecar_sha=side["sha"])

    assert refused.value.paths == (sidecar_path(path),)
    assert _head(forgejo) == after_it
    assert _held(forgejo, path)["sha"] == card["sha"]


def test_a_new_note_where_one_was_filed_since_is_refused_and_writes_nothing(forgejo):
    """Given a path that held no note when Homer's note was made, and
    Marge's filed there after that, when Homer's is written as a new card,
    then it is refused, naming both files, and Marge's stays. Why: a new
    card never replaces a card it did not read."""
    path = "marge/notes/2026/10/tent-6f7a8b.md"
    _files_a_note(forgejo, path, "note-6f7a8b9c0d")
    there, before = _held(forgejo, path), _head(forgejo)

    with pytest.raises(CardChanged) as refused:
        commit_card(_archivist(forgejo), OWNER, REPO, path, _card("note-0b1c2d3e4f", "- Tarp."),
                    _sidecar("note-0b1c2d3e4f", "homer"), card_sha="", sidecar_sha="",
                    author=HOMER, message="capture: Tarp")

    assert set(refused.value.paths) == {path, sidecar_path(path)}
    assert _head(forgejo) == before
    assert _held(forgejo, path)["sha"] == there["sha"]
