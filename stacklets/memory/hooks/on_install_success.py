"""Push memory seeds to Forgejo on first install.

Runs after the install completes successfully. Idempotent: every step
in the pipeline is safe to re-run.

The hook is a thin wrapper around `lib.install_memory_to_forgejo` —
all the orchestration lives in the lib so it can be unit-tested
without constructing a `StackContext`. The hook's only job is to
unwrap admin credentials from `ctx`, persist the bot password and
token through `ctx.secret`, and report progress.

If Forgejo is unreachable (no `code` stacklet up, network down), the
hook logs a skip and returns successfully so a partial stack still
finishes installing. The next `stack up memory` runs the hook again.
"""

from __future__ import annotations

import secrets as _secrets
import sys
from pathlib import Path

# lib.py is one level up from hooks/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    DEFAULT_SHARED_BUCKET,
    REPO_NAME,
    REPO_OWNER,
    install_memory_to_forgejo,
    vault_path_for,
)


def run(ctx):
    code_url = ctx.env.get("CODE_URL")
    admin_user = ctx.env.get("ADMIN_USER")
    admin_password = ctx.env.get("ADMIN_PASSWORD")
    if not (code_url and admin_user and admin_password):
        ctx.step("Memory install: missing CODE_URL or admin credentials; skipping")
        return

    # Bot password is generated once and reused on every re-up so the
    # token can be re-issued if it ever needs rotation.
    bot_password = ctx.secret("MEMORY_BOT_PASSWORD")
    if not bot_password:
        bot_password = _secrets.token_urlsafe(24)
        ctx.secret("MEMORY_BOT_PASSWORD", bot_password)

    bot_token = ctx.secret("MEMORY_BOT_TOKEN")

    vault = vault_path_for(ctx.stack.data)
    shared_bucket = ctx.env.get("SHARED_BUCKET") or DEFAULT_SHARED_BUCKET

    result = install_memory_to_forgejo(
        code_url=code_url,
        admin_user=admin_user,
        admin_password=admin_password,
        bot_password=bot_password,
        bot_token=bot_token,
        vault_path=vault,
        shared_bucket=shared_bucket,
    )

    if result.get("skipped_reason"):
        ctx.step(f"Memory install: skipped ({result['skipped_reason']})")
        return

    if not bot_token and result.get("bot_token"):
        ctx.secret("MEMORY_BOT_TOKEN", result["bot_token"])

    if result.get("created_org"):
        ctx.step(f"Memory: created Forgejo org {REPO_OWNER!r}")
    if result.get("created_repo"):
        ctx.step(f"Memory: created Forgejo repo {REPO_OWNER}/{REPO_NAME}")

    seeds = result.get("seeds", {})
    if seeds.get("created"):
        ctx.step(f"Memory: pushed {len(seeds['created'])} seed file(s)")

    if result.get("cloned_vault"):
        ctx.step(f"Memory: cloned vault to {vault}")
