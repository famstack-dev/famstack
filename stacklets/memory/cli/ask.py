"""stack memory ask <question> — a question answered from the family brain.

The same shape as `stack web ask`: a plain question, a short answer,
its sources. One model call: the search uses the question's own words
(question words and fillers dropped), and the model answers from the
top hits. No tool loop deciding what to read next.

No model rewrites the question first. The `--nl` rewrite adds
category words from the family's ontology, and rare words weigh most in
the ranking, so a document that only shares those categories can beat
the page that answers. The question's own words avoid that.

    stack memory ask "Wann hat Bart sein Seepferdchen geschafft?"

      Am 12. Juni 2026 [1].

      Sources
      [1] April
          family/diary/2026/04.md

The sources print whether or not the model was reached, so the family
can read the pages themselves. A timing line on stderr says where the
time went, which is what this command is for: comparing a fixed
route with the agent's loop.

    stack memory ask "..." --json         for an agent
    stack memory ask "..." --sources 3    fewer hits for the model

Search reads the brain (see `stack memory search`). Both model calls
run in the bot-runner, because the host CLI is stdlib-only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import refresh_vault_if_stale, search_memory  # noqa: E402
from _common import dispatch_capture  # noqa: E402
from search import resolve_vault  # noqa: E402

HELP = "Ask a question and get an answer with sources"

DEFAULT_SOURCES = 5

# Words that carry the grammar of a question, not its subject. Dropped
# so they neither match every page nor crowd the excerpt. Common words
# that survive this list ("bekommen") weigh little in the ranking.
_FILLERS = frozenset("""
wann wer wen wem wessen was wie wo wohin woher wieso warum weshalb welche
welcher welches welchen welchem hat haben hatte hatten ist sind war waren
wird werden wurde wurden der die das den dem des ein eine einen einem einer
eines und oder aber mit von vom zu zum zur im in am an auf aus für bei nach
sein seine seinen seinem seiner seines ihr ihre ihren ihrem ihrer ihres mein
meine meinen unser unsere unseren es er sie wir ich du uns euch noch schon
denn doch mal bitte gibt gab kann können konnte soll sollen muss müssen
when who whom whose what how where why which did does do has have had is
are was were be been the a an and or of to in on at for from with by his
her hers their my our its it he she we i you us me get got please can could
should would will
""".split())


def question_words(question: str) -> list[str]:
    """The words of a question that say what it is about, in order, once each."""
    seen: dict[str, str] = {}
    for word in re.findall(r"\w+", question):
        key = word.lower()
        if len(word) > 1 and key not in _FILLERS and key not in seen:
            seen[key] = word
    return list(seen.values())


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stack memory ask",
        description="Answer a question from the family brain, with sources.",
    )
    p.add_argument("question", help="the question, in words")
    p.add_argument("--json", action="store_true",
                   help="print the answer and sources as JSON")
    p.add_argument("--sources", type=int, default=DEFAULT_SOURCES,
                   help=f"hits handed to the model (default {DEFAULT_SOURCES})")
    p.add_argument("--vault", default=None,
                   help="search this directory instead of the brain")
    p.add_argument("--no-refresh", action="store_true",
                   help="skip the upstream check when reading the memory vault")
    return p


def _render_sources(results: list[dict]) -> str:
    return "\n".join(
        f"  [{n}] {r['title']}\n      {r['rel']}" for n, r in enumerate(results, 1)
    )


def run(args, stacklet, config) -> dict | None:
    ns = _parser().parse_args(args)

    resolved = resolve_vault(ns.vault, config)
    if isinstance(resolved, dict):
        return resolved
    vault, searching_brain = resolved
    if not vault.exists():
        print(f"error: vault not found at {vault}", file=sys.stderr)
        sys.exit(3)
    if not ns.no_refresh and not searching_brain:
        refresh_vault_if_stale(vault)

    started = time.monotonic()
    keywords = question_words(ns.question)
    query = "|".join(re.escape(w) for w in keywords) or re.escape(ns.question)
    results = search_memory(query, vault, limit=max(ns.sources, 1))
    searched = time.monotonic()

    if not results:
        print(f'Search returned nothing for "{ns.question}".', file=sys.stderr)
        sys.exit(1)

    evidence = [
        {
            "path": r["rel"],
            "title": r["title"],
            "date": str(r.get("date") or ""),
            "excerpt": r.get("excerpt") or "",
            "summary": r.get("summary") or "",
        }
        for r in results
    ]
    payload = json.dumps({"question": ns.question, "evidence": evidence},
                         ensure_ascii=False)
    rc, out, reason = dispatch_capture("answer", timeout=180, input_text=payload)
    answered = time.monotonic()
    answer = out.strip() if rc == 0 else ""

    print(
        f"[memory] searched for: {', '.join(keywords) or ns.question}; "
        f"search {searched - started:.1f}s, answer {answered - searched:.1f}s",
        file=sys.stderr,
    )

    if not answer:
        # The search worked; only the answering failed. The pages are
        # still worth printing -- the family can read them itself.
        detail = f" ({reason})" if reason else ""
        print(f"\n  Couldn't reach the model{detail}. What the search found:\n")
        print(_render_sources(results))
        print()
        sys.exit(1)

    if ns.json:
        print(json.dumps({
            "question": ns.question,
            "answer": answer,
            "sources": [{"n": n, "title": r["title"], "path": r["rel"]}
                        for n, r in enumerate(results, 1)],
        }, indent=2, ensure_ascii=False))
        return None

    print()
    print(answer)
    print()
    print("  Sources")
    print(_render_sources(results))
    print()
    return None
