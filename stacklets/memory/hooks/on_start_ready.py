"""Pull the memory vault on every `stack up memory`.

The install hook clones the vault once; this hook keeps it fresh on
every restart. Best-effort — a failed pull never blocks startup.
Readers fall back to whatever is already on disk, or to the seed.

It also re-derives the vault's `origin` URL on every run. Both halves
of that URL rot on their own schedule: the host part when the Mac takes
a new DHCP lease, the embedded token when Forgejo expires it. Neither
is refreshed by anything else, so a clone made months ago quietly stops
working. Re-pointing it here costs one git config write and makes a
restart the fix.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    BOT_USERNAME,
    authenticated_remote,
    brain_path_for,
    ensure_brain_projection_admin,
    ensure_vault_cloned,
    host_code_url,
    point_remote_at,
    pull_vault,
    purge_local_generated_memory_pages,
    vault_path_for,
    vault_remote_url,
)


def run(ctx):
    vault = vault_path_for(ctx.stack.data)
    brain = brain_path_for(ctx.stack.data)

    # Loopback, not the LAN address: this hook runs on the machine
    # Forgejo is published from. See `host_code_url`.
    code_url = host_code_url(ctx.env.get("CODE_URL", ""))
    token = ctx.secret("MEMORY_BOT_TOKEN")
    remote = (
        authenticated_remote(vault_remote_url(code_url), BOT_USERNAME, token)
        if code_url and token else ""
    )

    # If the vault never got cloned (install hook ran before code
    # stacklet was reachable, for example), try once more here. This
    # is the recovery path — same idempotent shape as install.
    if not (vault / ".git").exists():
        if not remote:
            ctx.step("Memory vault not cloned and Forgejo credentials missing; skipping")
            return
        if ensure_vault_cloned(vault, remote):
            ctx.step(f"Memory vault cloned to {vault}")
        else:
            ctx.step(f"Memory vault clone failed at {vault}")
            return
    else:
        if remote:
            point_remote_at(vault, remote)
        if pull_vault(vault):
            ctx.step("Memory vault pulled from Forgejo")
        else:
            ctx.step("Memory vault pull skipped (Forgejo unreachable or non-FF)")

    # Seamless B1 migration for existing installs. Those instances will
    # not rerun on_install_success, so create/clone brain here when
    # missing and purge legacy generated pages from the source repo.
    admin_user = ctx.env.get("ADMIN_USER", "")
    admin_password = ctx.env.get("ADMIN_PASSWORD", "")
    if not (code_url and admin_user and admin_password):
        ctx.step("Memory brain not cloned and admin credentials missing; skipping")
        return

    result = ensure_brain_projection_admin(
        code_url=code_url,
        admin_user=admin_user,
        admin_password=admin_password,
        brain_path=brain,
    )
    if result.get("skipped_reason"):
        ctx.step(f"Memory brain migration skipped ({result['skipped_reason']})")
        return
    if result.get("created_brain_repo"):
        ctx.step("Memory: created Forgejo repo family/brain")
    brain_seeds = result.get("brain_seeds", {})
    if brain_seeds.get("created"):
        ctx.step(f"Memory: pushed {len(brain_seeds['created'])} brain scaffold file(s)")
    memory_purge = result.get("memory_purge", {})
    if memory_purge.get("deleted"):
        ctx.step(f"Memory: purged {len(memory_purge['deleted'])} generated source page(s)")
    local_purge = purge_local_generated_memory_pages(vault)
    if local_purge.get("deleted"):
        ctx.step(f"Memory: removed {len(local_purge['deleted'])} local generated page(s)")
    if memory_purge.get("deleted") or local_purge.get("deleted"):
        pull_vault(vault)
    if result.get("cloned_brain"):
        ctx.step(f"Memory: cloned brain projection to {brain}")
    elif not (brain / ".git").exists():
        ctx.step(f"Memory brain projection clone failed at {brain}")
