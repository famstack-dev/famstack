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

`todo` is the only resource today; the noun leaves room for more (notes,
about) without reshaping the command.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _todos  # noqa: E402

HELP = "Inspect a topic (e.g. its todos)"


def run(args, stacklet, config):
    argv = args or []
    positionals = [a for a in argv if not a.startswith("-")]
    # `stack memory topic <name> todo` → the topic's todos; bare `topic`
    # lists the topics that have one. Anything else is a usage error.
    if positionals and positionals[-1] == "todo":
        scope = " ".join(positionals[:-1]).strip()
    elif not positionals:
        scope = ""  # discovery listing
    else:
        return {"error": "usage: stack memory topic <name> todo [--all]"}
    return _todos.list_todos(scope, show_all="--all" in argv, config=config)
