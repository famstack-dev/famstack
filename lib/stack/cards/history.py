"""A note's history, read from git: what people changed by hand, for a rebuild (ADR-018 §7).

A person's edit of a note is not stored a second time: the card's history
holds it. A rebuild (`stack memory reprocess`) reads that history once,
one `git log` of the card, and derives from it what the model and the
code apply again (`history_of`):

- the clean version the note had before its first correction or edit,
  which the card conversation starts from (`prior`);
- each edit a person made to the clean version, as a message of that
  conversation (`stack.cards.events.TextEdit`);
- each edit a person made to the fields, as an operation applied after
  the model (`stack.cards.fields.FieldOp`);
- each edit a person made to a quote under `## Sources`, which is their
  correction (ADR-018 §4): the quote is put back to the words as sent
  (`quotes_as_sent`), and what they changed it to is appended as a `note`
  source of theirs (`quote_edits`).

Whose commit counts is the one rule the dashboard and `stack memory
history` use (`stack.cards.commits.is_machine`): a person's change, not
a bot's own rebuild, a migration or a plain reprocess. A person's commit
that carries a source (a filing, a reply, a refile with `--msg`) is a
correction: the correction is read from the card's sources, and the card
that commit wrote is the model's, so it is no edit.

    versions = store.history("family/camping/notes/2026/10/camping-3f2a1b.md",
                             card_id="note-3f2a1b9c0d")     # the curator's card store
    history = history_of(versions, read_sources=card_sources)
    history.prior     # "Camping trip next weekend:\\n\\n- Flanders lends us his tent. ..."
    history.edits     # (TextEdit('marge', '…', (('Bring 3 sleeping bags.', 'Bring 4 sleeping bags.'),)),)
    history.ops       # (FieldOp('marge', '…', 'add', 'tags', 'camping'),)
    history.quotes_as_sent  # (('$camping:simpson', 'bring 3 sleeping bags, we leave at 4'),)
    history.quote_edits     # (Source('note', 'marge', '…', 'cards', 'text', 'cards:…', …),)

Which sources a version holds is read by the caller's reader: the
memory passes `note_card.card_sources`, which reads a note filed in the
format before ADR-018 as well as one with a sidecar.

A card's versions are read one way (`card_versions`), by the memory's
curator from its clone (`CardStore.history`), which every other reader
asks through the curator's API (`Version.to_json`, `Version.from_json`).
`parse_log` and `parse_blobs` read git's output for it.

The history follows the card across moves by its `card_id`
(ADR-018 §7), not by how alike the file is before and after: a move is
one commit that removes the card at its old path and adds it at the new
one, and the removed file is the card's past only when it held the same
`card_id` just before.

Stdlib only: the host CLI and the bot runner import it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..frontmatter import FrontmatterError, parse
from .commits import is_machine, kind_of
from .events import TextEdit, changes_between
from .fields import FieldOp, ops_between
from .sources import (
    MACHINE_FIELDS,
    NOTE,
    ORIGINAL,
    UNKNOWN,
    Source,
    SourcesMismatch,
    interpretation_of,
    new_id,
    read_sidecar,
    sidecar_path,
)

# How the sources a version holds are read: its card's text and its
# sidecar in, the sources out; `SourcesMismatch` when the two do not pair up.
SourcesReader = Callable[[str, dict], list[Source]]

# The kinds of commit (`stack.cards.commits.kind_of`) a person's name is
# on because the pipeline wrote them from a source of theirs: what the
# card says after one is the model's, never the person's own edit.
_FROM_SOURCES = frozenset({"filed", "replied", "refiled", "updated"})


@dataclass(frozen=True)
class Version:
    """The card and its sidecar as one commit left them."""

    sha: str
    at: str
    author: str
    committer: str
    subject: str
    card: str
    sidecar: str = ""
    trailers: tuple[tuple[str, str], ...] = ()

    @property
    def by_a_person(self) -> bool:
        return not is_machine(self.author, self.committer, self.kind)

    @property
    def kind(self) -> str:
        return kind_of(self.subject, dict(self.trailers))

    @property
    def by_hand(self) -> bool:
        """A person's own change of the card, not one the pipeline wrote
        from a source of theirs."""
        return self.by_a_person and self.kind not in _FROM_SOURCES

    def to_json(self) -> dict:
        """The version as JSON, the way the curator's API sends a history."""
        return {"sha": self.sha, "at": self.at, "author": self.author,
                "committer": self.committer, "subject": self.subject, "card": self.card,
                "sidecar": self.sidecar, "trailers": [list(pair) for pair in self.trailers]}

    @classmethod
    def from_json(cls, data: dict) -> Version:
        """A version `to_json` wrote."""
        return cls(str(data["sha"]), str(data["at"]), str(data["author"]),
                   str(data["committer"]), str(data["subject"]), str(data["card"]),
                   str(data.get("sidecar") or ""),
                   tuple((str(k), str(v)) for k, v in data.get("trailers") or ()))


