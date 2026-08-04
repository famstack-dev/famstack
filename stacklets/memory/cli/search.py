"""stack memory search — full-text query over the curated memory vault.

The memory vault is the local checkout the memory stacklet maintains
at `<data_dir>/memory/vault/`. It holds the *derived intelligence* layer:
doc briefings, entity notes, bookmarks, correspondent profiles, and
(soon) periodic summaries. Search is the agent-facing read surface
over that layer.

This file is a thin argparse + formatter wrapper around
`memory.lib.search_memory` — the engine lives in the lib so the
archivist bot can call it in-process without going through subprocess.
Paperless's own full-text search remains the right answer for *source*
documents; this command is for the curated layer above them.

Contract — kept stable so wrappers (CLI, MCP, Matrix bot) can build
on it without rework:

    stack memory search <query> [--nl] [--person <name>] [--tag <value>]
                        [--scope <prefix>] [--limit N]
                        [--paths | --count]
                        [--vault <path>] [--no-refresh]

Exit codes:
    0  results found
    1  no results
    2  invalid arguments (argparse usage error)
    3  backend failure — bad regex, vault unreadable

The query is a Python regex, case-insensitive, matched against the
file content (frontmatter included). `--person` and `--tag` are
repeatable, OR-combined within their axis, AND-combined across axes.
`--tag` normalizes internal whitespace so `Person:Homer` matches a
file that tags `'Person: Homer'` — writers shouldn't have to know
which spelling lives on disk.

`--nl` is the sentence path. "What do we still need to buy for the
camping trip" is not a regex — as one it asks for those exact words,
adjacent, which no file contains — so with `--nl` the question goes
to a model first, which answers with 2-4 words that would literally
appear in a matching document, and *those* become the regex. The
command prints what it searched for so a bad rewrite is visible
rather than silent:

    Searched for: Travel, Shopping, Trip

The line is suppressed under `--paths` and `--count`, which are
machine-readable output.

Three properties of `--nl` are deliberate:

  * **It degrades, never fails.** No AI configured, bot-runner down,
    model unreachable, empty keyword list: the query is searched
    literally instead. Recall is a quality-of-life feature, never a
    gate, and the exit codes below mean the same thing either way.
  * **A single bare word skips the model.** `search camping` is a
    keyword, not a question. It short-circuits before the round trip,
    so the cheap case stays cheap even when a caller passes `--nl`
    on everything.
  * **It is opt-in, and chat's trigger is not.** The archivist infers
    the same rewrite from a trailing `?` (see `docs/bot/recall.py`),
    which is right there and wrong here: on this surface the default
    query language is a regex, where `?` is a quantifier, so
    `Zelt?` would silently become a model call. Chat also pays per
    message, while this is on an agent's hot path. The asymmetry is
    the point, not an oversight.

The model runs in `stack-core-bot-runner`, reached the same way
`stack memory wiki` reaches it (`cli/_common.py`). The host stays
stdlib-only; only the keywords cross the boundary, and the vault walk,
the filters and the output all stay here.

Results are sorted by frontmatter `date` (newest first). Files
without a parseable date fall to the end.

Output (default) — one block per result, blank line between:

    YYYY-MM-DD [Persons] <relative-path>
      <Title>
      …<one-line excerpt around the match>…

`--paths` prints just file paths, newline-delimited (for xargs).
`--count` prints just the integer total. Agents read the default text
output directly — JSON would cost tokens for no readable gain.

Before walking the vault, the command compares the local `HEAD` to
the remote `HEAD` via `git ls-remote` and pulls only when they
differ. The fast path (no upstream changes) costs one round-trip;
the slow path adds a full fast-forward pull. Pass `--no-refresh` to
skip the check entirely — useful for scripting, offline use, or
when running against a `--vault` override that isn't a clone.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Sibling-import pattern used by the other memory CLI plugins
# (correspondents, lookup, check): plugins run on the host's
# stdlib-only python3, so we manipulate sys.path before importing
# from `lib` rather than relying on a package install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    keywords_to_regex,
    refresh_vault_if_stale,
    search_memory,
    vault_path_for,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dispatch_capture  # noqa: E402


HELP = "Full-text search over the curated memory vault"


# ── argparse surface ────────────────────────────────────────────────────

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stack memory search",
        description=HELP,
    )
    p.add_argument("query", help="search term (regex, case-insensitive)")
    p.add_argument(
        "--nl", action="store_true",
        help=(
            "treat the query as a question: ask the model for search "
            "keywords first, then search for those (falls back to a "
            "literal search when no model is reachable)"
        ),
    )
    p.add_argument(
        "--person", action="append", default=[], metavar="NAME",
        help="filter by person; repeatable, OR within axis",
    )
    p.add_argument(
        "--tag", action="append", default=[], metavar="VALUE",
        help=(
            "filter by tag; repeatable, OR within axis. Whitespace is "
            "normalized so 'Person:Homer' matches 'Person: Homer'."
        ),
    )
    p.add_argument(
        "--scope", action="append", default=[], metavar="PREFIX",
        help=(
            "restrict to vault path prefixes (e.g. family/, marge/); "
            "repeatable, OR within axis. Trailing slash optional."
        ),
    )
    p.add_argument(
        "--limit", type=int, default=20,
        help="maximum results to return (default 20)",
    )
    out = p.add_mutually_exclusive_group()
    out.add_argument(
        "--paths", action="store_true",
        help="print just file paths, newline-delimited",
    )
    out.add_argument(
        "--count", action="store_true",
        help="print just the result count",
    )
    p.add_argument(
        "--vault", default=None, metavar="PATH",
        help="vault path override (defaults to <data_dir>/memory/vault/)",
    )
    p.add_argument(
        "--no-refresh", action="store_true",
        help="skip the upstream-HEAD check before searching",
    )
    return p


# ── the sentence path ───────────────────────────────────────────────────

def _resolve_query(query: str) -> tuple[str, list[str]]:
    """Turn a `--nl` question into `(regex, keywords)`.

    Every failure lands on the same answer: `(query, [])`, meaning
    "search what the caller typed". An empty keyword list is how the
    caller tells the difference, and it is also what suppresses the
    `Searched for:` line, because there is nothing to disclose when no
    rewrite happened.

    The single-word short-circuit is a cost decision, not a
    correctness one. `search camping --nl` would round-trip to a
    container and wait on a model to be told that the keyword for
    "camping" is "camping". A query with no whitespace is already the
    shape the walker wants.
    """
    if not _looks_like_a_sentence(query):
        return query, []

    rc, out, reason = dispatch_capture("rewrite", query)
    if rc != 0:
        # Container down, no docker, no AI endpoint, model timed out,
        # or an entry point too old to know the command. Deliberately
        # one branch and one line: the caller asked for results, not
        # for a report on our infrastructure, and a literal search
        # still finds whatever literally matches.
        detail = f" ({reason})" if reason else ""
        print(
            f"[memory] no rewrite available{detail}, "
            "searching the query literally",
            file=sys.stderr,
        )
        return query, []

    keywords = [line.strip() for line in out.splitlines() if line.strip()]
    if not keywords:
        return query, []
    return keywords_to_regex(keywords), keywords


def _looks_like_a_sentence(query: str) -> bool:
    """Is this worth spending a model call on?

    Whitespace is the whole test. One word is a keyword and already
    works; two or more are a phrase, which as a regex means "these
    words, adjacent" and is the case that returns nothing today.
    """
    return len(query.split()) > 1


# ── output ──────────────────────────────────────────────────────────────

def _format_block(r: dict) -> str:
    """Render one result as the default human/agent block.

    `date` is shown as a 10-char placeholder when missing so the
    columns stay aligned; an empty string would shift the persons
    bracket leftwards.
    """
    persons = (
        "[" + ",".join(r["persons"]) + "]" if r["persons"] else "[]"
    )
    date = r["date"] or "----------"
    lines = [
        f"{date} {persons} {r['rel']}",
        f"  {r['title']}",
    ]
    if r["excerpt"]:
        lines.append(f"  …{r['excerpt']}…")
    return "\n".join(lines)


# ── entry point ─────────────────────────────────────────────────────────

def run(args, stacklet, config) -> dict | None:
    """Dispatcher entry — `args` is the residual list after the top-level parse.

    argparse handles usage errors itself: on bad input it prints to
    stderr and calls `sys.exit(2)`, which matches our exit-code
    contract. The function returns None on success; "no results"
    exits 1 silently rather than printing an error.
    """
    parser = _parser()
    ns = parser.parse_args(args)

    if ns.vault:
        vault = Path(ns.vault).expanduser().resolve()
    else:
        data_dir = (config or {}).get("data_dir")
        if not data_dir:
            return {
                "error": (
                    "no vault available — pass --vault or run inside a "
                    "configured stack"
                ),
            }
        vault = vault_path_for(Path(data_dir))

    if not vault.exists():
        print(f"error: vault not found at {vault}", file=sys.stderr)
        sys.exit(3)

    if not ns.no_refresh:
        status = refresh_vault_if_stale(vault)
        if status == "pulled":
            print("[memory] vault updated from Forgejo", file=sys.stderr)
        elif status == "pull_failed":
            print(
                "[memory] warning: remote has new commits but pull failed "
                "(non-fast-forward or local edits); searching stale cache",
                file=sys.stderr,
            )
        # "up_to_date" and "unreachable" are silent — the first because
        # there's nothing to say, the second because nagging on every
        # offline call would be noise.

    query, keywords = _resolve_query(ns.query) if ns.nl else (ns.query, [])

    results = search_memory(
        query, vault,
        persons=ns.person, tags=ns.tag,
        scopes=ns.scope or None,
        limit=ns.limit,
    )

    # Before the results, and before the no-results exit: a rewrite
    # that found nothing is exactly when a family needs to see which
    # words were used. Machine-readable modes stay machine-readable.
    if keywords and not (ns.paths or ns.count):
        print(f"Searched for: {', '.join(keywords)}\n")

    if not results:
        sys.exit(1)

    if ns.count:
        print(len(results))
        return None

    if ns.paths:
        for r in results:
            print(r["rel"])
        return None

    print("\n\n".join(_format_block(r) for r in results))
    return None
