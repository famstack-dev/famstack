"""Host-native reader for a scope's derived todos — used by `stack memory topic`.

`stack memory topic <name> todo` mirrors the `/go/topic/<name>/todo` link: the
CLI noun and the URL noun are the same first-class entity. It reads the
`todos.md` the curator writes straight off the vault on disk, so listing needs
neither the LLM nor the bot-runner — it works even when the model is down. The
`_`-prefix keeps this off the command list; `topic.py` routes to it.

The curator only writes topic-scope todos today (`<bucket>/<topic>/todos.md`);
personal-scope todos (`<member>/todos.md`) are deferred, so a `person` scope
just reports no list until they land.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    DEFAULT_SHARED_BUCKET,
    refresh_vault_if_stale,
    update_memory,
    vault_path_for,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot" / "cli"))
from todo_list import read_todos, set_todo_done, update_todo_doc  # noqa: E402


def _vault(config) -> Path | None:
    data_dir = config.get("data_dir") if config else None
    return vault_path_for(Path(data_dir)) if data_dir else None


def _known_scopes(vault: Path) -> list[str]:
    """Every scope slug that currently has a todos.md, deduped and sorted."""
    return sorted({p.parent.name for p in vault.glob("*/*/todos.md")})


def list_todos(scope: str, *, show_all: bool, config) -> dict:
    """Print a scope's open todos (with `--all`, the done ones too).

    Returns the framework's `{"ok": ...}` / `{"error": ...}` envelope; the
    structured `lists` payload always carries both open and done items so an
    agent reading the JSON sees the full state regardless of `show_all`.
    """
    vault = _vault(config)
    if vault is None or not vault.exists():
        return {"error": "no vault found — is the memory stacklet installed?"}

    # Pull if the remote moved, so the list reflects edits made anywhere else
    # (Forgejo web, another family member, the curator), not just this host's
    # own writes. Same cheap ls-remote check `stack memory search` runs; a
    # host-side, best-effort pull that never makes a read worse.
    refresh_vault_if_stale(vault)

    scope = (scope or "").strip()
    if not scope:
        known = _known_scopes(vault)
        hint = ("  topics with todos: " + ", ".join(known)) if known else \
            "  no topic has a todo list yet"
        return {"error": f"usage: stack memory topic <name> todo\n{hint}"}

    # A topic slug can occur under more than one bucket (a shared
    # `family/camping` and a personal `bart/camping`); each is its own list.
    matches = sorted(vault.glob(f"*/{scope}/todos.md"))
    if not matches:
        known = _known_scopes(vault)
        hint = ("  topics with todos: " + ", ".join(known)) if known else ""
        return {"error": f"no todo list for topic {scope!r}\n{hint}".rstrip()}

    lists = []
    for i, path in enumerate(matches):
        open_items, done_items = read_todos(path.read_text(encoding="utf-8"))
        bucket = path.parent.parent.name  # <bucket>/<scope>/todos.md
        if i:
            print()
        print(f"{bucket}/{scope} — {len(open_items)} open, {len(done_items)} done")
        for item in open_items:
            print(f"- [ ] {item}")
        if show_all:
            for item in done_items:
                print(f"- [x] {item}")
        lists.append({
            "bucket": bucket, "scope": scope,
            "open": open_items, "done": done_items,
        })

    return {"ok": True, "lists": lists}


def strike_todo(scope: str, item: str, *, done: bool, actor: str, config) -> dict:
    """Strike (`done=True`) or unstrike (`done=False`) a scope's todo.

    Resolves the scope's `todos.md`, then hands the edit + commit to
    `update_memory`, which writes canonically to Forgejo attributed to `actor`
    and refreshes the local clone. The toggle itself lives in `set_todo_done`
    (`todo_list.py`), so the read and write sides share one notion of a task
    line. Prints a human line (what the agent relays) and returns the envelope.
    """
    vault = _vault(config)
    if vault is None or not vault.exists():
        return {"error": "no vault found — is the memory stacklet installed?"}

    scope = (scope or "").strip()
    item = (item or "").strip()
    # The agent may pass a full mxid (@homer:simpson); the commit and the message
    # should read as the person's handle.
    actor = (actor or "someone").strip().split(":")[0].lstrip("@") or "someone"
    if not (scope and item):
        return {"error": 'usage: stack memory topic <name> todo strike "<item>" --by <person>'}

    matches = sorted(vault.glob(f"*/{scope}/todos.md"))
    if not matches:
        known = _known_scopes(vault)
        hint = ("  topics with todos: " + ", ".join(known)) if known else ""
        return {"error": f"no todo list for topic {scope!r}\n{hint}".rstrip()}

    # A slug can live under several buckets (family/camping, bart/camping);
    # narrow to the one that actually holds this item so we edit the right list.
    if len(matches) > 1:
        needle = item.lower()
        narrowed = [p for p in matches
                    if needle in p.read_text(encoding="utf-8").lower()]
        if len(narrowed) > 1:
            buckets = ", ".join(p.parent.parent.name for p in narrowed)
            return {"error": f"{item!r} is in several lists ({buckets}); name the bucket"}
        if not narrowed:
            return {"error": f"no todo matching {item!r} in {scope!r}"}
        matches = narrowed

    path = matches[0]
    bucket = path.parent.parent.name
    repo_path = f"{bucket}/{scope}/todos.md"

    # Capture the exact task the canonical edit matched, so we echo precisely
    # what changed even when the caller passed only a substring.
    captured: dict[str, str] = {}

    def _tx(doc: str) -> str:
        new_doc, matched = set_todo_done(doc, item, done=done)
        captured["matched"] = matched
        return new_doc

    verb = "ticked off" if done else "reopened"
    message = f'chore(todos): {actor} {verb} "{item}" in {scope}'
    result = update_memory(config, repo_path, _tx, actor=actor, message=message)
    if "error" in result:
        return result

    matched = captured.get("matched", item)
    if not result.get("committed"):
        state = "already done" if done else "already open"
        print(f'"{matched}" was {state} in {bucket}/{scope} — nothing to do')
        return {"ok": True, "committed": False, "matched": matched, "scope": scope}

    print(f'{"Struck" if done else "Reopened"}: {matched}  ({bucket}/{scope}, by {actor})')
    return {"ok": True, "committed": True, "matched": matched,
            "scope": scope, "bucket": bucket, "by": actor}


def add_todo(scope: str, item: str, *, actor: str, config) -> dict:
    """Add a new todo to a scope's list, creating the list if it is missing.

    The write counterpart to the archivist's action-item extraction: the agent
    can put an item straight onto a topic's `todos.md` when asked, reusing the
    same `update_todo_doc` merge the curator uses (a duplicate is a no-op; a
    done item is never resurrected). Commits to Forgejo as `actor`.
    """
    vault = _vault(config)
    if vault is None or not vault.exists():
        return {"error": "no vault found — is the memory stacklet installed?"}

    scope = (scope or "").strip()
    item = (item or "").strip()
    actor = (actor or "someone").strip().split(":")[0].lstrip("@") or "someone"
    if not (scope and item):
        return {"error": 'usage: stack memory topic <name> todo add "<item>" --by <person>'}

    # Add to the scope's existing list; if it has none, start one in the shared
    # bucket (auto-extend: no new-topic ceremony just to jot something down).
    matches = sorted(vault.glob(f"*/{scope}/todos.md"))
    if len(matches) > 1:
        buckets = ", ".join(p.parent.parent.name for p in matches)
        return {"error": f"{scope!r} has lists in several buckets ({buckets}); name the bucket"}
    bucket = matches[0].parent.parent.name if matches else DEFAULT_SHARED_BUCKET
    repo_path = f"{bucket}/{scope}/todos.md"
    title = scope.replace("-", " ").title()

    result = update_memory(
        config, repo_path,
        lambda doc: update_todo_doc(doc or None, title, [item]),
        actor=actor,
        message=f'chore(todos): {actor} added "{item}" to {scope}',
    )
    if "error" in result:
        return result
    if not result.get("committed"):
        print(f'"{item}" is already on the {bucket}/{scope} list; nothing added')
        return {"ok": True, "committed": False, "scope": scope}

    print(f'Added: {item}  ({bucket}/{scope}, by {actor})')
    return {"ok": True, "committed": True, "item": item,
            "scope": scope, "bucket": bucket, "by": actor}
