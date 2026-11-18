"""Allowing a waiting browser from the terminal or the Server room:
`stack devices allow <code>`.

    stack devices allow K7MP-4XQD                         # as the family's admin
    stack devices allow K7MP-4XQD --as marge              # as Marge's device
    stack devices allow K7MP-4XQD --shared "Living room"  # a shared device
    stack devices allow K7MP-4XQD --json
    # {"ok": true, "person": "homer", "device": "Chrome on a Mac",
    #  "line": "Chrome on a Mac is now Homer's."}

    allow(root, config, "K7MP-4XQD", person="marge")   # the same answer as --json

A browser the home address does not know shows a code and waits until a
paired device allows it (ADR-023 D20). An admin who has no paired device
yet, only the family chat, cannot: the allow page answers a paired device
or the Mac's address, and the Mac's address listens on 127.0.0.1 alone.
The Server room's notice reaches that admin, so the room runs this
command for them, and the Mac's terminal runs it too.

Why it asks core. The waiting requests live in core's tools server, in
its memory only, and so does the rule of who may allow and for whom. So
the command is a client (ADR-019): it sends one request to the tools
server's own allow on the Mac's address (`127.0.0.1:42002`, `[core]
cards_port`), with the admin key's cookie, as the admin's browser on the
Mac sends it. The tools server then treats it as the Mac: it may allow for
anyone of the family or as a shared device, and ten wrong codes in a minute
make it wait a minute, as on the allow page. The code is read as a person
types it, with or without its dash, in any case.

Whose device it becomes: `--as <person>`, `--shared <name>`, or else the
family's first admin, the person the Mac's address stands for. The Server
room passes `--as` with the admin who wrote the command.

What it does not do: check who runs it. Only the Mac's user can read the
admin key, and the Server room's bot runs it only for an admin
(`stacklets/core/bot/stacker.py`). It never sees the browser's device key
either: the browser collects the allowance on its own next request.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ..http_json import JsonClient, Unreachable
from . import admin_key
from .notice import admins

__all__ = ["allow"]

# The tools server's allow, on the Mac's address (`devices/joining.py` in core).
_ALLOW = "/dashboard/api/devices/allow"
# The person value the tools server reads as "a shared device".
_SHARED = "shared"
# A request to the tools server answers at once; longer means it hangs.
_ASK_SECS = 10


def allow(instance_dir: Path, config: Mapping, code: str, *, person: str = "",
          shared: str = "") -> dict:
    """Allow the browser that waits with `code` on the instance at
    `instance_dir` (`config` is its stack.toml): as `person`'s device (a
    user id), as a shared device named `shared`, or else as the family's
    first admin's.

    Returns the tools server's answer with `ok`: `person` ("" for a shared
    device), `device` (its name) and `line`, the sentence that says which
    browser is now whose, in the stack's language. A refusal is `{"error":
    <one line>}` and allows nothing: no code, both `person` and `shared`, a
    code no browser waits with (also one that expired or was used), ten
    wrong codes within a minute, a person not in the family, core not
    answering.

        allow(root, config, "k7mp4xqd")
        # {"ok": True, "person": "homer", "device": "Chrome on a Mac",
        #  "line": "Chrome on a Mac is now Homer's."}
        allow(root, config, "2345-6789")
        # {"error": "This code is not waiting any more. Show a new one on the new device."}
    """
    if not code:
        return {"error": "Name the code the browser shows: stack devices allow K7MP-4XQD"}
    if person and shared:
        return {"error": "Allow it as one person's device (--as) or as a shared one (--shared), "
                         "not both."}
    body = _body(instance_dir, code, person=person, shared=shared)
    if "error" in body:
        return body
    try:
        answer = _tools_of(config).post(_ALLOW, body, headers=_headers(instance_dir, config))
    except Unreachable:
        return {"error": "Core does not answer on this Mac: start it with stack up core."}
    said = answer.body if isinstance(answer.body, dict) else {}
    if answer.status == 200 and "line" in said:
        return {"ok": True, **said}
    if answer.status == 404:
        return {"error": f"{body['as']} is not in the family. See who is with stack family."}
    return {"error": said.get("error") or f"Core answered {answer.status}: try stack up core."}


# ── The request ──────────────────────────────────────────────────────────

def _body(instance_dir: Path, code: str, *, person: str, shared: str) -> dict:
    """The allow request's JSON, as the allow page sends it: the code, and
    whom it is for. No person and no shared name means the family's first
    admin; a family with no admin is a refusal."""
    if shared:
        return {"code": code, "as": _SHARED, "name": shared}
    if person:
        return {"code": code, "as": person}
    first = next(iter(admins(instance_dir)), "")
    if not first:
        return {"error": "The family has no admin to allow it for: name the person with --as."}
    return {"code": code, "as": first}


def _headers(instance_dir: Path, config: Mapping) -> dict[str, str]:
    """The admin key's cookie, and the stack's language, so the line comes
    back in the language of the Server room's other messages."""
    language = ((config or {}).get("core") or {}).get("language") or "en"
    return {**admin_key.cookie_header(instance_dir), "Accept-Language": language}


def _tools_of(config: Mapping) -> JsonClient:
    """Core's tools server on the Mac's address of this instance."""
    return JsonClient(f"http://127.0.0.1:{admin_key.mac_port(config)}", timeout=_ASK_SECS)
