"""agent_confirm: the agent deletes a list with open items only after the person said yes.

The list store deletes a list that still has open items only with `--yes`
(`stack memory list delete <list> --yes`). The agent's `list_edit` tool
passes it after the person said yes in a later message. The model's exec
shell runs `stack` lines too, so a `--yes` on the line alone says nothing
about who said yes. The runtime therefore signs each confirmation for the
one list, and the host API runs a `--yes` only with that signature.

    the runtime, after the yes      confirm_delete("partyliste", key, now=t)
                                    "1763900060:4be1..."             (STACK_CONFIRMED)
    line from the agent's shim      confirmed 1763900060:4be1... memory list delete partyliste --yes
    take_confirmation(args)         ("1763900060:4be1...", ["memory", "list", "delete", ...])
    delete_refusal(args, c, key, now)   None, or why the line may not run

Where it fits: `list_edit` (`runtime/list_tool.py`) puts the confirmation
into the environment of its own command as `STACK_CONFIRMED`. The agent's
`stack` shim sends it after the audience as `confirmed <value>`. The host
API (`famstack-api.py`, `agent_command`) takes it off the line and asks
`delete_refusal`. The key is the agent's audience key
(`stack.agent_audience.AUDIENCE_KEY`), and the signature is the same
HMAC-SHA256, over other words, so a signed audience is never a
confirmation. Compose mounts this file into the agent's container, so both
ends sign and check with one definition.

SAFETY: the refusal reads the line as argparse reads it, after
`shlex.split`: `'--yes'`, `--ye` and `--y` are `--yes`, and a quoted
`'list'` is `list`. A pattern over the shell text could not see that. The
exec tool's environment never holds the key or a confirmation
(`room_people.audience_env` removes both), so the model's shell cannot sign
one. A confirmation holds for one list and one minute.
"""

from __future__ import annotations

import hashlib
import hmac

__all__ = ["CONFIRM_ENV", "CONFIRM_WORD", "CONFIRMED_FOR_SECONDS", "confirm_delete",
           "take_confirmation", "delete_refusal"]

# The variable the runtime puts the confirmation into for its own command.
CONFIRM_ENV = "STACK_CONFIRMED"

# The word in front of the confirmation on the shim's line.
CONFIRM_WORD = "confirmed"

# The tool signs right before its command starts, so a minute only bounds
# what a kept confirmation is worth.
CONFIRMED_FOR_SECONDS = 60

DELETE = ["memory", "list", "delete"]
YES_OPTION = "--yes"
AS_OPTION = "--as"

ASK_FIRST = ("only the list_edit tool deletes a list with open items, after the person "
             "said yes; use it and ask them")


# ── Signing a confirmation ────────────────────────────────────────────

def confirm_delete(list_: str, key: str, *, now: float) -> str:
    """A confirmation to delete `list_`, signed with `key` until
    `CONFIRMED_FOR_SECONDS` after `now`.

        confirm_delete("partyliste", key, now=1763900000)   # "1763900060:4be1..."
    """
    expires = int(now) + CONFIRMED_FOR_SECONDS
    return f"{expires}:{_mac(key, list_, expires)}"


# ── The line the API runs ─────────────────────────────────────────────

def take_confirmation(args: list[str]) -> tuple[str, list[str]]:
    """What the line names after `confirmed` (unchecked), and the command
    without it; "" and the line unchanged when there is none.

        take_confirmation(["confirmed", "1763900060:4be1", "memory", "list", "delete", "x"])
        # ("1763900060:4be1", ["memory", "list", "delete", "x"])
    """
    if len(args) > 1 and args[0] == CONFIRM_WORD:
        return args[1], args[2:]
    return "", args


def delete_refusal(args: list[str], confirmation: str, key: str, *, now: float) -> str | None:
    """Why the agent may not run `args`; None when it may.

    Refuses a list delete with `--yes`, in any spelling argparse reads as
    it, unless `confirmation` is valid now for the one list the line names.
    Every other line passes.

        delete_refusal(["memory", "list", "delete", "partyliste", "--ye"], "", key, now=t)
        # "only the list_edit tool deletes a list with open items, ..."
        delete_refusal(["memory", "list", "delete", "partyliste", "--yes"],
                       confirm_delete("partyliste", key, now=t), key, now=t)
        # None
    """
    if args[:len(DELETE)] != DELETE or not any(_is_yes(word) for word in args):
        return None
    lists = _positionals(args[len(DELETE):])
    if len(lists) == 1 and _confirms(confirmation, lists[0], key, now):
        return None
    return ASK_FIRST


# ── Reading the line as argparse does ─────────────────────────────────

def _is_yes(word: str) -> bool:
    """Whether argparse reads `word` as `--yes`: `--y`, `--ye`, `--yes`,
    with or without `=...`."""
    option = word.split("=", 1)[0]
    return len(option) > len("--") and YES_OPTION.startswith(option)


def _positionals(words: list[str]) -> list[str]:
    """The words of a delete that are not options or the value of `--as`
    (also abbreviated, `--a homer`)."""
    found, skip = [], False
    for word in words:
        if skip:
            skip = False
        elif word.startswith("-"):
            skip = len(word) > len("--") and "=" not in word and AS_OPTION.startswith(word)
        else:
            found.append(word)
    return found


def _confirms(confirmation: str, list_: str, key: str, now: float) -> bool:
    expires, _, mac = confirmation.partition(":")
    # `isdigit` alone is true for "²", which `int` refuses.
    if not key or not (expires.isascii() and expires.isdigit()) or int(expires) < now:
        return False
    # SAFETY: a constant-time compare, so the answer's timing says nothing of the mac.
    return hmac.compare_digest(mac.encode(), _mac(key, list_, int(expires)).encode())


def _mac(key: str, list_: str, expires: int) -> str:
    return hmac.new(key.encode(), f"list delete {list_}:{expires}".encode(),
                    hashlib.sha256).hexdigest()
