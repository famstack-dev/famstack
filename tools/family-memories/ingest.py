#!/usr/bin/env python3
"""ingest.py — replay the rendered Memories corpus into a TEST RIG room.

Reads out/manifest.json (produced by generate.py) and posts each item
into a Matrix room in timeline order, reproducing the real room's
chaos: sync bursts land back-to-back, captions arrive late as replies,
diary entries get edited, voice messages carry the MSC3245 voice flag.
Writes out/ingest-log.json mapping item id -> event id so pipeline
tests can assert against ground truth.

    python tools/family-memories/ingest.py \
        --homeserver http://testrig.local:42031 \
        --room '#memories:testrig.local' \
        --login marge:PASSWORD --login homer:PASSWORD

SAFETY: this writes invented Simpsons memories into a room, so it only
runs on an instance configured as the Simpsons. Any other household is
somebody's real family, and this is the last place fabricated memories
belong. There is no default homeserver on purpose, and --force-i-know
overrides the check.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
REPO_ROOT = HERE.parents[1]

# The household this corpus is about. Everything it sends is a Simpsons
# memory, so the instance being the Simpsons is the real precondition:
# on any other household these are fabricated memories in a room meant
# for the family's own.
DEMO_HOUSEHOLD = "simpson"


def configured_household(root: Path) -> str:
    """The family name this checkout is installed for, or "" if unknown.

    Unknown fails the check. An instance with no `stack.toml` has not
    been set up, and guessing in its favour is the wrong way to be
    wrong about where invented memories get written.
    """
    try:
        with open(root / "stack.toml", "rb") as f:
            core = tomllib.load(f).get("core") or {}
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    return str(core.get("stack_owner") or "")


class Client:
    def __init__(self, homeserver: str):
        self.hs = homeserver.rstrip("/")
        self.txn = 0

    def call(self, path, token=None, method="GET", body=None,
             raw_body=None, content_type="application/json"):
        data = raw_body if raw_body is not None else (
            json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(
            self.hs + path, data=data, method=method,
            headers={"Content-Type": content_type,
                     **({"Authorization": f"Bearer {token}"} if token else {})})
        return json.load(urllib.request.urlopen(req, timeout=120))

    def login(self, user, password):
        r = self.call("/_matrix/client/v3/login", method="POST", body={
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": user},
            "password": password})
        return r["access_token"], r["user_id"]

    def resolve_room(self, room, token):
        if room.startswith("!"):
            return room
        r = self.call("/_matrix/client/v3/directory/room/"
                      + urllib.parse.quote(room), token)
        return r["room_id"]

    def join(self, room_id, token):
        self.call(f"/_matrix/client/v3/join/{urllib.parse.quote(room_id)}",
                  token, method="POST", body={})

    def upload(self, blob, mimetype, token):
        r = self.call("/_matrix/media/v3/upload?filename=upload",
                      token, method="POST", raw_body=blob,
                      content_type=mimetype)
        return r["content_uri"]

    def send(self, room_id, content, token):
        self.txn += 1
        r = self.call(
            f"/_matrix/client/v3/rooms/{urllib.parse.quote(room_id)}"
            f"/send/m.room.message/corpus{self.txn}-{int(time.time()*1000)}",
            token, method="PUT", body=content)
        return r["event_id"]


def voice_content(item, mxc):
    dur = item["duration_ms"]
    return {
        "msgtype": "m.audio", "body": item["file"], "url": mxc,
        "info": {"duration": dur, "mimetype": "audio/wav",
                 "size": (OUT / item["file"]).stat().st_size},
        "org.matrix.msc1767.audio": {
            "duration": dur,
            "waveform": [512 + (i * 37) % 400 for i in range(30)]},
        "org.matrix.msc3245.voice": {},
    }


def image_content(item, mxc):
    return {"msgtype": "m.image", "body": item["file"], "url": mxc,
            "info": {"mimetype": "image/png", "w": 1134, "h": 1512,
                     "size": (OUT / item["file"]).stat().st_size}}


def burst_ordered(manifest):
    """Send order with each burst's members contiguous.

    A sync burst is a phone coming back online and flushing its queue,
    so its members reach the server back-to-back regardless of when
    they were recorded. The spec lists items in timeline order, which
    interleaves burst members with live messages; replaying that order
    literally spaces them out and erases the pattern they exist to
    encode. Members are emitted where the burst's first one appears.
    """
    groups = {}
    for item in manifest:
        if burst := item.get("burst"):
            groups.setdefault(burst, []).append(item)

    out, sent = [], set()
    for item in manifest:
        if item["id"] in sent:
            continue
        for member in groups.get(item.get("burst"), [item]):
            out.append(member)
            sent.add(member["id"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--homeserver", required=True)
    ap.add_argument("--room", required=True,
                    help="room id (!..) or alias (#memories:server)")
    ap.add_argument("--login", action="append", required=True,
                    metavar="USER:PASSWORD")
    ap.add_argument("--locale", choices=["de", "en"], default="de")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="seconds between non-burst items")
    ap.add_argument("--force-i-know", action="store_true")
    args = ap.parse_args()
    global OUT
    OUT = OUT / args.locale

    household = configured_household(REPO_ROOT)
    if not args.force_i_know and \
            not household.lower().startswith(DEMO_HOUSEHOLD):
        sys.exit(
            f"REFUSING: this instance is set up for "
            f"{household or 'no household'}, not the Simpsons. Everything "
            "below is an invented Simpsons memory and does not belong in "
            "another family's room.")

    manifest = json.loads((OUT / "manifest.json").read_text())
    c = Client(args.homeserver)
    tokens, user_ids = {}, {}
    for spec in args.login:
        user, _, pw = spec.partition(":")
        tokens[user], user_ids[user] = c.login(user, pw)
    missing = {i["sender"] for i in manifest} - set(tokens)
    if missing:
        sys.exit(f"no --login for sender(s): {', '.join(sorted(missing))}")

    room_id = c.resolve_room(args.room, next(iter(tokens.values())))
    for t in tokens.values():
        c.join(room_id, t)

    event_ids, prev_burst = {}, None
    for item in burst_ordered(manifest):
        tok = tokens[item["sender"]]
        burst = item.get("burst")
        if "delay_after_prev" in item:
            time.sleep(min(item["delay_after_prev"], 10))
        elif not (burst and burst == prev_burst):
            time.sleep(args.delay)
        prev_burst = burst

        if item["kind"] in ("voice", "dialogue"):
            mxc = c.upload((OUT / item["file"]).read_bytes(), "audio/wav", tok)
            content = voice_content(item, mxc)
        elif item["kind"] == "image":
            mxc = c.upload((OUT / item["file"]).read_bytes(), "image/png", tok)
            content = image_content(item, mxc)
        else:
            content = {"msgtype": "m.text", "body": item["text"]}
        if item.get("reply_to"):
            content["m.relates_to"] = {
                "m.in_reply_to": {"event_id": event_ids[item["reply_to"]]}}
        eid = event_ids[item["id"]] = c.send(room_id, content, tok)
        print(f"sent {item['id']} -> {eid}")

        if item.get("edit_text"):
            time.sleep(args.delay)
            eid2 = c.send(room_id, {
                "msgtype": "m.text", "body": "* " + item["edit_text"],
                "m.new_content": {"msgtype": "m.text",
                                  "body": item["edit_text"]},
                "m.relates_to": {"rel_type": "m.replace", "event_id": eid},
            }, tok)
            event_ids[item["id"] + "#edit"] = eid2
            print(f"sent {item['id']}#edit -> {eid2}")

    (OUT / "ingest-log.json").write_text(json.dumps(
        {"room_id": room_id, "events": event_ids}, indent=2))
    print(f"\n{len(event_ids)} events -> {OUT}/ingest-log.json")


if __name__ == "__main__":
    main()
