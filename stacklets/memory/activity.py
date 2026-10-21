"""What changed in the memory, read from its git history as activity.

The memory repo's log is its activity: every filing, reply, correction,
refile and migration is one commit, authored by the person it is by or
for, and committed by the bot that wrote it (ADR-015). This module turns
that log into changes a page can show: when, who, what kind of change,
and which files. The cards API (`cards_api.py`) maps the files to cards
for an audience.

The kind comes from the subject's prefix, the convention every writer of
the memory follows (`capture:`, `reply:`, `reprocess:`, `cards:`, ...),
and from the `Migration:` trailer a migration puts on its commit. The
words a page shows for a kind are the page's (core's dashboard); this
module only names it.

Pure apart from the git command line it builds: no I/O here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from stack.card_lookup import SIDECAR_SUFFIX

# One commit per record, its fields apart, the trailers a kind reads last.
# `%aI` is the author time with its offset: the moment the change was made.
_RECORD, _FIELD, _TRAILER = "\x1e", "\x1f", "\x1d"
LOG_FORMAT = (f"{_RECORD}%h{_FIELD}%aI{_FIELD}%an{_FIELD}%s{_FIELD}"
              f"%(trailers:key=Migration,key=Requested-By,separator={_TRAILER})")

# The subject prefixes the memory's writers use, by the kind of change.
# A prefix not here is "changed": still shown, never guessed at.
_KINDS = {
    "capture": "filed",       # the archivist filing a chat message, by its sender
    "learn": "filed",         # a document or a diary entry filed
    "update": "updated",      # a filing that grew (an email thread, a document)
    "reply": "replied",       # a reply folded into the note it answers
    "correct": "corrected",   # a person's correction, applied by a bot (ADR-015)
    "edit": "edited",
    "reprocess": "refiled",   # `stack memory reprocess`
    "cards": "migrated",      # the card format migrations
    "notes": "migrated",
    "seed": "seeded",         # a new memory's first content
    "forget": "removed",
    "people": "edited",       # `stack memory person`, the people files
    "lists": "edited",
}
# A conventional `chore(<scope>): <person> ...` is a person's edit through
# a command: `stack memory write`, `stack memory list`.
_CHORE = "chore("
# The framework names every bot account `<slug>-bot`; `stackadmin` is the
# install's own account (`cli/history.py`, the same rule).
_ADMIN = "stackadmin"


@dataclass(frozen=True)
class Commit:
    """One commit of the memory repo, as activity reads it."""

    sha: str
    at: str
    who: str
    subject: str
    trailers: dict[str, str] = field(default_factory=dict)
    files: tuple[str, ...] = ()

    @property
    def kind(self) -> str:
        return kind_of(self.subject, self.trailers)

    @property
    def machine(self) -> bool:
        return is_machine(self.who)


def log_command(repo: str, commits: int) -> list[str]:
    """The git command that lists the latest `commits` with their files."""
    return ["git", "-C", repo, "log", f"-{commits}", "--name-only", f"--format={LOG_FORMAT}"]


def parse_log(out: str) -> list[Commit]:
    """The commits in the output of `log_command`, newest first."""
    commits = []
    for block in out.split(_RECORD)[1:]:
        head, _, files = block.partition("\n")
        sha, at, who, subject, trailers = (head.split(_FIELD) + [""] * 5)[:5]
        commits.append(Commit(sha, at, who, subject, _trailers(trailers),
                              tuple(f for f in files.splitlines() if f.strip())))
    return commits


def kind_of(subject: str, trailers: dict[str, str]) -> str:
    """The kind of change a commit is, in one plain word.

    >>> kind_of("capture: Tent is torn", {})
    'filed'
    >>> kind_of("lists: move each topic's todo list to lists/", {"Migration": "memory/0001"})
    'migrated'
    >>> kind_of("chore(memory): marge changed Camping", {})
    'edited'
    """
    if trailers.get("Migration"):
        return "migrated"
    if subject.startswith(_CHORE):
        return "edited"
    prefix, colon, _ = subject.partition(":")
    return _KINDS.get(prefix.strip(), "changed") if colon else "changed"


def is_machine(author: str) -> bool:
    """True for a bot or the admin account, rather than a person."""
    name = (author or "").strip().lower()
    return name.endswith("-bot") or name == _ADMIN


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
