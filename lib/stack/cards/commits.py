"""The memory's commit grammar: what kind of change a commit is, and whose.

Every bot that writes to the memory repo starts its commit subject with a
prefix of one closed table (ADR-018 §6) and builds it with
`commit_subject`. Readers place a commit by its prefix (`kind_of`) and
decide from the kind, the author and the committer whether a person made
the change or the machine did (`is_machine`): the activity a page shows,
and which commits a rebuild keeps as a person's change (ADR-015, ADR-018
§7).

Pure: no I/O. Who a commit is by is decided here too (`commit_author`),
so every writer names a person the same way.
"""

from __future__ import annotations

# Every subject a bot writes to the memory starts with one of these
# prefixes (ADR-018 §6), built with `commit_subject`. The kind is the
# word a page reads. A person's commit with any other subject is a hand
# edit, in Forgejo or a clone, and reads as "changed".
SUBJECTS = {
    # Filed or grown from what somebody sent, in the sender's name.
    "capture": "filed",       # the archivist filing a chat message
    "learn": "filed",         # a document or a diary entry filed
    "update": "updated",      # a filing that grew (an email thread, a document)
    "reply": "replied",       # a reply appended to the note it answers, as a source
    "reprocess": "refiled",   # `stack memory reprocess`
    # A person's change, through a bot or a command.
    "correct": "corrected",   # a reply applied to a diary card
    "edit": "edited",         # the card editor
    "chore(memory)": "edited",  # `stack memory write`
    "chore(lists)": "edited",   # `stack memory list`
    "people": "edited",       # `stack memory person`, the people files
    "forget": "removed",
    # The machine's own work, whoever the commit names (`MACHINE_KINDS`).
    "transcribe": "transcribed",  # a new text form in a source's quote
    "declare": "declared",    # a correspondent or topic card written once
    "rename": "moved",        # a record moved to where its new class files it
    "lists": "listed",        # items put on a list from a record, or archived
    "chore(ontology)": "migrated",  # the shipped ontology synced in
    "cards": "migrated",      # the card format migrations
    "notes": "migrated",
    "seed": "seeded",         # a new memory's first content
}
MACHINE_KINDS = frozenset({"transcribed", "declared", "moved", "listed", "migrated", "seeded"})
# The framework names every bot account `<slug>-bot`; `stackadmin` is the
# install's own account. `stack memory history` reads this same rule.
_ADMIN = "stackadmin"
# What a bot commits on its own while naming a person as author, because
# the records it rewrites are theirs: by committing account, the kinds
# nobody asked for. The memory bot's `update:` is the diary compile
# writing entries again from the room. Every other commit a bot writes
# with a person as author, outside `MACHINE_KINDS`, carries that person's
# change (a filing, a reply, a card edit, a refile with their correction).
_REBUILDS = {"memory-bot": frozenset({"updated"})}


def commit_subject(prefix: str, text: str) -> str:
    """A commit subject of the memory's grammar: `<prefix>: <text>`.

    >>> commit_subject("capture", "Tent is torn")
    'capture: Tent is torn'

    A prefix outside `SUBJECTS` raises `ValueError`: a subject the history
    cannot place would read as a person's hand edit.
    """
    if prefix not in SUBJECTS:
        raise ValueError(f"not a prefix of the memory's commit grammar: {prefix!r}")
    return f"{prefix}: {text}"


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
    prefix, colon, _ = subject.partition(":")
    return SUBJECTS.get(prefix.strip(), "changed") if colon else "changed"


def is_machine(author: str, committer: str = "", kind: str = "") -> bool:
    """True for a change no person made, false for a person's.

    The author is the person a change is by or for, the committer the
    account that wrote it (ADR-015). A change is the machine's when its
    author is a bot or the admin account, when its kind is the machine's
    own work whoever it names (`MACHINE_KINDS`), or when a bot committed
    it as one of its own rebuilds (`_REBUILDS`). Before bots committed as
    themselves, the committer was the author, so an older commit reads by
    its author and kind alone.

    >>> is_machine("marge", "archivist-bot", "filed")
    False
    >>> is_machine("marge", "memory-bot", "updated")
    True
    >>> is_machine("marge", "marge", "updated")
    False
    >>> is_machine("marge", "archivist-bot", "listed")
    True
    """
    return (_is_account(author) or kind in MACHINE_KINDS
            or kind in _REBUILDS.get((committer or "").strip().lower(), ()))


def _is_account(name: str) -> bool:
    """A bot or the admin account, rather than a person."""
    name = (name or "").strip().lower()
    return name.endswith("-bot") or name == _ADMIN


# ── Who a commit is by ───────────────────────────────────────────────

# The memory's own account in Forgejo, and the address its commits carry.
MEMORY_BOT = "memory-bot"
MEMORY_BOT_EMAIL = "memory-bot@local"


class NoServerName(ValueError):
    """There is no Matrix server name to make a person's commit address from."""


def commit_author(actor: str, server_name: str = "") -> tuple[str, str]:
    """`(name, email)` of a commit to the memory repo by `actor`.

    The one rule for every path that commits to the memory repo. A person
    commits as `<person id>@<Matrix server name>`, which is how their Matrix
    id reads as an address: Marge on the server `simpson` is `marge
    <marge@simpson>`. `actor` is the person's id or their Matrix id. No real
    address goes into the repository, which is meant to outlive the stack.

    A bot (`<name>-bot`, the bot contract's ids) commits with its Forgejo
    account's address, `<name>-bot@local`, so Forgejo links its commits to
    the account and no bot reads as a person of the household.

    `server_name` is the caller's to read, from where it runs: stack.toml's
    `[messages] server_name` on the host, `MATRIX_SERVER_NAME` in a
    container. Without it a person's commit is refused (`NoServerName`):
    an address made up in its place would leave their commits under two
    addresses for good, since the history is never rewritten.

        commit_author("marge", "simpson")           # ("marge", "marge@simpson")
        commit_author("@marge:simpson", "simpson")  # ("marge", "marge@simpson")
        commit_author(MEMORY_BOT)                   # ("memory-bot", "memory-bot@local")
        commit_author("archivist-bot", "simpson")   # ("archivist-bot", "archivist-bot@local")
    """
    person = (actor or "").strip().split(":")[0].lstrip("@").lower()
    if person.endswith("-bot"):
        return person, f"{person}@local"
    if not person:
        raise ValueError("a commit to the memory is by someone, and no one was named")
    if not server_name:
        raise NoServerName(
            f"no Matrix server name to commit {person}'s change as {person}@<server name>: "
            "set `server_name` under `[messages]` in stack.toml (`stack up` gives it "
            "to the containers as MATRIX_SERVER_NAME)")
    return person, f"{person}@{server_name}"
