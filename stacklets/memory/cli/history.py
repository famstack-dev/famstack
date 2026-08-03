"""stack memory history — what changed in the family's memory, and when.

The vault is a git repository, which means it already remembers every version
of everything and who wrote it. Nothing reads that back. "What's new this
week?", "who changed Homer's profile?", "when did this land on the list?" are
all answerable from history the moment somebody asks it a question.

This is that reader. It is deliberately not about lists: a list is one kind of
page, and the same questions apply to a profile, a note, a document briefing,
or the vault as a whole.

    stack memory history                      recent changes, everywhere
    stack memory history camping              ...within one topic or person
    stack memory history --by marge           ...by one person
    stack memory history --since "last week"  ...in a time window
    stack memory history --item Kuehlbox      when this first appeared, and who

WHY A COMMAND AND NOT JUST GIT
    The agent can run shell, so raw `git log` was the obvious alternative. Two
    things argue against it. The first is that the obvious incantation is
    wrong: `git blame` attributes lines by position, and a page that gets
    rewritten -- splitting one list in two, a tidy-up -- reattributes every
    line in it to whoever did the rewrite. Asked when an item was added,
    blame confidently answers "today, by marge". The pickaxe (`log -S`)
    follows the text itself and survives rewrites, and it is not the tool
    anybody reaches for first.

    The second is that every other memory capability is a `stack memory` verb
    behind the host allowlist. Shelling git at a mount path inside the
    container is a second surface with a different trust boundary and no
    discoverability, for questions this answers in one line each.

WHAT IT COSTS TO READ
    Answers are one line apiece, because the agent pays for every one in
    context. `git log -p` on a page is enormous and almost never what was
    asked.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import vault_path_for  # noqa: E402

HELP = "Show what changed in the family's memory, and when"

_USAGE = ("usage: stack memory history [<topic-or-person>] [--item <text>] "
          "[--by <person>] [--since <when>] [--limit N]\n"
          "  e.g. stack memory history camping --since \"last week\"\n"
          "       stack memory history --item Kuehlbox")

# Tab-separated so the fields survive text that contains spaces, which every
# one of these fields does.
_FORMAT = "--format=%ad%x09%an%x09%s"
_SEP = "\t"

_DEFAULT_LIMIT = 10


def run(args, stacklet, config):
    argv = list(args or [])
    item, argv = _greedy(argv, "--item")
    actor, argv = _opt(argv, "--by")
    since, argv = _opt(argv, "--since")
    limit, argv = _opt(argv, "--limit")
    scope = " ".join(a for a in argv if not a.startswith("-")).strip()

    vault = _vault(config)
    if vault is None:
        return {"error": "no data_dir configured"}
    if not (vault / ".git").exists():
        return {"error": f"{vault} is not a git repository yet — "
                         f"run `stack up memory` first"}

    paths = _paths_for(vault, scope)
    if scope and paths is None:
        return {"error": f"nothing in the vault called {scope!r}"}

    if item:
        return _when_added(vault, item, paths)
    return _recent(vault, paths, actor=actor, since=since,
                   limit=_int(limit, _DEFAULT_LIMIT), scope=scope)


# ── the two questions ────────────────────────────────────────────────────

def _recent(vault, paths, *, actor, since, limit, scope):
    """What changed lately, most recent first."""
    argv = ["log", _FORMAT, "--date=short", f"-n{limit}"]
    if actor:
        argv += [f"--author={actor}"]
    if since:
        argv += [f"--since={since}"]
    argv += _pathspec(paths)

    rows = _rows(_git(vault, *argv))
    if not rows:
        return _nothing(scope, actor, since)

    where = f" in {scope}" if scope else ""
    print(f"{len(rows)} recent change{'s' if len(rows) != 1 else ''}{where}:")
    for date, who, subject in rows:
        print(f"  {date}  {who:<8} {subject}")
    return {"ok": True, "changes": [
        {"date": d, "by": w, "what": s} for d, w, s in rows]}


def _when_added(vault, item, paths):
    """When this text first appeared in the vault, and who put it there.

    Follows the text rather than the line, on purpose. `git blame` answers by
    position, so a page that has since been rewritten reports every line as
    written by whoever rewrote it -- which on a list is a routine tidy-up,
    and makes the answer wrong exactly when it matters.

    `-G` matches commits whose diff adds or removes the text. It is a regex,
    hence the escape: family wording is full of dots and dashes.

    Only the *arrival* is reported, because only the arrival is reliable. A
    rewrite that reshuffles a page leaves an untouched item sitting in the
    diff as context, so it appears in no commit's added or removed lines --
    verified against a real rewrite, where both `-S` and `-G` report the
    original commit and nothing else. That makes "when was this last
    touched" unanswerable here, and a number that is right only when nobody
    reorganised the page is worse than no number at all.
    """
    argv = ["log", _FORMAT, "--date=short", "--reverse",
            f"-G{re.escape(item)}"]
    argv += _pathspec(paths)

    rows = _rows(_git(vault, *argv))
    if not rows:
        return {"error": f"nothing in the vault's history mentions {item!r}"}

    date, who, subject = rows[0]
    print(f'"{item}" first appeared {date}, by {who}')
    print(f"  {subject}")
    return {"ok": True, "item": item, "added": date, "by": who}


def _nothing(scope, actor, since):
    """Say which filter came up empty, so the caller can drop the right one."""
    asked = [bit for bit in (f"in {scope}" if scope else "",
                             f"by {actor}" if actor else "",
                             f"since {since}" if since else "") if bit]
    print("no changes" + (" " + " ".join(asked) if asked else "") + ".")
    return {"ok": True, "changes": []}


# ── the vault, and where in it ───────────────────────────────────────────

def _vault(config):
    data_dir = config.get("data_dir") if config else None
    return vault_path_for(Path(data_dir)) if data_dir else None


def _paths_for(vault: Path, scope: str):
    """Turn "camping" or "homer" into the paths that mean it.

    A scope is whatever the family would say out loud, so it is resolved
    against the vault rather than demanded as a path: a topic, a person, or
    a path spelled out in full all arrive here as one word.
    """
    if not scope:
        return []
    for candidate in (Path("family") / scope, Path(scope)):
        if (vault / candidate).exists():
            return [str(candidate)]
    return None


def _pathspec(paths):
    return ["--", *paths] if paths else []


# ── running git, and reading it back ─────────────────────────────────────

def _git(vault: Path, *argv) -> str:
    """Read-only git against the vault clone; a failure is simply no history."""
    try:
        done = subprocess.run(["git", "-C", str(vault), *argv],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _rows(out: str):
    """The log's tab-separated lines, minus the ones we cannot read."""
    rows = []
    for line in (out or "").splitlines():
        parts = line.split(_SEP, 2)
        if len(parts) == 3:
            rows.append(tuple(p.strip() for p in parts))
    return rows


# ── argv ─────────────────────────────────────────────────────────────────

def _opt(argv, flag):
    """Pull `--flag value` out of argv, returning (value, remaining)."""
    out, value, i = [], None, 0
    while i < len(argv):
        if argv[i] == flag and i + 1 < len(argv):
            value = argv[i + 1]
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return value, out


def _greedy(argv, flag):
    """Pull `--flag` plus every word up to the next flag.

    The agent reaches this through a socket that splits on shlex, so an item
    it did not think to quote arrives in pieces. Searching for "Kuehlbox"
    when the family wrote "Kuehlbox mitbringen" finds the wrong thing or
    nothing, and neither failure is visible to whoever asked.
    """
    out, value, i = [], None, 0
    while i < len(argv):
        if argv[i] == flag:
            words = []
            i += 1
            while i < len(argv) and not argv[i].startswith("-"):
                words.append(argv[i])
                i += 1
            value = " ".join(words)
            continue
        out.append(argv[i])
        i += 1
    return value, out


def _int(value, fallback):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return fallback
