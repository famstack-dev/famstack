"""Keep the memory's Forgejo side in order on every `stack up memory`.

The curator holds the memory's clone and keeps it up to date (ADR-019).
This hook gives every chat account a person file, and makes sure Forgejo
tells the curator of every push to the vault (a webhook, added once).
Best-effort: a failure is reported and never blocks startup.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    brain_path_for,
    ensure_brain_projection_admin,
    add_admins_to_the_family,
    missing_people,
    people_folder,
    ensure_push_webhook,
    host_code_url,
)
from memory_api import MEMORY_BOT, CuratorDown, MemoryAPI, Refused, put  # noqa: E402

from stack.forgejo import ForgejoClient, ForgejoError  # noqa: E402
from stack.vault import DEFAULT_SHARED_BUCKET  # noqa: E402


def _ensure_people(ctx) -> None:
    """Add a person file for every account that has none, in one commit by
    the memory bot through the curator (`missing_people`). A failure is
    reported and never blocks startup: every reader keeps its previous
    behaviour until the files exist, and the next `stack up memory` tries
    again."""
    bucket = ctx.env.get("SHARED_BUCKET") or DEFAULT_SHARED_BUCKET
    memory = MemoryAPI.from_hook(ctx)
    try:
        there = [f.path for f in memory.files(people_folder(bucket))]
        missing = missing_people(ctx.users, there, shared_bucket=bucket)
        if missing:
            memory.commit([put(path, text, base="") for path, text in missing.items()],
                          by=MEMORY_BOT, kind="people", title="add a file for each chat account")
    except (Refused, CuratorDown) as e:
        ctx.warn(f"Memory: could not write the person files: {e}")
        return
    if missing:
        ctx.step(f"Memory: added {', '.join(Path(p).stem for p in missing)} to {people_folder(bucket)}/")


def run(ctx):
    brain = brain_path_for(ctx.stack.data)

    # Loopback, not the LAN address: this hook runs on the machine
    # Forgejo is published from. See `host_code_url`.
    code_url = host_code_url(ctx.env.get("CODE_URL", ""))
    admin_user = ctx.env.get("ADMIN_USER", "")
    admin_password = ctx.env.get("ADMIN_PASSWORD", "")

    _ensure_people(ctx)

    if not (code_url and admin_user and admin_password):
        ctx.step("Memory brain not cloned and admin credentials missing; skipping")
        return
    _ensure_brain(ctx, brain, code_url, admin_user, admin_password)
    _ensure_push_webhook(ctx, code_url, admin_user, admin_password)
    _admins_see_the_memory(ctx, code_url, admin_user, admin_password)


def _admins_see_the_memory(ctx, code_url: str, admin_user: str, admin_password: str) -> None:
    """The household's admins (users.toml's `role = "admin"`) in the family
    org's Owners team, on every start, so an admin added later gets the
    memory's repositories too. A failure is reported and never blocks startup."""
    from stack.users import user_id
    admins = [user_id(u) for u in ctx.users or [] if u.get("role") == "admin"]
    client = ForgejoClient(url=code_url, admin_user=admin_user, admin_password=admin_password)
    try:
        add_admins_to_the_family(client, admins)
    except (ForgejoError, OSError) as e:
        ctx.warn(f"Memory: could not give the admins the family's repositories in Forgejo: {e}")


def _ensure_brain(ctx, brain, code_url: str, admin_user: str, admin_password: str) -> None:
    """Seamless B1 migration for existing installs, which will not rerun
    on_install_success: create and clone the brain when missing. A failure
    is reported and never blocks startup."""
    try:
        result = ensure_brain_projection_admin(
            code_url=code_url,
            admin_user=admin_user,
            admin_password=admin_password,
            brain_path=brain,
        )
    except (ForgejoError, OSError) as e:
        ctx.warn(f"Memory: the brain projection could not be set up: {e}")
        return
    if result.get("skipped_reason"):
        ctx.step(f"Memory brain migration skipped ({result['skipped_reason']})")
        return
    if result.get("created_brain_repo"):
        ctx.step("Memory: created Forgejo repo family/brain")
    brain_seeds = result.get("brain_seeds", {})
    if brain_seeds.get("created"):
        ctx.step(f"Memory: pushed {len(brain_seeds['created'])} brain scaffold file(s)")
    if result.get("cloned_brain"):
        ctx.step(f"Memory: cloned brain projection to {brain}")
    elif not (brain / ".git").exists():
        ctx.warn(f"Memory brain projection clone failed at {brain}: the wiki and the diary will not update")


def _ensure_push_webhook(ctx, code_url: str, admin_user: str, admin_password: str) -> None:
    """Have Forgejo tell the curator of every push to the memory repo.

    A failure is reported and never blocks startup: the curator's poll
    still finds every push, only later.
    """
    client = ForgejoClient(url=code_url, admin_user=admin_user, admin_password=admin_password)
    try:
        if ensure_push_webhook(client):
            ctx.step("Memory: Forgejo now tells the curator of every push to family/memory")
    except (ForgejoError, OSError) as e:
        ctx.warn(f"Memory: could not add the push webhook, the curator polls instead: {e}")
