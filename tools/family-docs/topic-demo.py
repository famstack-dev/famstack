#!/usr/bin/env python3
"""Populate a topic room on a running famstack to eyeball topic-page generation.

The document demo (`ingest.py`) exercises the documents room; this exercises a
*topic channel* — the messy, collaborative kind a family actually uses to plan
something together. It posts a Simpsons-universe trip topic as Homer and Marge:
saved links, typed notes, and todo-shaped messages, deliberately mixing what
works well with what we *wish* worked, so a rebuild of the topic page shows
both:

  - bookmarks vs. notes split, and who filed each (the attribution work)
  - About as a recency-weighted overview that folds in the latest developments
  - the gaps: todos have no home yet (a checklist files as a plain note), short
    todo-shaped lines and chatter get dropped, buried action items in prose are
    not extracted

Nothing here writes to the vault directly — every line goes through Matrix as
the family member who'd have sent it, so the archivist captures it exactly the
way a real topic room does.

    # one-shot: create the room, join the bots + family, then post (slowly)
    python tools/family-docs/topic-demo.py --create

    # preview without touching the rig
    python tools/family-docs/topic-demo.py --dry-run

    # post into an existing room, faster
    python tools/family-docs/topic-demo.py --room topic-itchy-scratchy-land --delay 4

After it runs, rebuild the wiki and read `family/itchy-scratchy-land/about.md`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

STACK = Path(__file__).resolve().parents[2] / "stack"

ROOM_ALIAS = "topic-itchy-scratchy-land"
ROOM_NAME = "Topic: Itchy & Scratchy Land"
ROOM_TOPIC = "Planning the family trip to Itchy & Scratchy Land"

# (sender, label, text). `label` is an operator hint for the log only — the
# archivist classifies for real. We expect "chatter" and the short todos to be
# ignored, and the checkbox list to land as a note (no task list yet).
MESSAGES: list[tuple[str, str, str]] = [
    ("homer", "bookmark + framing",
     "ok THIS is the place we're going, the park itself "
     "https://en.wikipedia.org/wiki/Itchy_%26_Scratchy_Land"),
    ("homer", "messy note (buried todos)",
     "ok plan so far: leave friday after my shift, the drive is like 6 hours, "
     "stop at the cheese place near the gorge. bart NEEDS the parasol ride or "
     "he whines the whole time, lisa wants the museum thing. do not forget "
     "snacks or i am pulling over"),
    ("homer", "short todo (expect dropped)",
     "TODO: book the hotel before the prices jump"),
    ("homer", "chatter (expect ignored)",
     "cant wait woohoo"),
    ("marge", "checklist note (todo we wish worked)",
     "Packing list for the trip:\n"
     "- [ ] sunscreen (Bart burns)\n"
     "- [ ] Maggie's stroller\n"
     "- [ ] snacks and water\n"
     "- [ ] first aid kit\n"
     "- [ ] Lisa's allergy meds"),
    ("marge", "bookmark + framing",
     "found a big list of all the Itchy & Scratchy attractions and episodes "
     "to look through before we go "
     "https://en.wikipedia.org/wiki/Itchy_%26_Scratchy"),
    ("marge", "short decision note (borderline drop)",
     "Homer I booked the lodge for the 14th to the 16th, two rooms. "
     "confirmation came by email."),
    ("marge", "loose todo (expect dropped)",
     "we still need to sort out who watches Santa's Little Helper while we are gone"),
    ("homer", "duplicate link (dedup + re-attribution)",
     "https://en.wikipedia.org/wiki/Itchy_%26_Scratchy_Land"),
]


def stack(*args: str, dry: bool) -> None:
    cmd = [str(STACK), "messages", *args]
    shown = " ".join(a if "\n" not in a else repr(a) for a in cmd)
    print(f"  $ {shown}")
    if dry:
        return
    subprocess.run(cmd, check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Populate a Simpsons topic channel.")
    ap.add_argument("--room", default=ROOM_ALIAS, help="room alias to post into")
    ap.add_argument("--create", action="store_true",
                    help="create the room and join archivist-bot + marge + homer first")
    ap.add_argument("--delay", type=float, default=8.0,
                    help="seconds between messages so the archivist keeps up")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the commands without running them")
    a = ap.parse_args()
    dry = a.dry_run

    if a.create:
        print("Creating the topic room and joining bots + family...")
        stack("room", "create", a.room, ROOM_NAME, ROOM_TOPIC, dry=dry)
        stack("join", a.room, "archivist-bot", "marge", "homer", dry=dry)
        if not dry:
            time.sleep(a.delay)

    print(f"Posting {len(MESSAGES)} messages into #{a.room} ...")
    for i, (who, label, text) in enumerate(MESSAGES, start=1):
        print(f"[{i}/{len(MESSAGES)}] {who}: {label}")
        stack("send", a.room, text, "--as", who, dry=dry)
        if not dry and i < len(MESSAGES):
            time.sleep(a.delay)

    print("\nDone. Now rebuild the wiki and read the topic page:")
    print("  family/itchy-scratchy-land/about.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
