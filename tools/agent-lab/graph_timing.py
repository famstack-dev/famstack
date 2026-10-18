#!/usr/bin/env python3
"""How long the card graph takes on a family memory of a given size.

`stack memory find`, `card` and `links` build the graph from the files and
the git log on every call (no index). This builds a made-up family memory
with one commit per card, as filings arrive, and times one call of each.

    python3 tools/agent-lab/graph_timing.py            # 500 2000 5000 10000 cards
    python3 tools/agent-lab/graph_timing.py 20000
"""

from __future__ import annotations

import random
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO / "stacklets" / "memory"))

from graph import load_graph  # noqa: E402

PEOPLE = ["homer", "marge", "bart", "lisa", "maggie"]
TOPICS = ["camping", "schule", "auto", "garten", "urlaub", "gesundheit", "haus", "finanzen"]
WORDS = ("Zelt Schule Termin Auto Werkstatt Garten Rasen Urlaub Arzt Rechnung Versicherung "
         "Saxophon Fussball Geburtstag Kuchen Einkauf Heizung Dach Fenster Ferien").split()


def _cards(n: int, rng: random.Random):
    """(path, text) for n cards, oldest first: diary, notes in topics, documents."""
    start = date.today() - timedelta(days=5 * 365)
    for i in range(n):
        day = (start + timedelta(days=i * 5 * 365 // n)).isoformat()
        who, topic = rng.choice(PEOPLE), rng.choice(TOPICS)
        body = " ".join(rng.choice(WORDS) for _ in range(rng.randint(40, 200)))
        kind = i % 3
        if kind == 0:
            yield (f"family/diary/{day[:4]}/{day}-{i}.md",
                   f"---\ntype: diary\ntitle: Tag {day}\ndate: \"{day}\"\nentry_id: \"d{i:09d}\"\n"
                   f"persons:\n  - {who}\n---\n{body}\n")
        elif kind == 1:
            link = f"\nSiehe [{topic}](/family/{topic}/about.md)." if i % 4 else ""
            yield (f"family/{topic}/note-{i}.md",
                   f"---\ntype: note\ntitle: {rng.choice(WORDS)} {i}\ndate: \"{day}\"\n"
                   f"capture_id: \"$c{i:012d}\"\n---\n{body}{link}\n")
        else:
            yield (f"{who}/documents/doc-{i}.md",
                   f"---\ntype: document\ntitle: {rng.choice(WORDS)}-Brief {i}\ndate: \"{day}\"\n"
                   f"paperless_id: \"{i}\"\n---\n{body}\n")


def build(root: Path, n: int) -> None:
    """A git repository with one commit per card, written with fast-import."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    rng = random.Random(n)
    stream, mark = [], 0
    for topic in TOPICS:
        stream.append(("family/" + topic + "/about.md",
                       f"---\ntype: topic\ntitle: {topic.title()}\nslug: {topic}\n---\n"))
    stream += list(_cards(n, rng))
    lines = []
    for when, (path, text) in enumerate(stream):
        data = text.encode()
        mark += 1
        lines += [b"commit refs/heads/main",
                  f"committer {rng.choice(PEOPLE)} <x@famstack> {1600000000 + when * 3600} +0000".encode(),
                  f"data {len(path) + 7}".encode(), b"file: " + path.encode(),
                  f"M 100644 inline {path}".encode(), f"data {len(data)}".encode(), data, b""]
    subprocess.run(["git", "-C", str(root), "fast-import", "--quiet"], input=b"\n".join(lines),
                   check=True)
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "main"], check=True)


def timed(fn):
    t = time.perf_counter()
    result = fn()
    return result, (time.perf_counter() - t) * 1000


def main(sizes: list[int]) -> None:
    print(f"{'cards':>6}  {'load ms':>8}  {'find ms':>8}  {'links ms':>8}  {'with a version':>15}")
    for n in sizes:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "vault"
            build(root, n)
            graph, load_ms = timed(lambda: load_graph(root, history=root))
            _, find_ms = timed(lambda: graph.find("zelt werkstatt"))
            _, links_ms = timed(lambda: graph.neighbours("topic/camping", depth=2))
            versioned = sum(1 for c in graph.cards.values() if c.version)
            print(f"{n:>6}  {load_ms:>8.0f}  {find_ms:>8.0f}  {links_ms:>8.0f}  "
                  f"{versioned:>7} of {len(graph.cards)}")


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or [500, 2000, 5000, 10000])
