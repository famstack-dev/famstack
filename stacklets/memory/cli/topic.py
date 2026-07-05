"""stack memory topic <name> todo — a topic's todos, first-class like its link.

Topics are first-class entities: the CLI noun and the `/go/topic/<name>` link
share one model (`docs`, `topic`, `person`), so `stack memory topic camping
todo` mirrors `/go/topic/camping/todo`. The todos read is host-native — straight
off the vault on disk, no LLM, no bot-runner — so it works even when the model
is down.

Examples:

    stack memory topic itchy-scratchy-land todo
      family/itchy-scratchy-land — 3 open, 1 done
      - [ ] pick up the wristbands
      - [ ] charge the camera
      - [ ] pack a change of clothes for Maggie

    stack memory topic itchy-scratchy-land todo --all   # include done items
    stack memory topic                                  # topics that have a list

Todos are also mutable, attributed to the person who acted:

    stack memory topic itchy-scratchy-land todo strike   "charge the camera" --by homer
    stack memory topic itchy-scratchy-land todo unstrike "charge the camera" --by homer

`strike`/`unstrike` toggle a task's box and commit to Forgejo (the vault's
store) as that person; the write goes through `update_memory`, the read stays
host-native. `todo` is the only resource today; the noun leaves room for more
(notes, about) without reshaping the command.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _todos  # noqa: E402

HELP = "Inspect a topic (e.g. its todos)"


def run(args, stacklet, config):
    argv = args or []
    by, argv = _extract_opt(argv, "--by")
    show_all = "--all" in argv
    positionals = [a for a in argv if not a.startswith("-")]

    # Grammar: `<name...> todo [strike|unstrike <item...>]`. The `todo` keyword
    # splits the scope name from the verb+item; bare `topic` lists the topics
    # that have a list.
    if "todo" not in positionals:
        if not positionals:
            return _todos.list_todos("", show_all=show_all, config=config)
        return {"error": 'usage: stack memory topic <name> todo [--all | strike|unstrike "<item>" --by <person>]'}

    ti = positionals.index("todo")
    scope = " ".join(positionals[:ti]).strip()
    after = positionals[ti + 1:]
    if not after:
        return _todos.list_todos(scope, show_all=show_all, config=config)

    verb, item = after[0], " ".join(after[1:]).strip()
    if verb in ("strike", "unstrike"):
        return _todos.strike_todo(scope, item, done=(verb == "strike"),
                                  actor=by or "someone", config=config)
    return {"error": f"unknown todo verb {verb!r}; use strike or unstrike"}


def _extract_opt(argv, flag):
    """Pull `--flag value` out of argv, returning (value, remaining_argv).

    Needed because `--by <person>` carries a value, so the plain "non-dash
    token is a positional" rule would otherwise treat the person as scope text.
    """
    out, value, i = [], None, 0
    while i < len(argv):
        if argv[i] == flag and i + 1 < len(argv):
            value = argv[i + 1]
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return value, out
