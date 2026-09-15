"""stack memory list-edit — change one item on a list page.

The item verb for lists. `stack memory write` hands a page over whole.
That is right for a restructure and wrong for one tick: a whole-page
write can lose state the caller never meant to touch (measured
2026-09-15 in the agent lab: rewrites drop `[x]` marks, and prompt
rules do not reliably repair them). Here the caller only names the
item. Matching, state preservation, and the commit happen at the
store, so an item change cannot damage the rest of the page.

Matching is exact first, then substring, both case-insensitive. An
ambiguous name is an answer, not a guess: the reply names the
candidates and the caller retries with more of the item's own words.

Exit codes: 0 = changed, or an honest no-op ("already ticked");
1 = instructive refusal (unknown page, no match, ambiguous match);
2 = usage error.
"""

HELP = "Change one item on a family list page"

import argparse
import re
import sys
from pathlib import Path

# Sibling-import pattern used by the other memory CLI plugins: plugins
# run on the host's stdlib-only python3, so we manipulate sys.path
# instead of relying on a package install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import update_memory  # noqa: E402

from stack.links import go_page, public  # noqa: E402
from write import _commit_message  # noqa: E402

_BOX = re.compile(r"^(\s*)- \[([ xX])\]\s*(.+?)\s*$")

_OPS = ("add", "tick", "untick", "remove", "clear-done", "reset")


def apply_list_edit(text: str, op: str, item: str,
                    section: str | None = None) -> tuple[str, str, str]:
    """Apply one item operation, or one bulk operation, to a page's text.

    Pure transform, so the behavior is testable without the store.
    Returns (new_text, sentence, kind). kind is one of:
      changed  the page text changed; sentence says what happened
      noop     nothing to do; sentence says why ("already ticked")
      refuse   the operation cannot proceed; sentence instructs the caller
    """
    lines = text.splitlines()
    boxes = [(i, m.group(2).lower() == "x", m.group(3))
             for i, ln in enumerate(lines) if (m := _BOX.match(ln))]

    # Bulk operations: the everyday list flows. "We bought everything"
    # is clear-done; a recurring list starts the week with reset. Both
    # name every affected item, so the reply stays checkable.
    if op == "clear-done":
        done = [(i, t) for i, d, t in boxes if d]
        if not done:
            return text, "nothing is ticked; the list is already clear", "noop"
        for i, _ in reversed(done):
            del lines[i]
        names = "; ".join(t for _, t in done)
        return ("\n".join(lines) + "\n",
                f"REMOVED {len(done)}: {names}", "changed")
    if op == "reset":
        done = [(i, t) for i, d, t in boxes if d]
        if not done:
            return text, "nothing is ticked; the list is already open", "noop"
        for i, _ in done:
            lines[i] = re.sub(r"- \[[xX]\]", "- [ ]", lines[i], count=1)
        names = "; ".join(t for _, t in done)
        return ("\n".join(lines) + "\n",
                f"reopened {len(done)}: {names}", "changed")

    if op == "add":
        if any(item.casefold() == t.casefold() for _, _, t in boxes):
            return text, f"'{item}' is already on the list", "noop"
        insert_at = len(lines)
        if section:
            heads = [i for i, ln in enumerate(lines)
                     if ln.lstrip().startswith("#")
                     and ln.lstrip("# ").strip().casefold() == section.casefold()]
            if heads:
                insert_at = heads[0] + 1
                while (insert_at < len(lines)
                       and not lines[insert_at].lstrip().startswith("#")):
                    insert_at += 1
                # Keep the blank line that separates sections below the item.
                while insert_at > heads[0] + 1 and not lines[insert_at - 1].strip():
                    insert_at -= 1
        lines.insert(insert_at, f"- [ ] {item}")
        return "\n".join(lines) + "\n", f"added 1: {item}", "changed"

    matches = [b for b in boxes if item.casefold() == b[2].casefold()]
    if not matches:
        matches = [b for b in boxes if item.casefold() in b[2].casefold()]
    if not matches:
        open_items = "; ".join(t for _, done, t in boxes if not done)
        return text, (f"no item matching '{item}'. "
                      f"Open items: {open_items or '(none)'}"), "refuse"
    if len(matches) > 1:
        names = "; ".join(t for _, _, t in matches)
        return text, (f"'{item}' is ambiguous, it matches: {names}. "
                      "Name the item more exactly."), "refuse"

    idx, done, name = matches[0]
    if op == "tick":
        if done:
            return text, f"'{name}' is already ticked", "noop"
        lines[idx] = lines[idx].replace("- [ ]", "- [x]", 1)
        sentence = f"ticked off 1: {name}"
    elif op == "untick":
        if not done:
            return text, f"'{name}' is already open", "noop"
        lines[idx] = re.sub(r"- \[[xX]\]", "- [ ]", lines[idx], count=1)
        sentence = f"reopened 1: {name}"
    else:  # remove
        del lines[idx]
        sentence = f"REMOVED 1: {name}"
    return "\n".join(lines) + "\n", sentence, "changed"


