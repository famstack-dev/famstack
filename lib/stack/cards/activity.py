"""What changed in the memory, read from its git history as activity.

The memory repo's log is its activity: every filing, reply, correction,
refile and migration is one commit, authored by the person it is by or
for, and committed by the bot that wrote it (ADR-015). This module turns
that log into changes a page can show: when, who, what kind of change,
and which files. The memory's cards API (`stacklets/memory/cards_api.py`)
maps the files to cards for an audience.

The kind of each change, and whether a person or the machine made it,
come from the memory's commit grammar (`stack.cards.commits`). The words
a page shows for a kind are the page's (core's dashboard); this module
only names it.

Pure apart from the git command line it builds: no I/O here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .commits import is_machine, kind_of
from .lookup import SIDECAR_SUFFIX

# One commit per record, its fields apart, the trailers a kind reads last.
# `%aI` is the author time with its offset: the moment the change was made.
_RECORD, _FIELD, _TRAILER = "\x1e", "\x1f", "\x1d"
LOG_FORMAT = (f"{_RECORD}%h{_FIELD}%aI{_FIELD}%an{_FIELD}%cn{_FIELD}%s{_FIELD}"
              f"%(trailers:key=Migration,key=Requested-By,separator={_TRAILER})")


@dataclass(frozen=True)
class Commit:
    """One commit of the memory repo, as activity reads it."""

    sha: str
    at: str
    who: str
    committer: str
    subject: str
    trailers: dict[str, str] = field(default_factory=dict)
    files: tuple[str, ...] = ()

    @property
    def kind(self) -> str:
        return kind_of(self.subject, self.trailers)

    @property
    def machine(self) -> bool:
        return is_machine(self.who, self.committer, self.kind)


def log_command(repo: str, commits: int) -> list[str]:
    """The git command that lists the latest `commits` with their files."""
    return ["git", "-C", repo, "log", f"-{commits}", "--name-only", f"--format={LOG_FORMAT}"]


def parse_log(out: str) -> list[Commit]:
    """The commits in the output of `log_command`, newest first."""
    commits = []
    for block in out.split(_RECORD)[1:]:
        head, _, files = block.partition("\n")
        sha, at, who, committer, subject, trailers = (head.split(_FIELD) + [""] * 6)[:6]
        commits.append(Commit(sha, at, who, committer, subject, _trailers(trailers),
                              tuple(f for f in files.splitlines() if f.strip())))
    return commits


def card_files(files: tuple[str, ...]) -> list[str]:
    """The card files a commit touched, in its order, each once.

    A sidecar (`x.json` beside `x.md`, ADR-018) is part of its card, so a
    commit that wrote both touched one card. The media archive keeps its
    own sidecars and holds no card.
    """
    seen: dict[str, None] = {}
    for f in files:
        if f.startswith("media/"):
            continue
        if f.endswith(SIDECAR_SUFFIX):
            f = f.removesuffix(SIDECAR_SUFFIX) + ".md"
        if f.endswith(".md"):
            seen.setdefault(f, None)
    return list(seen)


def _trailers(text: str) -> dict[str, str]:
    found = {}
    for line in text.split(_TRAILER):
        key, colon, value = line.partition(":")
        if colon:
            found[key.strip()] = value.strip()
    return found
