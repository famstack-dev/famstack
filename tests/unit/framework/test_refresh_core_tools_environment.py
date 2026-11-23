"""Core's tools server is given exactly the values of core's env its code reads.

The tools server's compose service lists the keys of core's `.env` it is
given (`environment:` in `stacklets/core/docker-compose.yml`), instead of
the whole file, so a change to a value only the bot runner reads does not
recreate it (`stack.core_refresh`). A key its code reads but the list
lacks would be empty in the container, silently. This test reads the code
the way a reviewer would: every `os.environ[...]`, `os.environ.get(...)`
and `os.getenv(...)` in the tools server's files and in the `stack`
modules they import, absolutely or relatively, with every package's
`__init__` on the way and a module's string constant resolved as the key.
It compares the keys core's env has with that list, in both directions: a
listed key nobody reads recreates the server for nothing.

Three keys are read by modules the server imports, in code it never runs
(`NOT_USED`). A read by a name that is not a constant
(`os.environ.get(name)`) is not seen; the server has none today.

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

# Keys of core's env that a module the tools server imports reads, in code
# the server never calls. Listed, they would recreate the setup page's
# server on a change that means nothing to it.
NOT_USED = {
    # `MemoryAPI.from_container`, the bot runner's writer. The server
    # forwards the client's token and adds none (tools-server/cards_page.py).
    "CARDS_WRITE_TOKEN",
    # Read at import by `stack.ai.models` for `resolve_model`, which only
    # the bots call; `stack/__init__` imports it.
    "AI_DEFAULT_MODEL",
    "AI_MODELS_JSON",
}


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


def _stack_imports(tree: ast.Module, package: str) -> set[str]:
    """The `stack` modules a module imports, absolute or relative to its
    `package` (`""` for the tools server's own files), with each imported
    name as a possible submodule (`from stack.devices import admin_key`)
    and every package on the way, whose `__init__` runs first."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                if not package:
                    continue
                parts = package.split(".")[:len(package.split(".")) - node.level + 1]
                module = ".".join([*parts, *(node.module or "").split(".")]).strip(".")
            else:
                module = node.module or ""
            if module.split(".")[0] == "stack":
                found |= {module} | {f"{module}.{a.name}" for a in node.names}
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.split(".")[0] == "stack"}
    return {".".join(name.split(".")[:i]) for name in found
            for i in range(1, len(name.split(".")) + 1)}


def _module_file(name: str) -> tuple[Path, str] | None:
    """The file of module `name` and the package its relative imports start from."""
    path = LIB.joinpath(*name.split("."))
    if (path / "__init__.py").exists():
        return path / "__init__.py", name
    if path.with_suffix(".py").exists():
        return path.with_suffix(".py"), name.rpartition(".")[0]
    return None


def keys_read_by_module(tools_server: Path = TOOLS_SERVER) -> dict[str, set[str]]:
    """Every key read by the tools server's code and by each `stack` module
    it imports, directly or through others, by file."""
    reads, queue, seen = {}, [], set()
    for path in tools_server.rglob("*.py"):
        tree = ast.parse(path.read_text())
        reads[str(path.relative_to(REPO))] = _env_reads(tree)
        queue += _stack_imports(tree, "")
    while queue:
        name = queue.pop()
        if name in seen or not (found := _module_file(name)):
            continue
        seen.add(name)
        path, package = found
        tree = ast.parse(path.read_text())
        reads[str(path.relative_to(REPO))] = _env_reads(tree)
        queue += _stack_imports(tree, package)
    return reads


def keys_the_tools_server_reads() -> set[str]:
    return set().union(*keys_read_by_module().values())


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
    read = keys_the_tools_server_reads() & core_env_keys(tmp_path)

    assert read - NOT_USED - listed_keys() == set()


def test_the_tools_server_lists_only_keys_its_code_reads(tmp_path):
    """Given the tools server's list, every key on it is read by its code,
    and is in core's env.

    A listed key nobody reads recreates the setup page's server whenever
    it changes, as the memory's write token did on the memory's first run;
    a listed key core's env lacks is always empty."""
    read = keys_the_tools_server_reads() - NOT_USED

    assert listed_keys() - read == set()
    assert listed_keys() - core_env_keys(tmp_path) == set()


def test_every_key_kept_off_the_list_is_still_read_somewhere():
    """Given `NOT_USED`, each key on it is still read by a module the tools
    server imports: one nothing reads any more is a stale exception."""
    assert NOT_USED - keys_the_tools_server_reads() == set()


def test_the_tools_server_takes_no_whole_env_file():
    """Given core's compose file, the tools server has no `env_file`:
    with the whole file, any change to core's env recreates it."""
    assert "env_file" not in tools_service()
