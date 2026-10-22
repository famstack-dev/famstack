"""A note's history, read from git: what people changed by hand, for a rebuild (ADR-018 §7).

A person's edit of a note is not stored a second time: the card's history
holds it. A rebuild (`stack memory reprocess`) reads that history once,
one `git log` of the card, and derives from it what the model and the
code apply again (`history_of`):

- the clean version the note had before its first correction or edit,
  which the card conversation starts from (`prior`);
- each edit a person made to the clean version, as a message of that
  conversation (`card_events.TextEdit`).

Whose commit counts is the one rule the dashboard and `stack memory
history` use (`activity.is_machine`): a person's change, not a bot's own
rebuild, a migration or a plain reprocess. A person's commit that carries
a source (a filing, a reply, a refile with `--msg`) is a correction: the
correction is read from the card's sources, and the card that commit
wrote is the model's, so it is no edit.

    versions = read_versions(vault, "family/camping/notes/2026/10/camping-3f2a1b.md",
                             card_id="note-3f2a1b9c0d")
    history = history_of(versions)
    history.prior     # "Camping trip next weekend:\\n\\n- Flanders lends us his tent. ..."
    history.edits     # (TextEdit('marge', '…', (('Bring 3 sleeping bags.', 'Bring 4 sleeping bags.'),)),)

Two readers give the versions, and the same `history_of` reads them:

- `read_versions`, on the host, from the local clone: one `git log
  --follow` of the card, so a move is followed, and a version that holds
  another card's id is left out;
- `versions_from_forgejo`, in the bot runner, which has no clone, from
  Forgejo's commits of the card's path and the files at each commit. It
  follows the path only: a note moved since it was filed is read from its
  move on.

Stdlib only: the host CLI and the bot runner import it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

try:  # the host CLI, which has the memory stacklet on its path
    from activity import LOG_FORMAT, is_machine, kind_of, parse_log
    from card_events import TextEdit, changes_between
    from note_card import (
        ORIGINAL, SourcesMismatch, card_sources, interpretation_of, read_sidecar, sidecar_path,
    )
except ImportError:  # the bot runner, which reads the memory stacklet as `memory`
    from memory.activity import LOG_FORMAT, is_machine, kind_of, parse_log
    from memory.card_events import TextEdit, changes_between
    from memory.note_card import (
        ORIGINAL, SourcesMismatch, card_sources, interpretation_of, read_sidecar, sidecar_path,
    )

from stack.frontmatter import FrontmatterError, parse

# The kinds of commit (`activity.kind_of`) a person's name is on because
# the pipeline wrote them from a source of theirs: what the card says
# after one is the model's, never the person's own edit.
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


@dataclass(frozen=True)
class CardHistory:
    """What a rebuild applies again of a note's history."""

    prior: str = ""
    edits: tuple[TextEdit, ...] = ()


# ── Deriving (pure) ───────────────────────────────────────────────────


def history_of(versions: Sequence[Version]) -> CardHistory:
    """The edits and the starting point a rebuild reads from `versions`, oldest first.

    `prior` is the clean version of the last version before the first
    correction or text edit; with neither, the clean version the card has
    now. "" when that version shows no clean version (a summary, or the
    words alone).
    """
    edits: list[TextEdit] = []
    first = None
    for n in range(1, len(versions)):
        before, after = versions[n - 1], versions[n]
        if _adds_a_correction(before, after):
            first = n if first is None else first
            continue
        if not after.by_a_person or after.kind in _FROM_SOURCES:
            continue
        changes = changes_between(interpretation_of(before.card), interpretation_of(after.card))
        if changes:
            edits.append(TextEdit(after.author, after.at, changes))
            first = n if first is None else first
    start = versions[first - 1] if first else (versions[-1] if versions else None)
    return CardHistory(prior=_clean(start.card) if start else "", edits=tuple(edits))


def _adds_a_correction(before: Version, after: Version) -> bool:
    """Whether `after` holds a source other than the original that `before` did not."""
    return bool(_correction_ids(after) - _correction_ids(before))


def _correction_ids(version: Version) -> set[str]:
    try:
        sources = card_sources(version.card, read_sidecar(version.sidecar))
    except SourcesMismatch:
        return set()
    return {s.id for s in sources if s.role != ORIGINAL}


def _clean(card: str) -> str:
    """The clean version a card shows; "" for a summary callout or nothing."""
    text = interpretation_of(card)
    return "" if text.startswith(">") else text


def _fields(card: str) -> dict:
    try:
        return parse(card)
    except FrontmatterError:
        return {}


# ── Reading (git) ─────────────────────────────────────────────────────


def read_versions(vault: Path, path: str, *, card_id: str) -> list[Version]:
    """Every version of the card at `path`, oldest first, across moves.

    One `git log` of the card, then one `git cat-file` for the card and
    its sidecar at each commit.
    """
    out = _git(vault, "log", "--follow", "--name-only", f"--format={LOG_FORMAT}", "--", path)
    commits = list(reversed(parse_log(out)))
    paths = [c.files[0] if c.files else path for c in commits]
    blobs = _blobs(vault, [spec for c, p in zip(commits, paths)
                           for spec in (f"{c.sha}:{p}", f"{c.sha}:{sidecar_path(p)}")])
    versions = []
    for n, c in enumerate(commits):
        card, side = blobs[2 * n], blobs[2 * n + 1]
        if card and str(_fields(card).get("card_id") or card_id) == card_id:
            versions.append(Version(c.sha, c.at, c.who, c.committer, c.subject, card, side,
                                    tuple(c.trailers.items())))
    return versions


def versions_from_forgejo(commits: Sequence[dict], read: Callable[[str, str], str], *,
                          path: str, card_id: str) -> list[Version]:
    """Every version of the card at `path`, oldest first, from Forgejo.

    ``commits`` is what Forgejo's commits API lists for the path, newest
    first; ``read(sha, path)`` the text of a file at a commit, "" when it
    has none there.
    """
    versions = []
    for c in reversed(commits):
        sha, commit = str(c.get("sha") or ""), c.get("commit") or {}
        message = str(commit.get("message") or "")
        card = read(sha, path)
        if not card or str(_fields(card).get("card_id") or card_id) != card_id:
            continue
        author, committer = commit.get("author") or {}, commit.get("committer") or {}
        versions.append(Version(sha, str(author.get("date") or ""), str(author.get("name") or ""),
                                str(committer.get("name") or ""), message.split("\n", 1)[0],
                                card, read(sha, sidecar_path(path)), _trailers_of(message)))
    return versions


def _trailers_of(message: str) -> tuple[tuple[str, str], ...]:
    """The `Key: value` lines of a commit message's last paragraph."""
    last = message.strip().split("\n\n")[-1] if "\n\n" in message.strip() else ""
    pairs = (line.partition(":") for line in last.splitlines())
    return tuple((k.strip(), v.strip()) for k, colon, v in pairs if colon and " " not in k.strip())


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True,
                          timeout=60).stdout


def _blobs(vault: Path, specs: list[str]) -> list[str]:
    """The text of each `<commit>:<path>`, "" for one that does not exist."""
    if not specs:
        return []
    out = subprocess.run(["git", "-C", str(vault), "cat-file", "--batch"],
                         input="\n".join(specs).encode() + b"\n", capture_output=True,
                         timeout=60).stdout
    texts, at = [], 0
    for _ in specs:
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
