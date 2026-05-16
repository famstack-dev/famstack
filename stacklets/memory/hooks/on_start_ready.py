"""Pull the memory vault on every `stack up memory`.

The install hook clones the vault once; this hook keeps it fresh on
every restart. Best-effort — a failed pull never blocks startup.
Readers fall back to whatever is already on disk, or to the seed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    BOT_USERNAME,
    authenticated_remote,
    ensure_vault_cloned,
    pull_vault,
    vault_path_for,
    vault_remote_url,
)


def run(ctx):
    vault = vault_path_for(ctx.stack.data)

    # If the vault never got cloned (install hook ran before code
    # stacklet was reachable, for example), try once more here. This
    # is the recovery path — same idempotent shape as install.
    if not (vault / ".git").exists():
        code_url = ctx.env.get("CODE_URL", "")
        token = ctx.secret("MEMORY_BOT_TOKEN")
        if not (code_url and token):
            ctx.step("Memory vault not cloned and Forgejo credentials missing; skipping")
            return
        remote = authenticated_remote(vault_remote_url(code_url), BOT_USERNAME, token)
        if ensure_vault_cloned(vault, remote):
            ctx.step(f"Memory vault cloned to {vault}")
        else:
            ctx.step(f"Memory vault clone failed at {vault}")
        return

    if pull_vault(vault):
        ctx.step("Memory vault pulled from Forgejo")
    else:
        ctx.step("Memory vault pull skipped (Forgejo unreachable or non-FF)")
