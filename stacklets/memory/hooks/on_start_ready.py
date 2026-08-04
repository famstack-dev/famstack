"""Pull the memory vault on every `stack up memory`.

The install hook clones the vault once; this hook keeps it fresh on
every restart. Best-effort — a failed pull never blocks startup.
Readers fall back to whatever is already on disk, or to the seed.

It also repairs the vault's `origin` URL on every run. Both halves of
that URL rot on their own schedule, and they need different cures. The
host part (a LAN IP baked in at clone time) is re-derived from the
current config, because the answer is knowable. The embedded token is
not: nothing anywhere holds a newer one, so a token Forgejo rejects —
or one that was never stored at all — is replaced with a freshly issued
one rather than rewritten. Between them, a clone made months ago starts
working again after a restart.
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
    reissue_write_token,
    remote_rejects_credentials,
    vault_path_for,
    vault_remote_url,
)


def run(ctx):
    vault = vault_path_for(ctx.stack.data)
    brain = brain_path_for(ctx.stack.data)

    # Loopback, not the LAN address: this hook runs on the machine
    # Forgejo is published from. See `host_code_url`.
    code_url = host_code_url(ctx.env.get("CODE_URL", ""))
    admin_user = ctx.env.get("ADMIN_USER", "")
    admin_password = ctx.env.get("ADMIN_PASSWORD", "")

    def remote_for(tok: str) -> str:
        if not (code_url and tok):
            return ""
        return authenticated_remote(
            vault_remote_url(code_url), BOT_USERNAME, tok,
        )

    token = ctx.secret("MEMORY_BOT_TOKEN")
    remote = remote_for(token)

    # The token is minted once at install and read forever after, so a
    # token Forgejo has since rejected cannot be re-derived from
    # anything — only replaced. Until it is, every host-side write
    # (a todo tick, an ontology edit) fails 401 and a restart changes
    # nothing, because re-pointing the remote writes the dead token
    # back. Checked before the pull so the pull gets the good one.
    #
    # A token that was never stored needs the same cure and used to get
    # none: instances installed before this hook's sibling learned to
    # persist one hold nothing, a missing token builds no remote, and
    # the repair below only ran once there was a remote to test. Those
    # instances answered "Forgejo credentials missing" to every vault
    # write until someone re-ran setup by hand. Both causes reduce to
    # "we hold no credential Forgejo accepts", so both mint one.
    if not code_url:
        reason = ""
    elif not token:
        reason = "Memory: no vault write token on file"
    elif remote_rejects_credentials(remote):
        reason = "Memory: Forgejo rejected the stored token"
    else:
        reason = ""

    if reason:
        if fresh := reissue_write_token(code_url, admin_user, admin_password):
            ctx.secret("MEMORY_BOT_TOKEN", fresh)
            remote = remote_for(fresh)
            ctx.step(f"{reason}; issued a new one")
        else:
            ctx.step(f"{reason} and it could not be replaced")

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