def run(args, stacklet, config):
    parser = argparse.ArgumentParser(prog="stack memory list-edit",
                                     add_help=False)
    parser.add_argument("page")
    parser.add_argument("--op", required=True, choices=list(_OPS))
    parser.add_argument("--item", default="")
    parser.add_argument("--section", default=None)
    parser.add_argument("--by", default="someone")
    try:
        ns = parser.parse_args(list(args or []))
    except SystemExit:
        print(parser.format_usage().strip())
        sys.exit(2)

    repo_path = ns.page.strip().removeprefix("vault/").lstrip("/")
    if not repo_path.endswith(".md"):
        print(f"{repo_path!r} is not a page (expected a .md path)")
        sys.exit(2)
    actor = ns.by.strip().split(":")[0].lstrip("@") or "someone"
    item = ns.item.strip()
    if not item and ns.op not in ("clear-done", "reset"):
        print("--item must name the item")
        sys.exit(2)

    # The transform runs against the canonical page Forgejo hands back,
    # not a local clone, so the match cannot lose a race with another
    # writer. Refusals and no-ops return the page unchanged; an
    # unchanged page does not commit.
    outcome: dict = {}

    def _apply(prior: str) -> str:
        if not (prior or "").strip():
            outcome["sentence"], outcome["kind"] = f"no such page: {repo_path}", "refuse"
            return prior or ""
        new, sentence, kind = apply_list_edit(prior, ns.op, item, ns.section)
        outcome["sentence"], outcome["kind"] = sentence, kind
        return new

    result = update_memory(
        config, repo_path, _apply, actor=actor,
        message=lambda before, after: _commit_message(
            actor, repo_path, outcome.get("sentence", "list edit")),
    )
    if "error" in result:
        return {"error": f"could not edit {repo_path}: {result['error']}"}

    sentence, kind = outcome.get("sentence", ""), outcome.get("kind", "")
    if kind == "refuse":
        print(sentence)
        sys.exit(1)
    if kind == "noop" or not result.get("committed"):
        print(sentence or "no change")
        return {"ok": True, "committed": False, "path": repo_path}

    # Same phrasing as `memory write`: the mirror lag is a delay, never
    # a doubt, or the model re-runs the edit and duplicates it. The link
    # line names the touched page in the wiki, for the reply's Sources.
    lag = "" if result.get("mirrored") else "\n  The wiki and the vault mount catch up shortly."
    home_url = (config or {}).get("home_url", "")
    url = public(go_page(repo_path), f"{home_url}/go" if home_url else "")
    link = f"\n  {url}" if url else ""
    print(f"{sentence} (by {actor}){link}{lag}")
    return {"ok": True, "committed": True, "path": repo_path,
            "by": actor, "summary": sentence}
