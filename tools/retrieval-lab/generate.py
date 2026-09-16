#!/usr/bin/env python3
"""Render corpus.yaml into a vault directory the search engines can index.

The output is disposable: delete `out/` and run this again. The spec is
the source of truth, which is the same arrangement `tools/family-memories`
uses, and for the same reason -- a corpus you cannot regenerate is a
corpus nobody can check.

    python3 tools/retrieval-lab/generate.py [--out DIR]

Fact pages come out as written. Noise pages are cycled from the topic
vocabularies with a fixed seed, so two runs produce byte-identical files
and a measurement taken last week still means something today.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def render(title: str, date: str, persons: list[str], tags: list[str],
           body: str) -> str:
    """One vault page, in the frontmatter shape the archivist writes."""
    lines = ["---", f"title: {title}", f"date: {date}", "type: note"]
    if persons:
        lines.append("persons:")
        lines += [f"  - {p}" for p in persons]
    if tags:
        lines.append("tags:")
        lines += [f"  - {t}" for t in tags]
    lines += ["---", "", body.rstrip(), ""]
    return "\n".join(lines)


def write_facts(spec: dict, out: Path) -> dict[str, str]:
    """Write the answer pages. Returns {fact id: vault-relative path}."""
    paths: dict[str, str] = {}
    for fact in spec["facts"]:
        rel = f"{fact['scope']}/{fact['slug']}.md"
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render(fact["title"], str(fact["date"]), fact.get("persons", []),
                   fact.get("tags", []), fact["body"]),
            encoding="utf-8")
        paths[fact["id"]] = rel
    return paths


def write_noise(spec: dict, out: Path) -> int:
    """Fill the vault with plausible wrong answers.

    Without these the corpus is thirty pages and every query is easy:
    ranking only earns its keep when there is something to rank against.
    The pages are deliberately dull and repetitive -- that is what a real
    vault of short notes looks like, and it is the condition under which
    a lexical engine either finds the one page that matters or does not.
    """
    noise = spec["noise"]
    rng = random.Random(noise["seed"])
    people = spec["persons"]
    topics = noise["topics"]
    written = 0
    for i in range(noise["count"]):
        topic = topics[i % len(topics)]
        opener = topic["openers"][rng.randrange(len(topic["openers"]))]
        details = rng.sample(topic["details"], 2)
        month = 1 + (i % 9)
        day = 1 + (i * 7) % 27
        rel = f"{topic['scope']}/notes/{i:03d}.md"
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render(opener.rstrip("."), f"2026-{month:02d}-{day:02d}",
                   [people[rng.randrange(len(people))]], topic["tags"],
                   opener + "\n\n" + "\n".join(details)),
            encoding="utf-8")
        written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(HERE / "out" / "vault"),
                        help="directory to render the vault into")
    parser.add_argument("--noise", type=int, default=None,
                        help="override the distractor count, for scale tests")
    parser.add_argument("--include", default=None, metavar="DIR",
                        help=("copy another vault's pages in first, so the "
                              "agent rig keeps its own scenario pages while "
                              "gaining a corpus big enough to rank over"))
    ns = parser.parse_args()

    spec = yaml.safe_load((HERE / "corpus.yaml").read_text(encoding="utf-8"))
    if ns.noise is not None:
        spec["noise"]["count"] = ns.noise
    out = Path(ns.out)
    if out.exists():
        for stale in sorted(out.rglob("*.md")):
            stale.unlink()
    out.mkdir(parents=True, exist_ok=True)

    included = 0
    if ns.include:
        source = Path(ns.include)
        for page in sorted(source.rglob("*.md")):
            target = out / page.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(page.read_bytes())
            included += 1

    facts = write_facts(spec, out)
    noise = write_noise(spec, out)
    (out.parent / "fact-paths.yaml").write_text(
        yaml.safe_dump(facts, allow_unicode=True, sort_keys=True),
        encoding="utf-8")

    carried = f"{included} carried + " if included else ""
    print(f"{carried}{len(facts)} fact pages + {noise} noise pages -> {out}")


if __name__ == "__main__":
    main()
