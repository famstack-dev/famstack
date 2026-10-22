"""The memory's commit grammar: what kind of change a commit is, and whose.

Every bot that writes to the memory repo starts its commit subject with a
prefix of one closed table (ADR-018 §6) and builds it with
`commit_subject`. Readers place a commit by its prefix (`kind_of`) and
decide from the kind, the author and the committer whether a person made
the change or the machine did (`is_machine`): the activity a page shows,
and which commits a rebuild keeps as a person's change (ADR-015, ADR-018
§7).

Pure: no I/O.
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
