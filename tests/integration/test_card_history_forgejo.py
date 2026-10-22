"""A note's history across a move, read from a real Forgejo and from a clone of it.

ADR-018 §7. A refile keeps a person's edits by reading the note's
history, and that history follows the card across moves by its
`card_id`. The bot runner reads it through Forgejo's API
(`versions_from_forgejo`, as `GitMirror.card_history` calls it), the host
from its clone of the memory (`read_versions`). What Forgejo's API
answers only Forgejo can say, so these start the Forgejo the code
stacklet ships (`forgejo_container`), write each case the way the
archivist writes, through the real `ForgejoClient`, and read it back
through both readers.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from .forgejo_container import ADMIN, OWNER, REPO, Forgejo, needs_docker, running_forgejo

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "memory"))

from note_card import card_sources  # noqa: E402

from stack.cards.history import Version, history_of, read_versions, versions_from_forgejo  # noqa: E402
from stack.cards.sources import sidecar_path  # noqa: E402
from stack.forgejo import FileChange, ForgejoClient  # noqa: E402

pytestmark = needs_docker

CONTAINER = "stack-test-card-history-forgejo"
ARCHIVIST = ("archivist-bot", "archivist-bot@local")


@pytest.fixture(scope="module")
def forgejo() -> Iterator[Forgejo]:
    with running_forgejo(CONTAINER) as forgejo:
        yield forgejo


# ── The memory, written as the archivist and a person write it ───────────

def _writer(forgejo, committer: tuple[str, str]) -> ForgejoClient:
    url, token = forgejo
    return ForgejoClient(url=url, token=token, committer=committer)


def _blob(forgejo, path: str) -> str:
    return str((_writer(forgejo, ARCHIVIST).get_file(OWNER, REPO, path) or {}).get("sha") or "")


def _files(forgejo, message: str, changes: list[FileChange], *, by: str) -> None:
    """One commit of several files, authored by `by`, with the archivist
    as committer: how the archivist writes a note's card and sidecar."""
    _writer(forgejo, ARCHIVIST).change_files(OWNER, REPO, changes, message=message,
                                            author_name=by, author_email=f"{by}@springfield.example")


def _files_a_note(forgejo, path: str, card: str, sidecar: str, *, title: str, by: str) -> None:
    _files(forgejo, f"capture: {title}",
           [FileChange("create", path, card), FileChange("create", sidecar_path(path), sidecar)], by=by)


def _edits_by_hand(forgejo, path: str, card: str, *, by: str) -> None:
    """`by` changes the card in Forgejo's editor: one file, their own commit."""
    _writer(forgejo, (by, f"{by}@springfield.example")).put_file(
        OWNER, REPO, path, content=card, message=f"Update {path}", sha=_blob(forgejo, path),
        author_name=by, author_email=f"{by}@springfield.example")


def _moves(forgejo, path: str, to: str, *, message: str, by: str,
           card: str = "", sidecar: str = "") -> None:
    """One commit that removes the card and its sidecar at `path` and adds
    them at `to`: as they were, or as `card` and `sidecar` say."""
    writer = _writer(forgejo, ARCHIVIST)
    card = card or str(writer.get_file(OWNER, REPO, path)["content"])
    sidecar = sidecar or str(writer.get_file(OWNER, REPO, sidecar_path(path))["content"])
    _files(forgejo, message, [
        FileChange("delete", path, sha=_blob(forgejo, path)),
        FileChange("delete", sidecar_path(path), sha=_blob(forgejo, sidecar_path(path))),
        FileChange("create", to, card), FileChange("create", sidecar_path(to), sidecar)], by=by)


# ── The two readers ──────────────────────────────────────────────────────

def _in_the_bot_runner(forgejo, path: str, card_id: str) -> tuple[list[Version], list[str]]:
    """The versions the bot runner reads, with the client `GitMirror.card_history`
    makes, and each commit it read on its own."""
    url, token = forgejo
    client = ForgejoClient(url=url, token=token)
    asked: list[str] = []
    read_commit = client.get_commit

    def get_commit(owner, repo, sha, **kwargs):
        asked.append(sha)
        return read_commit(owner, repo, sha, **kwargs)

    client.get_commit = get_commit  # type: ignore[method-assign]
    return versions_from_forgejo(client, OWNER, REPO, path=path, card_id=card_id), asked


def _on_the_host(forgejo, tmp_path: Path, path: str, card_id: str) -> list[Version]:
    """The versions the host reads, from a clone of the same repository."""
    url, token = forgejo
    clone = tmp_path / "clone"
    authed = url.replace("http://", f"http://{ADMIN}:{token}@")
    subprocess.run(["git", "clone", "-q", f"{authed}/{OWNER}/{REPO}.git", str(clone)],
                   check=True, capture_output=True, timeout=60)
    return read_versions(clone, path, card_id=card_id)


# ── A note ───────────────────────────────────────────────────────────────

def _card(card_id: str, title: str, clean: str, words: tuple[str, ...]) -> str:
    quotes = "\n\n".join(f"> {w}" for w in words)
    return (f"---\ntype: note\ncard_id: {card_id}\ntitle: {title}\ntags:\n  - camping\n---\n\n"
            f"# {title}\n\n{clean}\n\n## Sources\n\n{quotes}\n")


