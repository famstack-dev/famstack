"""Stacklet CLI plugins: which commands each stacklet has, and their help.

A file `stacklets/<id>/cli/<cmd>.py` is the command `stack <id> <cmd>`.
The CLI builds its parser from this, and the agent's allowlist checks
`[agent] allow` against it, so both agree on what a stacklet command is.
"""

from __future__ import annotations

import ast
from pathlib import Path


def stacklet_commands(stck) -> dict[str, dict[str, str]]:
    """`{stacklet id: {command: module path}}` for every discovered stacklet.

    Files starting with `_` are private helpers, not commands. A stacklet
    without a `cli/` directory has no entry.
    """
    commands: dict[str, dict[str, str]] = {}
    for s in stck.discover():
        cli_dir = Path(s["path"]) / "cli"
        if not cli_dir.exists():
            continue
        for py in sorted(cli_dir.glob("*.py")):
            if py.name.startswith("_"):
                continue
            commands.setdefault(s["id"], {})[py.stem] = str(py)
    return commands


def plugin_help(module_path: str) -> tuple[str | None, str | None]:
    """`(short, full)` help for a CLI plugin, read statically, no import.

    `short` is the plugin's `HELP = "..."` (the one-liner in `stack <id>`'s
    command list); `full` is its module docstring (the usage + examples that
    `stack <id> <cmd> --help` should print). Pulled with `ast` so building the
    parser stays cheap and never runs the plugins' import-time side effects.
    """
    try:
        tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None, None
    full = ast.get_docstring(tree)
    short = None
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "HELP"
                        for t in node.targets)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            short = node.value.value
            break
    return short, full
