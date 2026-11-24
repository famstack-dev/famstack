"""agent_audience: the agent reads the memory only for the people in its room.

The agent answers in a person's own chat and in rooms with several people.
A person's own notes are written for them alone (`stack.cards.audience`),
so a memory read is run for the people who will see the answer: the room's
people, the audience, given to the read as `--audience`. This module is the
one home of how each memory and docs command the agent may run reads, of how
the runtime signs the audience, and of the refusals around it.

    the runtime, for a command        sign(["marge", "bart"], key, now=t)
                                      "bart,marge:1763900120:9f2c..."  (STACK_AUDIENCE)
    line from the agent's shim        audience bart,marge:1763900120:9f2c... memory find gift
    take_signed_audience(args)        ("bart,marge:1763900120:9f2c...", ["memory", "find", "gift"])
    signed_audience(signed, key, now) "bart,marge"
    with_audience(args, audience)     ["memory", "find", "--audience", "bart,marge", "gift"]

The table. Every `memory` and `docs` command the agent runs is in one of two
lists, and a command in neither is refused, whatever allows it (the API's
`DOMAIN_ALLOW`, the admin's `[agent] allow`):

    AUDIENCE_COMMANDS   read for the audience       memory find, card, list, write, ...
    NO_PRIVATE_READS    read no person's own card   memory lookup, correspondents, capture
    neither             refused                     memory ask, docs show, ...

`memory ask` answers from the whole memory and `docs show` from Paperless,
which has one account for the stack: both would read every person's own
notes, in any room. A document's text is in its card (`memory card <id>`),
which reads for the audience.

Where it fits: the agent's runtime knows the room's members. For each
command of a turn, a tool's or exec's, it signs them with the agent's
audience key and puts the result on the command (`STACK_AUDIENCE`,
`room_people.audience_env`). The agent's `stack` shim sends it after its
token as `audience <signed>`. The host API (`famstack-api.py`,
`agent_command`) takes it off the line, checks it with the same key, asks
this module, and runs the command with `--audience`. The runtime imports
this file too (compose mounts it into the agent's container), so both ends
sign and check with one definition. The admin's own `./stack` on the Mac
never passes through the API, so it keeps the admin's view, which reads
every card.

SAFETY: no audience is the admin's view. So a read that takes an audience
and has no validly signed one is refused, never run without one: outside a
turn, in a room whose members nobody could tell, with a value the shell set
itself, a signature with other people in it, or one that is over. The key is
a secret of the agent stacklet. The runtime holds it, and the exec tool's
environment never does (nanobot builds that from an allowlist), so the
model's shell cannot sign people of its own choice. An `--audience` in the
agent's own line is refused too.

The agent's exec shell runs only plain `stack` lines (`allow_patterns` in
the agent's config), so it cannot read the key from the runtime's
environment, keep a command's signature, or run this file to sign.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable

__all__ = ["AUDIENCE_COMMANDS", "NO_PRIVATE_READS", "AUDIENCE_KEY", "SIGNED_FOR_SECONDS",
           "audience_of", "sign", "signed_audience", "take_signed_audience", "audience_takes",
           "outside_the_table", "audience_refusal", "with_audience"]

# The memory commands that take `--audience`, by their leading words, as the
# API's allowlist matches them. Each one shows cards, a person's page, a
# list, a topic's page or what changed in a folder. Every `list` verb and
# `write` are here too: a change's reply reads the list or page back (the
# items it ticked, the ones to choose from).
AUDIENCE_COMMANDS = [
    ["memory", "search"],
    ["memory", "find"],
    ["memory", "card"],
    ["memory", "links"],
    ["memory", "person", "show"],
    ["memory", "history"],
    ["memory", "list"],
    ["memory", "topic"],
    ["memory", "write"],
]

# The memory commands that read no person's own card, so they run without
# an audience: `lookup` reads the ontology, `correspondents` the shared
# folder, and `capture` files a note through the archivist and reads nothing
# back.
NO_PRIVATE_READS = [
    ["memory", "lookup"],
    ["memory", "correspondents"],
    ["memory", "capture"],
]

# The stacklets whose commands read the family's memory or documents. Each
# of their commands the agent runs is in one of the two lists above.
MEMORY_STACKLETS = {"memory", "docs"}

# The agent stacklet's secret the people are signed with, and the variable
# the runtime reads it from.
AUDIENCE_KEY = "STACK_AUDIENCE_KEY"

# How long a signature holds. The runtime signs when a command starts and the
# shim sends it at once, so this only bounds what a kept signature is worth.
SIGNED_FOR_SECONDS = 120

# The word in front of the signed audience on the shim's line, after its token.
AUDIENCE_WORD = "audience"

AUDIENCE_OPTION = "--audience"

NOBODY_KNOWN = ("the memory is read only for the people in a room, and nobody could say "
                "who is in this room; answer without the memory")
NOT_YOURS = ("leave out --audience: who is listening is the room's to say, "
             "and the runtime adds it")
NOT_IN_THE_TABLE = ("'{command}' reads the family's memory without the people in the room "
                    "in mind, so the agent does not run it in any room")


# ── Signing the room's people ─────────────────────────────────────────

def audience_of(people: Iterable[str]) -> str:
    """The people as one audience value: lowercase, without blanks or repeats, sorted.

    The one place the value is normalised: `sign` signs this value, so what
    the API reads back from a valid signature is already in this form.

        audience_of(["Marge", "", "bart", "marge"])   # "bart,marge"
    """
    return ",".join(sorted({p.strip().lower() for p in people if p.strip()}))


def sign(people: Iterable[str], key: str, *, now: float) -> str:
    """`people`, signed with `key` until `SIGNED_FOR_SECONDS` after `now`."""
    audience = audience_of(people)
    expires = int(now) + SIGNED_FOR_SECONDS
    return f"{audience}:{expires}:{_mac(key, audience, expires)}"


def signed_audience(signed: str, key: str, *, now: float) -> str:
    """The audience `signed` names, when `key` signed it and it is not over; "" otherwise.

        signed_audience(sign(["marge"], key, now=t), key, now=t)      # "marge"
        signed_audience("marge", key, now=t)                         # ""
        signed_audience(sign(["marge"], key, now=t), key, now=t + 600)   # ""
    """
    audience, expires, mac = (signed.split(":") + ["", ""])[:3]
    # `isdigit` alone is true for "²", which `int` refuses.
    if not key or not (expires.isascii() and expires.isdigit()) or int(expires) < now:
        return ""
    # SAFETY: a constant-time compare, so the answer's timing says nothing of the mac.
    if not hmac.compare_digest(mac.encode(), _mac(key, audience, int(expires)).encode()):
        return ""
    return audience



# ── The line the API runs ─────────────────────────────────────────────

def take_signed_audience(args: list[str]) -> tuple[str, list[str]]:
    """What the line names after `audience` (the signed audience, unchecked),
    and the command without it.

    "" when the line names nobody: the shim sends no `audience` word then.

        take_signed_audience(["audience", "marge:1763900120:9f2c", "memory", "find", "gift"])
        # ("marge:1763900120:9f2c", ["memory", "find", "gift"])
    """
    if len(args) > 1 and args[0] == AUDIENCE_WORD:
        return args[1], args[2:]
    return "", args


def audience_takes(args: list[str]) -> bool:
    """Whether `args` is a command that reads for an audience (`AUDIENCE_COMMANDS`).

        audience_takes(["memory", "find", "gift"])        # True
        audience_takes(["memory", "lookup", "school"])    # False
    """
    return any(args[:len(command)] == command for command in AUDIENCE_COMMANDS)


def outside_the_table(args: list[str]) -> bool:
    """Whether `args` is a memory or docs command that neither list names.

    The API refuses such a command in every room, and the agent's generated
    command list (`stack.agent_access`) leaves it out.

        outside_the_table(["memory", "ask", "gift"])     # True
        outside_the_table(["memory", "find", "gift"])    # False
        outside_the_table(["kettle", "status"])          # False

    SAFETY: such a command would run in the admin's view, which reads every
    person's own notes, so it is refused rather than run without an audience.
    """
    if not args or args[0] not in MEMORY_STACKLETS:
        return False
    return not audience_takes(args) and not any(
        args[:len(command)] == command for command in NO_PRIVATE_READS)


def audience_refusal(args: list[str], audience: str) -> str | None:
    """Why the agent may not run `args` for `audience`; None when it may.

    `audience` is what `signed_audience` read from the line's signature.
    Refuses an audience the line names itself, a memory or docs command the
    table does not name, and a read that takes an audience when no one is
    validly signed as listening.

        audience_refusal(["memory", "find", "gift"], "bart")    # None
        audience_refusal(["memory", "find", "gift"], "")        # NOBODY_KNOWN
        audience_refusal(["memory", "ask", "gift"], "bart")     # "'memory ask' reads ..."
        audience_refusal(["ai", "say", "Essen!"], "")           # None
    """
    if any(_names_an_audience(word) for word in args):
        return NOT_YOURS
    if outside_the_table(args):
        return NOT_IN_THE_TABLE.format(command=" ".join(args[:2]))
    if audience_takes(args) and not audience:
        return NOBODY_KNOWN
    return None


def with_audience(args: list[str], audience: str) -> list[str]:
    """`args` with `--audience <audience>` when it is a read that takes one.

    Put right after the command's own words, before anything the agent
    wrote: a `--` in its line then cannot turn the option into a word.

        with_audience(["memory", "find", "gift"], "bart")   # ["memory", "find", "--audience", "bart", "gift"]
        with_audience(["ai", "say", "Essen!"], "bart")      # ["ai", "say", "Essen!"]
    """
    for command in AUDIENCE_COMMANDS:
        if args[:len(command)] == command:
            return [*command, AUDIENCE_OPTION, audience, *args[len(command):]]
    return list(args)


def _names_an_audience(word: str) -> bool:
    """Whether `word` is `--audience` in any spelling argparse reads as it:
    `--audience=marge`, or an abbreviation such as `--aud`."""
    option = word.split("=", 1)[0]
    return len(option) > len("--") and AUDIENCE_OPTION.startswith(option)


def _mac(key: str, audience: str, expires: int) -> str:
    return hmac.new(key.encode(), f"{audience}:{expires}".encode(), hashlib.sha256).hexdigest()
