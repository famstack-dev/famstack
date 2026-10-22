"""A note's history as a rebuild reads it: a person's edits, kept apart from the machine's work.

ADR-018 §7. A note's history in git is its event log. From it a rebuild
takes the clean version before the first correction or edit, each text
edit a person made, and each field edit as an operation applied again
after the model. Whose commit counts is `activity.is_machine`; a commit
that carries a source is a correction, read from the sources instead.

The versions are what `read_versions` gives for a card; one test reads
them from a real repository, across a move.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))

from card_events import TextEdit  # noqa: E402
from card_history import Version, history_of, read_versions  # noqa: E402
from field_ops import FieldOp, FieldValues, apply_ops  # noqa: E402

from stack.frontmatter import parse  # noqa: E402

ORIGINAL = {"role": "original", "person": "homer", "time": "2026-10-20T08:00:00+00:00",
            "channel": "chat", "kind": "text", "id": "$camping:simpson"}
REPLY = {"role": "reply", "person": "homer", "time": "2026-10-20T09:00:00+00:00",
         "channel": "chat", "kind": "text", "id": "$r1:simpson"}
WORDS = "bring 3 sleeping bags, we leave at 4"
CLEAN = "- Bring 3 sleeping bags.\n- We leave at 4."


def _card(clean: str, *, tags=("camping",), replies=()) -> str:
    quotes = "\n\n".join(f"> {q}" for q in (WORDS, *replies))
    tag_lines = "".join(f"  - {t}\n" for t in tags)
    return (f"---\ntype: note\ncard_id: note-3f2a1b9c0d\ntitle: Camping\ntags:\n{tag_lines}---\n\n"
            f"# Camping\n\n{clean}\n\n## Sources\n\n{quotes}\n")


def _sidecar(*sources: dict) -> str:
    return json.dumps({"card_id": "note-3f2a1b9c0d", "sources": [ORIGINAL, *sources]})


def _version(n: int, card: str, *, author: str, subject: str, committer: str = "",
             sidecar: str = "") -> Version:
    return Version(f"c{n}", f"2026-10-2{n}T10:00:00+00:00", author, committer or author, subject,
                   card, sidecar or _sidecar())


FILED = _version(0, _card(CLEAN), author="homer", committer="archivist-bot",
                 subject="capture: Camping")


def test_a_persons_text_edit_is_a_message_and_their_tag_an_operation():
    edited = _card(CLEAN.replace("3 sleeping", "4 sleeping"), tags=("camping", "tent"))
    history = history_of([FILED, _version(1, edited, author="marge", subject="Update camping.md")])

    assert history.edits == (TextEdit("marge", "2026-10-21T10:00:00+00:00",
                                      (("Bring 3 sleeping bags.", "Bring 4 sleeping bags."),)),)
    assert history.ops == (FieldOp("marge", "2026-10-21T10:00:00+00:00", "add", "tags", "tent"),)
    assert history.prior == CLEAN


def test_a_correction_is_no_edit_and_the_rebuild_starts_before_it():
    """The card the reply's refile wrote is the model's; the reply itself is a source."""
    corrected = _card(CLEAN.replace("at 4", "at 5"), tags=("camping", "trip"), replies=("at 5",))
    history = history_of([FILED, _version(1, corrected, author="homer", committer="archivist-bot",
                                          subject="reply: Camping", sidecar=_sidecar(REPLY))])

    assert (history.edits, history.ops, history.prior) == ((), (), CLEAN)


def test_a_bots_rebuild_and_a_migration_are_no_ones_edit():
    rebuilt = _card(CLEAN.replace("We leave", "Leave"), tags=("outdoors",))
    history = history_of([FILED, _version(1, rebuilt, author="memory-bot", subject="reprocess: Camping"),
                          _version(2, rebuilt + "\n", author="memory-bot", subject="cards: kinds")])

    assert (history.edits, history.ops) == ((), ())
    assert history.prior == CLEAN.replace("We leave", "Leave"), "nothing happened: the card as it is"


def test_a_tag_a_person_removed_stays_removed_and_the_models_others_come_in():
    proposed = _card(CLEAN, tags=("camping", "tent", "outdoors"))
    ops = [FieldOp("marge", "t", "remove", "tags", "camping")]
    text, left_out = apply_ops(proposed, ops, values=FieldValues(), path="homer/notes/c.md",
                               bucket="family")

    assert parse(text)["tags"] == ["tent", "outdoors"]
    assert left_out == []


def test_an_operation_that_no_longer_fits_is_left_out_and_named():
    """Marge named Bart; Bart is no declared person any more."""
    ops = [FieldOp("marge", "t", "add", "persons", "bart"), FieldOp("marge", "t", "set", "title", "Trip")]
    text, left_out = apply_ops(_card(CLEAN), ops, values=FieldValues(person_ids=frozenset({"homer"})),
                               path="homer/notes/c.md", bucket="family")

    assert "persons" not in parse(text)
    assert parse(text)["title"] == "Trip" and "\n# Trip\n" in text
    assert left_out == ["marge: add persons=bart: bart is no declared person"]


def _git(repo: Path, *args: str, author: str = "homer") -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", f"user.name={author}",
                           "-c", f"user.email={author}@simpson", "-c", "commit.gpgsign=false",
                           *args], check=True, capture_output=True, text=True).stdout


def test_the_history_follows_the_card_when_it_moves(tmp_path):
    repo = tmp_path / "memory"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    old, new = repo / "homer" / "notes" / "camping.md", repo / "family" / "camping" / "camping.md"
    old.parent.mkdir(parents=True)
    old.write_text(_card(CLEAN))
    old.with_suffix(".json").write_text(_sidecar())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "capture: Camping")
    new.parent.mkdir(parents=True)
    _git(repo, "mv", str(old), str(new))
    _git(repo, "mv", str(old.with_suffix(".json")), str(new.with_suffix(".json")))
    _git(repo, "commit", "-q", "-m", "cards: move camping to its topic", author="memory-bot")
    new.write_text(_card(CLEAN.replace("3 sleeping", "4 sleeping")))
    _git(repo, "commit", "-q", "-am", "Update camping.md", author="marge")

    versions = read_versions(repo, "family/camping/camping.md", card_id="note-3f2a1b9c0d")

    assert [v.subject for v in versions] == ["capture: Camping", "cards: move camping to its topic",
                                             "Update camping.md"]
    assert all(v.sidecar for v in versions)
    assert [e.person for e in history_of(versions).edits] == ["marge"]
