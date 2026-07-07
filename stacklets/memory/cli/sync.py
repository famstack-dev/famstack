"""stack memory sync - mirror memory source into the brain projection now.

The background curator already keeps `family/brain` current, but it polls.
This command is the explicit fast path for tests and operators who need
read-your-writes in the rendered wiki projection without waiting for the next
curator tick. It mirrors source files only; it does not regenerate wiki pages.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    BOT_USERNAME,
    BRAIN_MIGRATION_TOKEN_NAME,
    TOKEN_SCOPES,
    authenticated_remote,
    brain_path_for,
    brain_remote_url,
    vault_path_for,
    vault_remote_url,
)
from stack.forgejo import ForgejoClient  # noqa: E402

HELP = "Mirror memory source into the brain projection now"

BRAIN_COMMIT_MESSAGE = "brain: project sync source"
BRAIN_AUTHOR_NAME = "memory-curator"
BRAIN_AUTHOR_EMAIL = "memory-curator@local"
GENERATED_NAMES = {"about.md", "index.md"}


def run(args, stacklet, config):
    data_dir = config.get("data_dir")
    if not data_dir:
        return {"error": "stack data_dir not configured"}

    memory = vault_path_for(Path(data_dir))
    brain = brain_path_for(Path(data_dir))
    state_dir = Path(data_dir) / "memory" / "curator"

    try:
        _refresh_remotes(config, memory, brain)
        result = sync_projection(memory, brain, state_dir)
    except RuntimeError as e:
        return {"error": str(e)}
    if "error" in result:
        return result
    print(
        "Synced memory source into brain "
        f"({result['copied']} copied, {result['removed']} removed)"
    )
    return result


def _refresh_remotes(config, memory: Path, brain: Path) -> None:
    secrets = config.get("secrets", {}) if config else {}
    code_url = secrets.get("__code_url", "") or _code_url(config)
    memory_token = secrets.get("memory__MEMORY_BOT_TOKEN", "")
    admin_password = (
        secrets.get("global__ADMIN_PASSWORD", "")
        or secrets.get("ADMIN_PASSWORD", "")
    )
    admin_user = "stackadmin"

    if memory_token:
        _git(
            memory, "remote", "set-url", "origin",
            authenticated_remote(vault_remote_url(code_url), BOT_USERNAME, memory_token),
        )

    if not admin_password:
        raise RuntimeError("Missing global__ADMIN_PASSWORD in secrets")
    admin = ForgejoClient(
        url=code_url,
        admin_user=admin_user,
        admin_password=admin_password,
    )
    token = admin.issue_token(
        admin_user, admin_password, BRAIN_MIGRATION_TOKEN_NAME, TOKEN_SCOPES,
    )
    _git(
        brain, "remote", "set-url", "origin",
        authenticated_remote(brain_remote_url(code_url), admin_user, token),
    )


def _code_url(config) -> str:
    stck = config.get("stack") if config else None
    if isinstance(stck, dict):
        port = stck.get("code", {}).get("port", 42040)
    else:
        port = 42040
    return f"http://localhost:{port}"


def sync_projection(memory: Path, brain: Path, state_dir: Path) -> dict:
    if not (memory / ".git").exists():
        return {"error": f"memory vault is not cloned at {memory}"}
    if not (brain / ".git").exists():
        return {"error": f"brain projection is not cloned at {brain}"}

    _git(memory, "pull", "--quiet", "--ff-only")
    _git(brain, "pull", "--quiet", "--ff-only")

    memory_paths = _tracked_files(memory)
    brain_paths = _tracked_files(brain)
    copied, removed = reconcile_source_files(memory, brain, memory_paths, brain_paths)
    committed = _commit_push_brain(brain)

    head = _git(memory, "rev-parse", "HEAD").strip()
    if head:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "last-mirrored-sha").write_text(head, encoding="utf-8")

    return {
        "ok": True,
        "copied": copied,
        "removed": removed,
        "committed": committed,
        "memory_head": head,
    }


def reconcile_source_files(
    memory: Path, brain: Path, memory_paths: list[str], brain_paths: list[str],
) -> tuple[int, int]:
    """Make brain's source files match memory's tracked source files."""
    memory_source = [p for p in memory_paths if is_source_path(p)]
    brain_source = {p for p in brain_paths if is_source_path(p)}
    memory_source_set = set(memory_source)

    copied = 0
    for rel in memory_source:
        src = memory / rel
        dst = brain / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        before = dst.read_bytes() if dst.exists() else None
        data = src.read_bytes()
        if before != data:
            dst.write_bytes(data)
            copied += 1

    removed = 0
    for rel in sorted(brain_source - memory_source_set):
        try:
            (brain / rel).unlink()
            removed += 1
        except OSError:
            pass
        _prune_empty_dirs((brain / rel).parent, stop=brain)

    return copied, removed


def is_source_path(path: str) -> bool:
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0] == ".git":
        return False
    if parts[-1] in GENERATED_NAMES:
        return False
    return True


def _tracked_files(repo: Path) -> list[str]:
    out = _git(repo, "ls-files", "-z")
    return [p for p in out.split("\0") if p]


def _commit_push_brain(brain: Path) -> bool:
    _git(brain, "add", "-A")
    diff = subprocess.run(
        ["git", "-C", str(brain), "diff", "--cached", "--quiet"],
        capture_output=True,
        text=True,
    )
    if diff.returncode == 0:
        return False
    if diff.returncode != 1:
        raise RuntimeError(diff.stderr.strip() or "git diff failed")
    _git(
        brain,
        "-c", f"user.name={BRAIN_AUTHOR_NAME}",
        "-c", f"user.email={BRAIN_AUTHOR_EMAIL}",
        "commit", "-m", BRAIN_COMMIT_MESSAGE,
    )
    _git(brain, "push", "--quiet")
    return True


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _prune_empty_dirs(path: Path, *, stop: Path) -> None:
    try:
        path.relative_to(stop)
    except ValueError:
        return
    while path != stop:
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent
