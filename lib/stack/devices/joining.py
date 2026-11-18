"""Answering a waiting browser from the terminal or the Server room:
`stack devices allow <code>` and `stack devices dismiss <code>`.

    stack devices allow K7MP-4XQD                         # as the family's admin
    stack devices allow K7MP-4XQD --as marge              # as Marge's device
    stack devices allow K7MP-4XQD --shared "Living room"  # a shared device
    stack devices dismiss K7MP-4XQD                       # end the request, as "Not now"
    stack devices allow K7MP-4XQD --json
    # {"ok": true, "person": "homer", "device": "Chrome on a Mac",
    #  "line": "Chrome on a Mac is now Homer's."}

    allow(root, config, "K7MP-4XQD", person="marge")   # the same answer as --json
    dismiss(root, config, "K7MP-4XQD")                 # {"ok": True, "line": "..."}

A browser the home address does not know shows a code and waits until a
paired device allows it (ADR-023 D20). An admin who has no paired device
yet, only the family chat, cannot: the allow page answers a paired device
or the Mac's address, and the Mac's address listens on 127.0.0.1 alone.
The Server room's notice reaches that admin, so the room runs these
commands for them, and the Mac's terminal runs them too.

Why it asks core. The waiting requests live in core's tools server, in
its memory only, and so does the rule of who may allow and for whom. So
each command is a client (ADR-019): it sends one request to the tools
server's own allow or decline on the Mac's address (`127.0.0.1:42002`,
`[core] cards_port`), with the admin key's cookie, as the admin's browser
on the Mac sends it. The tools server then treats it as the Mac: it may
allow for anyone of the family or as a shared device, and ten wrong codes
in a minute make it wait a minute, as on the allow page. The code is read
as a person types it, with or without its dash, in any case.

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

from ..http_json import Answer, JsonClient, Unreachable
from . import admin_key
from .notice import admins

__all__ = ["allow", "dismiss"]

# The tools server's allow and decline, on the Mac's address (`devices/joining.py` in core).
_ALLOW = "/dashboard/api/devices/allow"
_DECLINE = "/dashboard/api/devices/decline"
# The person value the tools server reads as "a shared device".
_SHARED = "shared"
# A request to the tools server answers at once; longer means it hangs.
_ASK_SECS = 10
# What `dismiss` says once the request ended, in the stack's language. The
# tools server's decline answers with no sentence of its own.
_DISMISSED = {
    "en": "The request with code {code} has ended. If that browser asks again, it shows a new code.",
    "de": ("Die Anfrage mit dem Code {code} ist beendet. Fragt dieser Browser noch einmal, "
           "zeigt er einen neuen Code."),
}
_NO_CODE = "Name the code the browser shows: stack devices {verb} K7MP-4XQD"
_NO_CORE = "Core does not answer on this Mac: start it with stack up core."


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
        return {"error": _NO_CODE.format(verb="allow")}
    if person and shared:
        return {"error": "Allow it as one person's device (--as) or as a shared one (--shared), "
                         "not both."}
    body = _allow_body(instance_dir, code, person=person, shared=shared)
    if "error" in body:
        return body
    answer = _ask(instance_dir, config, _ALLOW, body)
    if answer is None:
        return {"error": _NO_CORE}
    if answer.status == 200 and "line" in answer.body:
        return {"ok": True, **answer.body}
    if answer.status == 404:
        return {"error": f"{body['as']} is not in the family. See who is with stack family."}
    return _refusal(answer)


def dismiss(instance_dir: Path, config: Mapping, code: str) -> dict:
    """End the request of the browser that waits with `code`, as "Not now"
    on the Mac's Devices page does: the browser is not let in, and shows a
    new code if it asks again.

    Returns `{"ok": True, "line": <what happened, in the stack's language>}`,
    or `{"error": <one line>}` when nothing ended: no code, a code no
    browser waits with, core not answering.

        dismiss(root, config, "K7MP-4XQD")
        # {"ok": True, "line": "The request with code K7MP-4XQD has ended. ..."}
    """
    if not code:
        return {"error": _NO_CODE.format(verb="dismiss")}
    answer = _ask(instance_dir, config, _DECLINE, {"code": code})
    if answer is None:
        return {"error": _NO_CORE}
    if answer.status == 200 and answer.body.get("declined"):
        words = _DISMISSED.get(_language(config), _DISMISSED["en"])
        return {"ok": True, "line": words.format(code=_shown(code))}
    return _refusal(answer)


# ── The request ──────────────────────────────────────────────────────────

def _allow_body(instance_dir: Path, code: str, *, person: str, shared: str) -> dict:
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


def _ask(instance_dir: Path, config: Mapping, path: str, body: dict) -> Answer | None:
    """POST `body` to `path` of core's tools server on the Mac's address,
    with the admin key's cookie and the stack's language, so a sentence
    comes back in the language of the Server room's other messages. None
    when core does not answer."""
    tools = JsonClient(f"http://127.0.0.1:{admin_key.mac_port(config)}", timeout=_ASK_SECS)
    headers = {**admin_key.cookie_header(instance_dir), "Accept-Language": _language(config)}
    try:
        answer = tools.post(path, body, headers=headers)
    except Unreachable:
        return None
    return answer if isinstance(answer.body, dict) else Answer(answer.status, {})


def _refusal(answer: Answer) -> dict:
    """The tools server's own refusal, in its words."""
    return {"error": answer.body.get("error") or f"Core answered {answer.status}: try stack up core."}


def _shown(code: str) -> str:
    """`code` as the browser shows it: "k7mp4xqd" -> "K7MP-4XQD"."""
    plain = "".join(c for c in code.upper() if c.isalnum())
    return f"{plain[:4]}-{plain[4:]}" if len(plain) == 8 else code


def _language(config: Mapping) -> str:
    return ((config or {}).get("core") or {}).get("language") or "en"
