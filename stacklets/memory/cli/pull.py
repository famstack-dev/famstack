"""stack memory pull — fast-forward the local vault from Forgejo.

Hand edits made through the Forgejo web UI or an Obsidian clone don't
reach the local working copy until someone pulls. This command is the
explicit refresh — call it after editing the wiki in Forgejo, or in
a cron if you want continuous sync.

`on_start_ready` already pulls on every `stack up memory`, so most
users only need this command when they've edited mid-session.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    BOT_USERNAME,
    authenticated_remote,
    ensure_vault_cloned,
    host_code_url,
    pull_vault,
    vault_path_for,
    vault_remote_url,
)

HELP = "Pull the latest memory from Forgejo into the local vault"


def run(args, stacklet, config):
    data_dir = config.get("data_dir")
    if not data_dir:
        return {"error": "stack data_dir not configured"}

    vault = vault_path_for(Path(data_dir))

    # First-time path: the install hook should have done this, but be
    # forgiving if the user is running `pull` before `stack up memory`
    # ever ran.
    if not (vault / ".git").exists():
        token = config.get("secrets", {}).get("memory__MEMORY_BOT_TOKEN", "")
        code_url = host_code_url(
            config.get("secrets", {}).get("__code_url", "") or _code_url(config)
        )
        if not (token and code_url):
            return {"error": "Vault not cloned and Forgejo credentials missing — run `stack up memory` first"}
        remote = authenticated_remote(vault_remote_url(code_url), BOT_USERNAME, token)
        if ensure_vault_cloned(vault, remote):
            print(f"Cloned memory vault to {vault}")
            return {"ok": True, "cloned": True, "vault": str(vault)}
        return {"error": f"Could not clone memory vault to {vault}"}

    if pull_vault(vault):
        print(f"Pulled latest memory into {vault}")
        return {"ok": True, "cloned": False, "vault": str(vault)}
    return {"error": f"Pull failed at {vault} (Forgejo unreachable, or local changes block fast-forward)"}


def _code_url(config) -> str:
    """Best-effort code-stacklet URL — used only when the install hook
    hasn't run yet so we don't yet have the URL cached anywhere obvious.
    """
    stck = config.get("stack")
    if isinstance(stck, dict):
        port = stck.get("code", {}).get("port", 42040)
    else:
        port = 42040
    return f"http://localhost:{port}"
