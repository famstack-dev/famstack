"""Set up the memory's repositories in Forgejo on first install.

Runs after the install completes successfully. Idempotent: every step
in the pipeline is safe to re-run.

The hook is a thin wrapper around `lib.install_memory_to_forgejo_admin`:
all the orchestration lives in the lib so it can be unit-tested
without constructing a `StackContext`. The hook unwraps the admin
credentials from `ctx`, reports progress, and then asks the curator to
sync: the curator makes its clone of the new memory and gives it its
seed as its first commit (ADR-019).

Uses admin credentials directly for repo creation. The memory's content
is the curator's to write, the seed included.

If Forgejo is unreachable (no `code` stacklet up, network down), the
hook logs a skip and returns successfully so a partial stack still
finishes installing. The next `stack up memory` runs the hook again.
"""

from __future__ import annotations


# lib.py is one level up from hooks/
from ..lib import (
    BRAIN_REPO_NAME,
    REPO_NAME,
    REPO_OWNER,
    brain_path_for,
    install_memory_to_forgejo_admin,
)
from ..memory_api import CuratorDown, MemoryAPI, Refused


def run(ctx):
    code_url = ctx.env.get("CODE_URL")
    admin_user = ctx.env.get("ADMIN_USER")
    admin_password = ctx.env.get("ADMIN_PASSWORD")
    if not (code_url and admin_user and admin_password):
        ctx.step("Memory install: missing CODE_URL or admin credentials; skipping")
        return

    brain = brain_path_for(ctx.stack.data)
    result = install_memory_to_forgejo_admin(
        code_url=code_url,
        admin_user=admin_user,
        admin_password=admin_password,
        brain_path=brain,
    )

    if result.get("skipped_reason"):
        ctx.step(f"Memory install: skipped ({result['skipped_reason']})")
        return

    if result.get("created_org"):
        ctx.step(f"Memory: created Forgejo org {REPO_OWNER!r}")
    if result.get("created_repo"):
        ctx.step(f"Memory: created Forgejo repo {REPO_OWNER}/{REPO_NAME}")
    if result.get("created_brain_repo"):
        ctx.step(f"Memory: created Forgejo repo {REPO_OWNER}/{BRAIN_REPO_NAME}")

    brain_seeds = result.get("brain_seeds", {})
    if brain_seeds.get("created"):
        ctx.step(f"Memory: pushed {len(brain_seeds['created'])} brain scaffold file(s)")
    if result.get("cloned_brain"):
        ctx.step(f"Memory: cloned brain projection to {brain}")

    _curator_clones_and_seeds(ctx)


def _curator_clones_and_seeds(ctx) -> None:
    """Ask the curator to sync now: it clones the new memory and gives it its
    seed, before the hooks and migrations that follow write to it. A failure
    is reported and never fails the install: the curator syncs every cycle."""
    try:
        synced = MemoryAPI.from_hook(ctx).sync()
    except (CuratorDown, Refused) as e:
        ctx.warn(f"Memory: the curator could not be asked to make its clone yet ({e}); "
                 "it makes it on its next cycle")
        return
    if synced.state == "ok":
        ctx.step("Memory: the curator holds the memory's clone")
    else:
        ctx.warn(f"Memory: the curator could not make its clone yet ({synced.detail}); "
                 "it tries again every cycle")
