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
from lib import vault_path_for  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot" / "cli"))
from todo_list import read_todos  # noqa: E402


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
