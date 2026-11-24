"""Stacklet CLI plugins: which commands each stacklet has, and their help.

A file `stacklets/<id>/cli/<cmd>.py` is the command `stack <id> <cmd>`.
A stacklet with a preparation, a hook `hooks/on_prepare.py`, has the
command `stack <id> prepare` too: the framework runs it (`stack.cli`), and
its help is the hook's `HELP` and docstring. The CLI builds its parser from this, and the agent's allowlist checks
`[agent] allow` against it, so both agree on what a stacklet command is.

`plugin_words` is the one reading of the words a plugin gets: the CLI
passes them to the command, and the host API checks the agent's line
against them. A rule that read the line in another way would check one
command and let the CLI run another.
"""

from __future__ import annotations

import ast
from pathlib import Path

# The framework's own option: `stack <id> <cmd> --json` prints the command's
# answer as JSON. It is not the command's, wherever it stands in the line.
JSON_OPTION = "--json"


def plugin_words(words: list[str]) -> list[str]:
    """`words` as a stacklet command reads them: without the framework's
    `--json`, wherever it stood.

        plugin_words(["list", "--json", "delete", "partyliste"])
        # ["list", "delete", "partyliste"]
    """
    return [word for word in words if word != JSON_OPTION]


def stacklet_commands(stck) -> dict[str, dict[str, str]]:
    """`{stacklet id: {command: module path}}` for every discovered stacklet.

    Files starting with `_` are private helpers, not commands. A stacklet
    with an `on_prepare.py` hook has `prepare`, whose module is the hook.
    A stacklet with neither has no entry.
    """
    commands: dict[str, dict[str, str]] = {}
    for s in stck.discover():
        cli_dir = Path(s["path"]) / "cli"
        for py in sorted(cli_dir.glob("*.py")) if cli_dir.exists() else []:
            if py.name.startswith("_"):
                continue
            commands.setdefault(s["id"], {})[py.stem] = str(py)
        preparation = Path(s["path"]) / "hooks" / "on_prepare.py"
        if preparation.exists():
            commands.setdefault(s["id"], {})["prepare"] = str(preparation)
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
