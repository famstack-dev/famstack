"""agent_audience: the agent reads the memory only for the people in its room.

The agent answers in a person's own chat and in rooms with several people.
A person's own notes are written for them alone (`stack.cards.audience`),
so a memory read is run for the people who will see the answer: the room's
people, given to the read as `--audience`. This module is the one home of
which reads take it, of how the runtime signs the people, and of the
refusals around it.

    the runtime, for a command      sign(["marge", "bart"], key, now=t)
                                    "bart,marge:1763900120:9f2c..."  (STACK_AUDIENCE)
    line from the agent's shim      audience bart,marge:1763900120:9f2c... memory find gift
    take_listeners(args)            ("bart,marge:1763900120:9f2c...", ["memory", "find", "gift"])
    signed_people(signed, key, now) "bart,marge"
    with_audience(args, people)     ["memory", "find", "--audience", "bart,marge", "gift"]

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

__all__ = ["AUDIENCE_READS", "AUDIENCE_KEY", "SIGNED_FOR_SECONDS", "listeners", "sign",
           "signed_people", "take_listeners", "audience_refusal", "with_audience"]

# The memory commands that take `--audience`, by their leading words, as the
# API's allowlist matches them. Each one shows cards, a person's page, a
# list, a topic's page or what changed in a folder. Every `list` verb and
# `write` are here too: a change's reply reads the list or page back (the
# items it ticked, the ones to choose from). `lookup` (the ontology) and
# `correspondents` (the shared folder) read no person's own card.
AUDIENCE_READS = [
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

# The agent stacklet's secret the people are signed with, and the variable
# the runtime reads it from.
AUDIENCE_KEY = "STACK_AUDIENCE_KEY"

# How long a signature holds. The runtime signs when a command starts and the
# shim sends it at once, so this only bounds what a kept signature is worth.
SIGNED_FOR_SECONDS = 120

# The word in front of the people on the shim's line, after its token.
LISTENERS_WORD = "audience"

AUDIENCE_OPTION = "--audience"

NOBODY_KNOWN = ("the memory is read only for the people in a room, and nobody could say "
                "who is in this room; answer without the memory")
NOT_YOURS = ("leave out --audience: who is listening is the room's to say, "
             "and the runtime adds it")


# ── Signing the room's people ─────────────────────────────────────────

def listeners(people: Iterable[str]) -> str:
    """The people as one value: lowercase, without blanks or repeats, sorted.

    The one place the list is normalised: `sign` signs this value, so what
    the API reads back from a valid signature is already in this form.

        listeners(["Marge", "", "bart", "marge"])   # "bart,marge"
    """
    return ",".join(sorted({p.strip().lower() for p in people if p.strip()}))


def sign(people: Iterable[str], key: str, *, now: float) -> str:
    """`people`, signed with `key` until `SIGNED_FOR_SECONDS` after `now`."""
    listening = listeners(people)
    expires = int(now) + SIGNED_FOR_SECONDS
    return f"{listening}:{expires}:{_mac(key, listening, expires)}"


def signed_people(signed: str, key: str, *, now: float) -> str:
    """The people `signed` names, when `key` signed it and it is not over; "" otherwise.

        signed_people(sign(["marge"], key, now=t), key, now=t)      # "marge"
        signed_people("marge", key, now=t)                         # ""
        signed_people(sign(["marge"], key, now=t), key, now=t + 600)   # ""
    """
    listening, expires, mac = (signed.split(":") + ["", ""])[:3]
    # `isdigit` alone is true for "²", which `int` refuses.
    if not key or not (expires.isascii() and expires.isdigit()) or int(expires) < now:
        return ""
    # SAFETY: a constant-time compare, so the answer's timing says nothing of the mac.
    if not hmac.compare_digest(mac.encode(), _mac(key, listening, int(expires)).encode()):
        return ""
    return listening


def _mac(key: str, listening: str, expires: int) -> str:
    return hmac.new(key.encode(), f"{listening}:{expires}".encode(), hashlib.sha256).hexdigest()


# ── The line the API runs ─────────────────────────────────────────────

def take_listeners(args: list[str]) -> tuple[str, list[str]]:
    """What the line names after `audience` (the signed people, unchecked),
    and the command without it.

    "" when the line names nobody: the shim sends no `audience` word then.

        take_listeners(["audience", "marge:1763900120:9f2c", "memory", "find", "gift"])
        # ("marge:1763900120:9f2c", ["memory", "find", "gift"])
    """
    if len(args) > 1 and args[0] == LISTENERS_WORD:
        return args[1], args[2:]
    return "", args


def audience_refusal(args: list[str], people: str) -> str | None:
    """Why the agent may not run `args` for `people`; None when it may.

    `people` is what `signed_people` read from the line's signature. Refuses
    an audience the line names itself, and a read that takes an audience
    when no one is validly signed as listening.
    """
    if any(_names_an_audience(word) for word in args):
        return NOT_YOURS
    if _takes_audience(args) and not people:
        return NOBODY_KNOWN
    return None


def with_audience(args: list[str], people: str) -> list[str]:
    """`args` with `--audience <people>` when it is a read that takes one.

    Put right after the command's own words, before anything the agent
    wrote: a `--` in its line then cannot turn the option into a word.
    """
    for read in AUDIENCE_READS:
        if args[:len(read)] == read:
            return [*read, AUDIENCE_OPTION, people, *args[len(read):]]
    return list(args)


def _names_an_audience(word: str) -> bool:
    """Whether `word` is `--audience` in any spelling argparse reads as it:
    `--audience=marge`, or an abbreviation such as `--aud`."""
    option = word.split("=", 1)[0]
    return len(option) > len("--") and AUDIENCE_OPTION.startswith(option)


def _takes_audience(args: list[str]) -> bool:
    return any(args[:len(read)] == read for read in AUDIENCE_READS)