@dataclass(frozen=True)
class CardHistory:
    """What a rebuild applies again of a note's history.

    `quotes_as_sent` pairs a source id with the quote it held before a
    person first edited it by hand; `quote_edits` are those edits, each a
    `note` source by the person (`stack.cards.sources.with_quote_edits`
    applies both).
    """

    prior: str = ""
    edits: tuple[TextEdit, ...] = ()
    ops: tuple[FieldOp, ...] = ()
    quotes_as_sent: tuple[tuple[str, str], ...] = ()
    quote_edits: tuple[Source, ...] = ()


# ── Deriving (pure) ───────────────────────────────────────────────────


def history_of(versions: Sequence[Version], *, read_sources: SourcesReader) -> CardHistory:
    """The edits and the starting point a rebuild reads from `versions`, oldest first.

    `read_sources` reads the sources a version holds (see `SourcesReader`).

    `prior` is the clean version of the last version before the first
    correction, text edit or quote edit; with none, the clean version the
    card has now. "" when that version shows no clean version (a summary,
    or the words alone).

    A quote holds the words as sent, so a person's edit of one is their
    correction: the quote is put back to what it held before their first
    edit, and what they changed it to becomes a `note` source of theirs,
    timed by their commit. A quote the machine writes again later (a
    transcribe step, a refile that put it back) holds the words as sent
    from then on, and nothing older is put back over it.
    """
    edits: list[TextEdit] = []
    ops: list[FieldOp] = []
    as_sent: dict[str, str] = {}
    notes: list[Source] = []
    first = None
    for n in range(1, len(versions)):
        before, after = versions[n - 1], versions[n]
        quotes = _edited_quotes(before, after, read_sources)
        if not after.by_hand:
            for source_id in quotes:
                as_sent.pop(source_id, None)
        elif quotes:
            for old, new in quotes.values():
                as_sent.setdefault(old.id, old.quote)
                notes.append(Source(NOTE, after.author, after.at, "cards", "text",
                                    new_id("cards"), quote=new.words))
            first = n if first is None else first
        if _adds_a_correction(before, after, read_sources):
            first = n if first is None else first
            continue
        if not after.by_hand:
            continue
        changes = changes_between(interpretation_of(before.card), interpretation_of(after.card))
        if changes:
            edits.append(TextEdit(after.author, after.at, changes))
            first = n if first is None else first
        ops += ops_between(_fields(before.card), _fields(after.card), person=after.author,
                           at=after.at, skip=MACHINE_FIELDS)
    start = versions[first - 1] if first else (versions[-1] if versions else None)
    return CardHistory(prior=_clean(start.card) if start else "", edits=tuple(edits),
                       ops=tuple(ops), quotes_as_sent=tuple(as_sent.items()),
                       quote_edits=tuple(notes))


def _adds_a_correction(before: Version, after: Version, read_sources: SourcesReader) -> bool:
    """Whether `after` holds a source other than the original that `before` did not."""
    return bool(_correction_ids(after, read_sources) - _correction_ids(before, read_sources))


def _correction_ids(version: Version, read_sources: SourcesReader) -> set[str]:
    try:
        sources = read_sources(version.card, read_sidecar(version.sidecar))
    except SourcesMismatch:
        return set()
    return {s.id for s in sources if s.role != ORIGINAL}


def _edited_quotes(before: Version, after: Version,
                   read_sources: SourcesReader) -> dict[str, tuple[Source, Source]]:
    """The sources both versions hold whose words differ, by id, as
    (before, after), in the order `after` holds them."""
    then, now = _sources_by_id(before, read_sources), _sources_by_id(after, read_sources)
    return {i: (then[i], s) for i, s in now.items() if i in then and then[i].words != s.words}


def _sources_by_id(version: Version, read_sources: SourcesReader) -> dict[str, Source]:
    """A version's sources by id; none when its quotes and sidecar do not
    pair up. An id nobody recorded (`unknown`) names no one source, so a
    source that has it is left out."""
    try:
        sources = read_sources(version.card, read_sidecar(version.sidecar))
    except SourcesMismatch:
        return {}
    return {s.id: s for s in sources if s.id != UNKNOWN}


def _clean(card: str) -> str:
    """The clean version a card shows; "" for a summary callout or nothing."""
    text = interpretation_of(card)
    return "" if text.startswith(">") else text


def _fields(card: str) -> dict:
    try:
        return parse(card)
    except FrontmatterError:
        return {}


# ── Reading: one card across its paths ────────────────────────────────


@dataclass(frozen=True)
class Commit:
    """One commit that touched a card's path, as a reader lists it.

    `parents` and `removed` are filled where the commit is read on its own
    (`CommitAt`): `removed` holds every file the commit removed, with
    renames off, so a move is a removal and an addition in one commit. A
    listing of a path's commits may leave both empty.
    """

    sha: str
    at: str
    author: str
    committer: str
    message: str
    parents: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


