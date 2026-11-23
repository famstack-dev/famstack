"""Core's tools server gets every value of core's env its code reads.

The tools server's compose service lists the keys of core's `.env` it is
given (`environment:` in `stacklets/core/docker-compose.yml`), instead of
the whole file, so a change to a value only the bot runner reads does not
recreate it (`stack.core_refresh`). A key its code reads but the list
lacks would be empty in the container, silently. This test reads the code
the way a reviewer would: every `os.environ[...]`, `os.environ.get(...)`
and `os.getenv(...)` in the tools server's files and in the `stack`
modules they import, with a module's string constant resolved as the key.
It compares the keys core's env has with that list.

Core's env is rendered by the real `Stack.env("core")` over a fabricated
instance, so a key added to core's manifest counts at once.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

from stack.stack import Stack

REPO = Path(__file__).resolve().parents[3]
TOOLS_SERVER = REPO / "stacklets" / "core" / "tools-server"
LIB = REPO / "lib"


# ── Reading the code ─────────────────────────────────────────────────────

def _string_constants(tree: ast.Module) -> dict[str, str]:
    """The module's `NAME = "text"` assignments, for keys named by a constant."""
    return {target.id: node.value.value
            for node in tree.body if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            for target in node.targets if isinstance(target, ast.Name)}


def _env_reads(tree: ast.Module) -> set[str]:
    """The environment keys a module reads by name."""
    constants = _string_constants(tree)

    def key(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return constants.get(node.id) if isinstance(node, ast.Name) else None

    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and \
                ast.unparse(node.func).endswith(("environ.get", "getenv")):
            keys.add(key(node.args[0]))
        if isinstance(node, ast.Subscript) and ast.unparse(node.value).endswith("environ"):
            keys.add(key(node.slice))
    return keys - {None}


def _stack_imports(tree: ast.Module) -> set[str]:
    """The `stack` modules a module imports, with each imported name as a
    possible submodule (`from stack.devices import admin_key`)."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and \
                (node.module or "").split(".")[0] == "stack":
            found |= {node.module} | {f"{node.module}.{a.name}" for a in node.names}
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.split(".")[0] == "stack"}
    return found


def _module_file(name: str) -> Path | None:
    path = LIB.joinpath(*name.split("."))
    return next((p for p in (path.with_suffix(".py"), path / "__init__.py") if p.exists()), None)


def keys_the_tools_server_reads() -> set[str]:
    """Every key the tools server's code reads, its `stack` imports included."""
    keys, queue, seen = set(), [], set()
    for path in TOOLS_SERVER.rglob("*.py"):
        tree = ast.parse(path.read_text())
        keys |= _env_reads(tree)
        queue += _stack_imports(tree)
    while queue:
        name = queue.pop()
        if name in seen or not (path := _module_file(name)):
            continue
        seen.add(name)
        tree = ast.parse(path.read_text())
        keys |= _env_reads(tree)
        queue += _stack_imports(tree)
    return keys


# ── What compose gives it ────────────────────────────────────────────────

def tools_service() -> dict:
    compose = yaml.safe_load((REPO / "stacklets" / "core" / "docker-compose.yml").read_text())
    return compose["services"]["stack-core-tools"]


def listed_keys() -> set[str]:
    block = tools_service().get("environment") or {}
    return set(block) if isinstance(block, dict) else {item.partition("=")[0] for item in block}


def core_env_keys(tmp_path: Path) -> set[str]:
    """The keys of core's rendered `.env`, from the real manifest."""
    (tmp_path / ".stack").mkdir()
    (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')
    (tmp_path / "stack.toml").write_text(f'[core]\ndata_dir = "{tmp_path / "data"}"\n')
    return set(Stack(REPO, tmp_path / "data", instance_dir=tmp_path).env("core"))


# ── The tests ────────────────────────────────────────────────────────────

def test_the_tools_server_is_given_every_key_of_core_env_its_code_reads(tmp_path):
    """Given the tools server's code and core's rendered env,
    when a key of core's env is read by that code,
    then the tools server's compose service lists it.

    An unlisted key is empty in the container, and the page that needs it
    fails without saying why."""
    missing = (keys_the_tools_server_reads() & core_env_keys(tmp_path)) - listed_keys()

    assert missing == set()


def test_the_tools_server_takes_no_whole_env_file():
    """Given core's compose file, the tools server has no `env_file`:
    with the whole file, any change to core's env recreates it."""
    assert "env_file" not in tools_service()


def test_the_tools_server_lists_only_keys_core_env_has(tmp_path):
    """Given core's rendered env, every key the tools server's list names
    is in it: a listed key core's env lacks is always empty."""
    assert listed_keys() - core_env_keys(tmp_path) == set()