def _sidecar(card_id: str, *sources: tuple[str, str, str]) -> str:
    """`sources` as (role, person, Matrix event id)."""
    return json.dumps({"card_id": card_id, "sources": [
        {"role": role, "person": person, "time": f"2026-10-2{n}T08:00:00+00:00",
         "channel": "chat", "kind": "text", "id": event}
        for n, (role, person, event) in enumerate(sources)]})


TENT = "note-7f3a9c21e4"
FILED_AT = "family/notes/2026/10/tent-7f3a.md"
IN_ITS_TOPIC = "family/camping/notes/2026/10/tent-7f3a.md"
WORDS = "the tent is torn, bring 3 sleeping bags and the repair kit"
REPLY = "and the big tarp"
CLEAN = "- The tent is torn.\n- Bring 3 sleeping bags and the repair kit."
EDITED = CLEAN.replace("3 sleeping", "4 sleeping")


def test_marges_edit_before_a_move_is_read_by_both_readers(forgejo, tmp_path):
    """Given Homer's note `Tent is torn` filed, Marge's edit of it by hand
    (4 sleeping bags, not 3), and Homer's reply, whose refile moved it into
    the camping topic's folder in one commit, when its history is read in
    the bot runner through Forgejo, then it holds all three commits and
    Marge's edit, at the cost of one commit read on its own; and the host,
    reading a clone of the same repository, gets the same versions. Why: a
    refile from the chat must keep what Marge changed, as one at the
    terminal does, however the note moved after her edit."""
    _files_a_note(forgejo, FILED_AT, _card(TENT, "Tent is torn", CLEAN, (WORDS,)),
                  _sidecar(TENT, ("original", "homer", "$tent:simpson")),
                  title="Tent is torn", by="homer")
    _edits_by_hand(forgejo, FILED_AT, _card(TENT, "Tent is torn", EDITED, (WORDS,)), by="marge")
    _moves(forgejo, FILED_AT, IN_ITS_TOPIC, message="reply: Tent is torn", by="homer",
           card=_card(TENT, "Tent is torn", EDITED + "\n- Bring the big tarp.", (WORDS, REPLY)),
           sidecar=_sidecar(TENT, ("original", "homer", "$tent:simpson"),
                            ("reply", "homer", "$tarp:simpson")))

    versions, read_on_their_own = _in_the_bot_runner(forgejo, IN_ITS_TOPIC, TENT)

    assert [(v.author, v.committer, v.subject) for v in versions] == [
        ("homer", "archivist-bot", "capture: Tent is torn"),
        ("marge", "marge", f"Update {FILED_AT}"),
        ("homer", "archivist-bot", "reply: Tent is torn")]
    [edit] = history_of(versions, read_sources=card_sources).edits
    assert (edit.person, edit.changes) == ("marge", (
        ("Bring 3 sleeping bags and the repair kit.", "Bring 4 sleeping bags and the repair kit."),))
    assert read_on_their_own == [versions[-1].sha], "one move, one commit read on its own"
    assert _on_the_host(forgejo, tmp_path, IN_ITS_TOPIC, TENT) == versions


KITE = "note-4b1c0e8d2a"
KITE_FILED_AT = "family/notes/2026/10/kite-4b1c.md"
KITE_IN_ITS_TOPIC = "family/kites/notes/2026/10/kite-4b1c.md"


def test_another_card_that_lived_at_the_old_path_is_not_this_cards_past(forgejo, tmp_path):
    """Given Bart's note at the path Homer's note is later filed at, moved
    away before, and Lisa's note filed at that path after Homer's moved
    into the kites topic's folder, when Homer's note's history is read,
    then it holds Homer's note's versions and no other's, in the bot
    runner and on the host. Why: a path names whatever card is there at
    the time; only the card id names this one."""
    def note(card_id: str, clean: str, *words: str) -> str:
        return _card(card_id, "Kite is stuck", clean, words)

    _files_a_note(forgejo, KITE_FILED_AT, note("note-b4r7b4r7b4", "- Bart's kite.", "my kite"),
                  _sidecar("note-b4r7b4r7b4", ("original", "bart", "$bart:simpson")),
                  title="Kite", by="bart")
    _moves(forgejo, KITE_FILED_AT, "bart/notes/2026/10/kite-4b1c.md",
           message=f"rename: {KITE_FILED_AT} → bart/notes/2026/10/kite-4b1c.md", by="archivist-bot")
    _files_a_note(forgejo, KITE_FILED_AT, note(KITE, "- The kite is stuck in the oak.", "kite stuck"),
                  _sidecar(KITE, ("original", "homer", "$kite:simpson")),
                  title="Kite is stuck", by="homer")
    _edits_by_hand(forgejo, KITE_FILED_AT, note(KITE, "- The kite is stuck in the elm.", "kite stuck"),
                   by="marge")
    _moves(forgejo, KITE_FILED_AT, KITE_IN_ITS_TOPIC, message="reply: Kite is stuck", by="homer")
    _files_a_note(forgejo, KITE_FILED_AT, note("note-11541154aa", "- Lisa's kite.", "lisa's kite"),
                  _sidecar("note-11541154aa", ("original", "lisa", "$lisa:simpson")),
                  title="Kite", by="lisa")

    versions, _ = _in_the_bot_runner(forgejo, KITE_IN_ITS_TOPIC, KITE)

    assert [(v.author, v.subject) for v in versions] == [
        ("homer", "capture: Kite is stuck"), ("marge", f"Update {KITE_FILED_AT}"),
        ("homer", "reply: Kite is stuck")]
    assert _on_the_host(forgejo, tmp_path, KITE_IN_ITS_TOPIC, KITE) == versions