# The commits that touched a path, newest first, from a commit back ("":
# the head).
CommitsOf = Callable[[str, str], Sequence[Commit]]
# The text of each `(commit, path)`, "" for a file a commit does not have.
FilesAt = Callable[[Sequence[tuple[str, str]]], list[str]]
# One commit read on its own, with its parents and the files it removed;
# None when there is no such commit.
CommitAt = Callable[[str], "Commit | None"]


def card_versions(path: str, *, card_id: str, commits_of: CommitsOf, read: FilesAt,
                  commit_at: CommitAt) -> list[Version]:
    """Every version of card `card_id`, now at `path`, oldest first, across its moves.

    One path at a time, newest first: the card's versions at a path run
    back to the commit that put the card there, and end at a commit that
    left no card there or another card. That commit is the card's filing,
    which never moves a card, or else it is read on its own: when it
    removed a card file that held `card_id` just before (`_moved_from`),
    the card was moved, and its history goes on at that file from the
    commit's parent back. A note that never moved is read in one round,
    with nothing read on its own.

    A version without a `card_id` counts as this card: it was filed before
    cards had ids. A move is followed only to a file that has the id.
    """
    versions: list[Version] = []
    since = ""
    while path:
        listed = list(commits_of(path, since))
        texts = read([(c.sha, file) for c in listed for file in (path, sidecar_path(path))])
        run = []
        for n, commit in enumerate(listed):
            card = texts[2 * n]
            if not card or str(_fields(card).get("card_id") or card_id) != card_id:
                break
            run.append(_version(commit, card, texts[2 * n + 1]))
        if not run:
            break
        versions[:0] = reversed(run)
        if run[-1].kind == "filed" or not card_id:
            break
        path, since = _moved_from(commit_at(listed[len(run) - 1].sha), path,
                                  card_id=card_id, read=read)
    return versions


def _moved_from(commit: Commit | None, path: str, *, card_id: str,
                read: FilesAt) -> tuple[str, str]:
    """The card file `commit` moved the card at `path` from, and the
    commit before it; ("", "") when the card did not move there.

    Each card file the commit removed is read as it was just before, the
    one with the card's own file name first: a move into a topic folder
    keeps it, so a move commit that moved many cards costs one read for
    this one. Only a file that held `card_id` is this card's past; another
    card that left in the same commit is not.
    """
    parent = commit.parents[0] if commit and commit.parents else ""
    if not (commit and parent):
        return "", ""
    name = PurePosixPath(path).name
    removed = sorted((f for f in commit.removed if f.endswith(".md")),
                     key=lambda f: PurePosixPath(f).name != name)
    for old in removed:
        [card] = read([(parent, old)])
        if card and str(_fields(card).get("card_id") or "") == card_id:
            return old, parent
    return "", ""


def _version(commit: Commit, card: str, sidecar: str) -> Version:
    return Version(commit.sha, commit.at, commit.author, commit.committer,
                   commit.message.split("\n", 1)[0], card, sidecar, _trailers_of(commit.message))


def _trailers_of(message: str) -> tuple[tuple[str, str], ...]:
    """The `Key: value` lines of a commit message's last paragraph."""
    last = message.strip().split("\n\n")[-1] if "\n\n" in message.strip() else ""
    pairs = (line.partition(":") for line in last.splitlines())
    return tuple((k.strip(), v.strip()) for k, colon, v in pairs if colon and " " not in k.strip())


# ── Reading git's output (pure) ───────────────────────────────────────
#
# What a reader of a clone asks git for, and how its answer is read. The
# card store runs the commands in the curator (`memory.cards`).

# One commit per record: its fields apart, its message, then the files it
# changed when they are asked for, one `<status>\t<path>` line each.
_RECORD, _FIELD, _MESSAGE_END = "\x1e", "\x1f", "\x1d"
LOG_FORMAT = f"{_RECORD}%H{_FIELD}%P{_FIELD}%aI{_FIELD}%an{_FIELD}%cn{_FIELD}%B{_MESSAGE_END}"


def parse_log(out: str) -> list[Commit]:
    """The commits of `git log --format=LOG_FORMAT [--name-status]` output,
    newest first, each with the files it removed when the statuses are in it."""
    commits = []
    for record in out.split(_RECORD)[1:]:
        head, _, files = record.partition(_MESSAGE_END)
        sha, parents, at, author, committer, message = head.split(_FIELD, 5)
        removed = tuple(line[2:] for line in files.splitlines() if line.startswith("D\t"))
        commits.append(Commit(sha, at, author, committer, message, tuple(parents.split()), removed))
    return commits


def parse_blobs(out: bytes, count: int) -> list[str]:
    """The text of each of `count` objects `git cat-file --batch` printed,
    "" for one that is missing or not a file."""
    texts, at = [], 0
    for _ in range(count):
        end = out.index(b"\n", at)
        header = out[at:end].split()
        at = end + 1
        if len(header) != 3 or header[1] != b"blob":
            texts.append("")
            continue
        size = int(header[2])
        texts.append(out[at:at + size].decode("utf-8", errors="replace"))
        at += size + 1
    return texts
