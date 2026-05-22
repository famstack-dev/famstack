"""stack memory search — full-text query over the curated memory vault.

The memory vault is the local checkout the memory stacklet maintains
at `<data_dir>/memory/vault/`. It holds the *derived intelligence* layer:
doc briefings, entity notes, bookmarks, correspondent profiles, and
(soon) periodic summaries. Search is the agent-facing read surface
over that layer.

This is the minimum shape — pure-Python regex over markdown body,
filter by frontmatter `persons` in-process, output a token-light
block per result. No system deps to install; an `rg` accelerator can
slot in later behind the same surface. Paperless's own full-text
search remains the right answer for *source* documents; this command
is for the curated layer above them.

Contract — kept stable so wrappers (CLI, MCP, Matrix bot) can build
on it without rework:

    stack memory search <query> [--person <name>] [--tag <value>]
                        [--limit N]
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
import re
import sys
from pathlib import Path

# Pull `vault_path_for` from the memory stacklet's lib. The CLI runs
# on stdlib-only python3, so we keep the path-mangling pattern the
# sibling commands (correspondents, lookup, check) already use.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import refresh_vault_if_stale, vault_path_for  # noqa: E402


HELP = "Full-text search over the curated memory vault"


# ── argparse surface ────────────────────────────────────────────────────

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stack memory search",
        description=HELP,
    )
    p.add_argument("query", help="search term (regex, case-insensitive)")
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


# ── frontmatter (stdlib-only) ───────────────────────────────────────────

def _parse_frontmatter(text: str) -> dict:
    """Parse the YAML frontmatter block at the head of `text`.

    Only the shapes the archivist emits in practice are supported:
    top-level scalars (`key: value`) and one-deep lists of scalars
    (`key:` followed by `  - item` lines). Anything fancier is ignored
    rather than crashed on — a malformed file should still surface in
    grep results, just without enriched metadata.
    """
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}

    data: dict[str, object] = {}
    current_list: list[str] | None = None
    for raw in text[4:end].splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("  - ") or line.startswith("- "):
            if current_list is not None:
                token = line.split("- ", 1)[1].strip().strip("'\"")
                current_list.append(token)
            continue
        if line.startswith(" ") or line.startswith("\t"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value == "":
            current_list = []
            data[key] = current_list
        else:
            data[key] = value.strip("'\"")
            current_list = None
    return data


def _persons(fm: dict) -> list[str]:
    v = fm.get("persons")
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        return [v]
    return []


def _tags(fm: dict) -> list[str]:
    v = fm.get("tags")
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        return [v]
    return []


def _norm_tag(value: str) -> str:
    """Collapse all whitespace and lower-case for tag comparison.

    'Person: Homer', 'Person:Homer', and 'PERSON :HOMER' all
    normalize to 'person:homer'. Writers and queriers can disagree
    on spacing and casing — the filter shouldn't care.
    """
    return re.sub(r"\s+", "", value).lower()


# ── engine ──────────────────────────────────────────────────────────────

def _matching_files(query: str, vault: Path) -> list[Path]:
    """Return markdown files whose content matches `query` (regex, ci).

    Pure-Python walk: `rglob('*.md')` over the vault, compile the
    query once, then `pattern.search(text)`. No system deps; fast
    enough for household-scale vaults (well under a second for a
    few thousand files on a Mac Mini). An `rg` accelerator can slot
    in behind the same signature later.
    """
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error as e:
        print(f"error: invalid regex: {e}", file=sys.stderr)
        sys.exit(3)

    matches: list[Path] = []
    for path in vault.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if pattern.search(text):
            matches.append(path)
    return sorted(matches)


def _excerpt(text: str, query: str, max_len: int = 200) -> str:
    """First non-empty body line that mentions `query` (case-insensitive).

    "Body" starts after the *closing* `---` of the frontmatter block,
    not the opening one — otherwise a hit on `title:` or any other
    frontmatter line gets surfaced as the excerpt, which is noisy and
    misleading. Headings and prose alike count once we're past the
    frontmatter; the agent only needs the line the human would glance
    at to decide if the hit is relevant.
    """
    needle = query.lower()
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            body = text[end + len("\n---\n"):]
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if needle in stripped.lower():
            if len(stripped) > max_len:
                stripped = stripped[:max_len] + "…"
            return stripped
    return ""


# ── core ────────────────────────────────────────────────────────────────

def _search(
    query: str, vault: Path,
    persons_filter: list[str], tags_filter: list[str],
) -> list[dict]:
    """Walk + filter the vault, return sorted result dicts.

    Filters: a doc passes when, for every supplied axis, at least one
    of its values matches at least one of the requested values. A doc
    that has no value on an axis fails that axis. Persons match
    case-insensitively on exact string; tags match on whitespace-and-
    case-normalized form (see `_norm_tag`).
    """
    results: list[dict] = []
    want_persons = {p.lower() for p in persons_filter}
    want_tags = {_norm_tag(t) for t in tags_filter}

    for path in _matching_files(query, vault):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fm = _parse_frontmatter(text)

        doc_persons = _persons(fm)
        if persons_filter:
            if not any(p.lower() in want_persons for p in doc_persons):
                continue

        doc_tags = _tags(fm)
        if tags_filter:
            doc_norm = {_norm_tag(t) for t in doc_tags}
            if not (doc_norm & want_tags):
                continue

        try:
            rel = str(path.relative_to(vault))
        except ValueError:
            rel = str(path)

        results.append({
            "path": path,
            "rel": rel,
            "title": fm.get("title") or path.stem,
            "date": fm.get("date") or "",
            "persons": doc_persons,
            "tags": doc_tags,
            "excerpt": _excerpt(text, query),
        })

    # Newest first by frontmatter `date`. Files with no date use an
    # empty string, which sorts lowest under reverse=True — so they
    # land at the bottom of the list behind every dated entry.
    results.sort(
        key=lambda r: (str(r.get("date") or ""), r["rel"]),
        reverse=True,
    )
    return results


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

    results = _search(ns.query, vault, ns.person, ns.tag)[: ns.limit]

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
