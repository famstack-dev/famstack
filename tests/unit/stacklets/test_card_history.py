"""A note's history as a rebuild reads it: a person's edits, kept apart from the machine's work.

ADR-018 §7. A note's history in git is its event log. From it a rebuild
takes the clean version before the first correction or edit, each text
edit a person made, and each field edit as an operation applied again
after the model. Whose commit counts is `stack.cards.commits.is_machine`; a commit
that carries a source is a correction, read from the sources instead.

The versions are what the curator's card store reads for a card
(`CardStore.history`), the history every other reader asks it for; the
last tests read them from a real repository, across moves.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))
# `memory.cards`, the curator's card store.
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))

from card_fields import SCHEMA_DIR  # noqa: E402
from memory.cards import CardStore  # noqa: E402
from note_card import card_sources  # noqa: E402

from stack.cards.events import TextEdit  # noqa: E402
from stack.cards.fields import FieldOp, FieldValues, apply_ops  # noqa: E402
from stack.cards.history import Version, history_of  # noqa: E402
from stack.cards.sources import sidecar_path  # noqa: E402
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
    history = history_of([FILED, _version(1, edited, author="marge", subject="Update camping.md")],
                         read_sources=card_sources)

    assert history.edits == (TextEdit("marge", "2026-10-21T10:00:00+00:00",
                                      (("Bring 3 sleeping bags.", "Bring 4 sleeping bags."),)),)
    assert history.ops == (FieldOp("marge", "2026-10-21T10:00:00+00:00", "add", "tags", "tent"),)
    assert history.prior == CLEAN


def test_a_correction_is_no_edit_and_the_rebuild_starts_before_it():
    """The card the reply's refile wrote is the model's; the reply itself is a source."""
    corrected = _card(CLEAN.replace("at 4", "at 5"), tags=("camping", "trip"), replies=("at 5",))
    history = history_of([FILED, _version(1, corrected, author="homer", committer="archivist-bot",
                                          subject="reply: Camping", sidecar=_sidecar(REPLY))],
                         read_sources=card_sources)

    assert (history.edits, history.ops, history.prior) == ((), (), CLEAN)


def test_a_bots_rebuild_and_a_migration_are_no_ones_edit():
    rebuilt = _card(CLEAN.replace("We leave", "Leave"), tags=("outdoors",))
    history = history_of([FILED, _version(1, rebuilt, author="memory-bot", subject="reprocess: Camping"),
                          _version(2, rebuilt + "\n", author="memory-bot", subject="cards: kinds")],
                         read_sources=card_sources)

    assert (history.edits, history.ops) == ((), ())
    assert history.prior == CLEAN.replace("We leave", "Leave"), "nothing happened: the card as it is"


def test_a_quote_a_bot_wrote_is_no_ones_correction():
    """A transcribe step replaces the quote of its own source (ADR-018 §4):
    the machine's words as sent, not a person's correction of them."""
    transcribed = _card(CLEAN).replace(f"> {WORDS}", "> bring three sleeping bags, we leave at four")
    history = history_of([FILED, _version(1, transcribed, author="memory-bot",
                                          subject="transcribe: Camping")],
                         read_sources=card_sources)

    assert (history.quotes_as_sent, history.quote_edits) == ((), ())


def test_a_quote_the_machine_wrote_again_after_a_persons_edit_is_not_put_back():
    """Marge corrected the quote, then a better transcriber wrote it again.
    Her edit is still her note, and the newer words as sent stay: putting
    the older ones back would undo the transcriber."""
    edited = _card(CLEAN).replace(f"> {WORDS}", "> bring 4 sleeping bags, we leave at 4")
    transcribed = _card(CLEAN).replace(f"> {WORDS}", "> bring three sleeping bags, we leave at four")
    history = history_of([FILED, _version(1, edited, author="marge", subject="Update camping.md"),
                          _version(2, transcribed, author="memory-bot", subject="transcribe: Camping")],
                         read_sources=card_sources)

    assert history.quotes_as_sent == ()
    assert [(e.person, e.words) for e in history.quote_edits] == [
        ("marge", "bring 4 sleeping bags, we leave at 4")]


def test_a_tag_a_person_removed_stays_removed_and_the_models_others_come_in():
    proposed = _card(CLEAN, tags=("camping", "tent", "outdoors"))
    ops = [FieldOp("marge", "t", "remove", "tags", "camping")]
    text, left_out = apply_ops(proposed, ops, values=FieldValues(schemas=SCHEMA_DIR),
                               path="homer/notes/c.md", bucket="family")

    assert parse(text)["tags"] == ["tent", "outdoors"]
    assert left_out == []


def test_an_operation_that_no_longer_fits_is_left_out_and_named():
    """Marge named Bart; Bart is no declared person any more."""
    ops = [FieldOp("marge", "t", "add", "persons", "bart"), FieldOp("marge", "t", "set", "title", "Trip")]
    values = FieldValues(schemas=SCHEMA_DIR, person_ids=frozenset({"homer"}))
    text, left_out = apply_ops(_card(CLEAN), ops, values=values,
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

    versions = _history(repo, "family/camping/camping.md")

    assert [v.subject for v in versions] == ["capture: Camping", "cards: move camping to its topic",
                                             "Update camping.md"]
    assert all(v.sidecar for v in versions)
    assert [e.person for e in history_of(versions, read_sources=card_sources).edits] == ["marge"]


# ── A card's history across moves ─────────────────────────────────────

CARD_ID = "note-3f2a1b9c0d"
FILED_AT = "homer/notes/2026/10/tent.md"
IN_ITS_TOPIC = "family/camping/notes/2026/10/tent.md"


def _history(repo: Path, path: str, card_id: str = CARD_ID) -> list[Version]:
    """The card's versions as the curator's card store reads them from its clone."""
    return CardStore(repo, remote="origin", server_name="simpson").history(path, card_id)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "memory"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    return repo


def _commit(repo: Path, subject: str, *, by: str, committer: str = "",
            write: dict[str, str] | None = None, move: dict[str, str] | None = None) -> None:
    """One commit by `by`, written by `committer` (a bot writing for a
    person), that writes `write` (path to text) and moves each file of
    `move` to its new path, card and sidecar together."""
    for old, new in (move or {}).items():
        (repo / new).parent.mkdir(parents=True, exist_ok=True)
        (repo / old).rename(repo / new)
    for path, text in (write or {}).items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    by_committer = ["-c", f"user.name={committer or by}", "-c", f"user.email={committer or by}@simpson"]
    subprocess.run(["git", "-C", str(repo), *by_committer, "-c", "commit.gpgsign=false", "commit",
                    "-q", f"--author={by} <{by}@simpson>", "-m", subject],
                   check=True, capture_output=True)


def _filed(repo: Path, path: str, card: str, *, by: str = "homer") -> None:
    _commit(repo, "capture: Camping", by=by, committer="archivist-bot",
            write={path: card, sidecar_path(path): _sidecar()})


def _moved(repo: Path, path: str, to: str, *, card: str = "", sidecar: str = "",
           subject: str = "", by: str = "archivist-bot", committer: str = "") -> None:
    """The archivist moves the card at `path` and its sidecar to `to`, in
    one commit, as Forgejo writes several files; `card` and `sidecar` are
    what that commit writes there instead, when it rewrites them too."""
    _commit(repo, subject or f"rename: {path} → {to}", by=by, committer=committer,
            move={path: to, sidecar_path(path): sidecar_path(to)},
            write={**({to: card} if card else {}), **({sidecar_path(to): sidecar} if sidecar else {})})


def test_a_move_that_rewrote_the_card_is_followed_by_its_card_id(tmp_path):
    """Given Marge's edit of Homer's note, and then Homer's reply, whose
    refile moved the note into the camping topic's folder and wrote it
    anew in the same commit, when its history is read, then
    Marge's edit is in it. Why: git follows a move by how alike the file
    is before and after, and a refile can leave too little alike; the
    card id names the card whatever it says."""
    repo = _repo(tmp_path)
    _filed(repo, FILED_AT, _card(CLEAN))
    _commit(repo, "Update tent.md", by="marge",
            write={FILED_AT: _card(CLEAN.replace("3 sleeping", "4 sleeping"))})
    rewritten = _card("- Camping at Lake Springfield on Saturday and Sunday.\n"
                      "- Ned Flanders lends us his big family tent and two lamps.\n"
                      "- Pack four sleeping bags, the cooler and the first aid kit.\n"
                      "- The car leaves the house at five in the afternoon.",
                      tags=("lake-springfield", "outdoors", "weekend-trip"),
                      replies=("we leave at 5, not 4, and it is lake springfield",))
    _moved(repo, FILED_AT, IN_ITS_TOPIC, card=rewritten.replace("title: Camping", "title: Lake trip"),
           sidecar=_sidecar(REPLY), subject="reply: Lake trip", by="homer", committer="archivist-bot")

    versions = _history(repo, IN_ITS_TOPIC)

    assert [v.subject for v in versions] == ["capture: Camping", "Update tent.md", "reply: Lake trip"]
    assert [e.person for e in history_of(versions, read_sources=card_sources).edits] == ["marge"]


def test_another_card_that_lived_at_the_old_path_is_not_this_cards_past(tmp_path):
    """Given Bart's note at the path Homer's note is later filed at, moved
    away before, and Lisa's note filed there after Homer's moved into the
    camping topic's folder, when Homer's note's history is read, then it
    holds Homer's note's versions and no other's. Why:
    a path names whatever card is there at the time; only the card id
    names this one."""
    repo = _repo(tmp_path)
    bart = _card(CLEAN.replace("3 sleeping", "2 sleeping")).replace(CARD_ID, "note-b4r7b4r7b4")
    _commit(repo, "capture: Tent", by="bart", committer="archivist-bot",
            write={FILED_AT: bart, sidecar_path(FILED_AT): _sidecar().replace(CARD_ID, "note-b4r7b4r7b4")})
    _moved(repo, FILED_AT, "bart/notes/2026/10/tent.md")
    _filed(repo, FILED_AT, _card(CLEAN))
    _commit(repo, "Update tent.md", by="marge",
            write={FILED_AT: _card(CLEAN.replace("3 sleeping", "4 sleeping"))})
    _moved(repo, FILED_AT, IN_ITS_TOPIC)
    lisa = bart.replace("note-b4r7b4r7b4", "note-11541154aa")
    _commit(repo, "capture: Tent", by="lisa", committer="archivist-bot",
            write={FILED_AT: lisa, sidecar_path(FILED_AT): _sidecar().replace(CARD_ID, "note-11541154aa")})

    versions = _history(repo, IN_ITS_TOPIC)

    assert [(v.author, v.subject) for v in versions] == [
        ("homer", "capture: Camping"), ("marge", "Update tent.md"),
        ("archivist-bot", f"rename: {FILED_AT} → {IN_ITS_TOPIC}")]
