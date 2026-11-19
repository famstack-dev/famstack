"""The command line of `stack devices`: its arguments, and what each verb
prints.

    stack devices                                  the paired devices
    stack devices add|remove|seen ...              change the list (the host API's devices_set)
    stack devices pair homer                       a pairing QR code for Homer's phone
    stack devices allow K7MP-4XQD [--as marge]     let a waiting browser in
    stack devices dismiss K7MP-4XQD                end its request
    stack devices admin-link [--page dashboard]    open the Mac's address with the admin key
    stack devices notice --kind waits ...          tell the Server room of a new device

    add_parser(sub)       # in lib/stack/cli.py, next to the other commands
    run(stck, args)       # what `stack devices ...` runs
    send_notice(stck, notice)   # also the restart's word on where to pair

Each verb's logic lives in its own module of this package: the list in
`stack.devices`, pairing in `.pairing`, allowing and dismissing in
`.joining`, the admin key in `.admin_key`, the Server room's text in
`.notice`. This module only reads the command line, calls one of them and
prints the answer: JSON with `--json`, a line for a person otherwise, and
exit 1 when the verb changed nothing. It is apart from `lib/stack/cli.py`
because the paired devices are one topic of their own.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime

from .. import devices
from ..prompt import GREEN, RED, RESET
from . import admin_key, joining, pairing
from . import notice as devices_notice

__all__ = ["add_parser", "run", "send_notice"]

# How long sending one notice to the chat may take.
_NOTICE_SEND_SECS = 20


def add_parser(sub) -> None:
    """Add `stack devices` and its options to `sub`, the CLI's subparsers.
    One parser for every verb: an option names in brackets the verbs that
    read it."""
    p = sub.add_parser("devices")
    p.add_argument("verb", nargs="?", default="list",
                   help="list, pair, allow, dismiss, admin-link, add, remove, seen or notice")
    p.add_argument("device", nargs="?", default="",
                   help="(remove, seen, notice --kind added) the device's id; (pair) the person's id; "
                        "(allow, dismiss) the code the waiting browser shows")
    p.add_argument("--as", dest="as_person", default="",
                   help="(allow) the person whose device it becomes; default: the family's admin")
    p.add_argument("--shared", default="",
                   help="(allow) allow it as a shared device with this name, 'Living room'")
    p.add_argument("--person", default="", help="(add) the person's id; empty for the household")
    p.add_argument("--name", default="", help="(add) the device's name, as its browser names it")
    p.add_argument("--key-hash", dest="key_hash", default="",
                   help="(add) the SHA-256 hash of the device's key, never the key")
    p.add_argument("--page", choices=("setup", "dashboard"), default="setup",
                   help="(admin-link) the page the link opens")
    p.add_argument("--kind", choices=("waits", "added"), default="waits",
                   help="(notice) a browser waits to join, or a device was added")
    p.add_argument("--browser", default="", help="(notice) the waiting browser: Chrome, Safari, ...")
    p.add_argument("--system", default="", help="(notice) its system: Mac, iPhone, Windows, ...")
    p.add_argument("--code", default="", help="(notice) the code the waiting browser shows")
    p.add_argument("--since", default="", help="(notice) when it started to wait, 21:14")
    p.add_argument("--by", default="",
                   help="(notice --kind added) the device that allowed it, or mac")
    p.add_argument("--json", action="store_true", help="Output as JSON")


def run(stck, args) -> None:
    """`stack devices <verb>` on the instance `stck`, with `args` from
    `add_parser`'s parser. Prints the answer; exits 1 when the verb
    refused or changed nothing.

        $ stack devices
          a1b2c3  Homer · iPhone · added 2026-11-12 · seen 2026-11-18
    """
    if args.verb == "pair":
        return _pair_a_phone(stck, args.device, as_json=args.json)
    if args.verb == "admin-link":
        return _admin_link(stck, args.page, as_json=args.json)
    if args.verb == "notice":
        return _notice(stck, args)
    if args.verb == "allow":
        return _print(joining.allow(stck.instance_dir, stck.config, args.device,
                                    person=args.as_person, shared=args.shared), args.json)
    if args.verb == "dismiss":
        return _print(joining.dismiss(stck.instance_dir, stck.config, args.device), args.json)
    _list_or_change(stck, args)


def send_notice(stck, notice) -> dict:
    """Send `notice` to the Server room as a chat message that mentions
    each admin, with its envelope when it has one: {"sent": True}, or
    {"sent": False, "why": ...} when the chat did not take it."""
    argv = [str(stck.root / "stack"), "messages", "send", "famstack", notice["text"],
            "--html", notice["html"], "--json"]
    for admin in notice["mentions"]:
        argv += ["--mention", admin]
    # A waiting notice carries its code in an envelope, which the stacker
    # bot reads when an admin replies `allow` to it.
    if notice.get("event"):
        argv += ["--event", json.dumps(notice["event"])]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=_NOTICE_SEND_SECS,
                              cwd=str(stck.root))
        answer = json.loads(done.stdout or "{}")
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return {"sent": False, "why": "The chat did not answer."}
    if isinstance(answer, dict) and answer.get("ok"):
        return {"sent": True}
    return {"sent": False, "why": (answer or {}).get("error") or "The chat did not take it."}


# ── The verbs ────────────────────────────────────────────────────────────

def _list_or_change(stck, args) -> None:
    """`stack devices [list]`, `add`, `remove` and `seen`: the list of
    paired devices, the one writer of which is `stack.devices`."""
    result = devices.command(stck.instance_dir, args.verb,
                             {"person": args.person, "name": args.name, "key_hash": args.key_hash,
                              "id": args.device}, datetime.now().astimezone())
    if args.json or "error" in result:
        return _print(result, args.json)
    if "devices" in result:
        if not result["devices"]:
            print("\n  No phone or laptop is added yet.\n")
        for d in result["devices"]:
            print(f"  {d['id']}  {d['person'] or 'Shared'} · {d['name']} · "
                  f"added {d['paired']} · seen {d['seen']}")
    else:
        print(f"\n  {GREEN}✓{RESET}  Done.")


def _pair_a_phone(stck, person: str, *, as_json: bool) -> None:
    """`stack devices pair <person>`: the QR code and its address, then
    the wait for the phone; with `--json` the address and when the code
    ends, and no wait. Exits 1 when no phone was added."""
    if not person:
        return _print({"error": "Name the person: stack devices pair <person id>"}, False)
    if not as_json:
        if code := pairing.pair(stck.instance_dir, stck.config, person):
            sys.exit(code)
        return
    answer = pairing.new_code(stck.instance_dir, stck.config, person)
    shown = answer if "error" in answer else {k: answer[k] for k in ("person", "link", "expires")}
    _print(shown, True)


def _admin_link(stck, page: str, *, as_json: bool) -> None:
    """`stack devices admin-link`: the link that opens `page` on the Mac's
    address with the admin key, the key on its own line under it, and the
    ssh tunnel that reaches the address from another computer; with
    `--json` the same as one object. Makes the key on its first run."""
    answer = admin_key.link(stck.instance_dir, stck.config, f"/{page}")
    if as_json:
        return _print(answer, True)
    print(f"\n  Open this link in a browser on this Mac:\n  {answer['link']}\n")
    print(f"  Or paste the admin key where the page asks for it:\n  {answer['key']}\n")
    print(f"  Over ssh, forward the address first, then open the link on your computer:\n"
          f"  {answer['tunnel']}\n")


def _notice(stck, args) -> None:
    """`stack devices notice`: tell the Server room that a browser waits
    to join (`--kind waits`) or that a device was added (`--kind added`),
    mentioning each admin, so their phones ring. The text comes from
    fields only (stack.devices.notice). Prints `{"ok": true, "sent":
    false}` when the chat did not take it, which is no error: the chat
    may not run."""
    language = (stck.config.get("core") or {}).get("language", "")
    if args.kind == "added":
        notice = devices_notice.added(stck.instance_dir, args.device, args.by, language=language)
    else:
        notice = devices_notice.waits(
            stck.instance_dir, {"browser": args.browser, "system": args.system,
                                "code": args.code, "since": args.since},
            home=stck._home_url(), language=language)
    result = notice if "error" in notice else {"ok": True, **send_notice(stck, notice)}
    if args.json or "error" in result:
        return _print(result, args.json)
    print(f"\n  {notice['text']}\n  {'Sent to the Server room.' if result['sent'] else result['why']}")


# ── Printing ─────────────────────────────────────────────────────────────

def _print(result: dict, as_json: bool) -> None:
    """`result` as JSON, or its error on stderr, or its `line`; exit 1
    after an error, as every `stack` command does."""
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif "error" in result:
        print(f"\n  {RED}✗{RESET}  {result['error']}\n", file=sys.stderr)
    elif "line" in result:
        print(f"\n  {GREEN}✓{RESET}  {result['line']}\n")
    if "error" in result:
        sys.exit(1)
