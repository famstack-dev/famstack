"""Bootstrap the repo root as a test instance.

The integration rig runs against the *actual* production instance
layout: `stack.toml`, `users.toml`, and `.stack/` at the repo root.
This function:

  1. Refuses to clobber a real user's stack if one is there. It looks
     for a sentinel marker at `.stack/.test-instance`. Without the
     marker, any pre-existing `stack.toml` or `.stack/` means someone's
     actual famstack lives here and we bail out with a cleanup hint.

  2. Otherwise copies the test templates (`tests/integration/instance/
     stack.toml` + `users.toml`) into the repo root, writes the sentinel,
     and seeds the global secrets (admin + user passwords) that
     `stack init` normally creates interactively.

Idempotent — existing values are preserved, so running the full rig
repeatedly is free.

Called by both the pytest fixtures and the `stacktests` bash wrapper
so every entry point gets the same preflight + seed treatment.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
TEMPLATE_DIR = HERE / "instance"

sys.path.insert(0, str(REPO_ROOT / "lib"))

from stack.secrets import TomlSecretStore

TEST_USERS = ("homer", "marge", "bart", "lisa")
TEST_ADMIN_PASSWORD = "testpass"

SENTINEL = REPO_ROOT / ".stack" / ".test-instance"
STACK_TOML = REPO_ROOT / "stack.toml"
USERS_TOML = REPO_ROOT / "users.toml"


class TestInstanceConflict(RuntimeError):
    """Raised when the repo root already holds a non-test stack.toml.

    The caller is expected to tell the user how to clean up — either
    via pytest.fail or a shell-friendly error.
    """


def _conflict_message() -> str:
    return (
        "Refusing to overwrite an existing stack at the repo root.\n\n"
        f"  {STACK_TOML}  -- exists\n"
        f"  {SENTINEL}  -- missing (so this isn't a test-owned instance)\n\n"
        "If this is your actual famstack, take it down first:\n\n"
        "  stack down all && stack destroy all --yes\n"
        "  rm -f stack.toml users.toml && rm -rf .stack ~/famstack-data\n\n"
        "Or use `tests/integration/stacktests cleanup` to reset a\n"
        "previously-test-owned repo root."
    )


def seed() -> None:
    """Ensure the repo root is set up as a test instance, then seed secrets.

    Called from pytest fixtures and the stacktests wrapper. Raises
    `TestInstanceConflict` if the repo root looks like a real user's
    stack and we shouldn't touch it.
    """
    sentinel_present = SENTINEL.exists()
    has_stack_toml = STACK_TOML.exists()
    has_dot_stack = (REPO_ROOT / ".stack").exists()

    if not sentinel_present and (has_stack_toml or has_dot_stack):
        # A real user's stack is here. Don't wipe it.
        # Exception: repo_root/.stack/secrets.toml alone (no stack.toml)
        # from an aborted prior test counts as recoverable — treat it
        # like a stale sentinel and proceed.
        if has_stack_toml:
            raise TestInstanceConflict(_conflict_message())

    # ── Templates -> repo root ──────────────────────────────────────
    (REPO_ROOT / ".stack").mkdir(exist_ok=True)
    if not STACK_TOML.exists():
        shutil.copy(TEMPLATE_DIR / "stack.toml", STACK_TOML)
    if not USERS_TOML.exists():
        shutil.copy(TEMPLATE_DIR / "users.toml", USERS_TOML)
    if not SENTINEL.exists():
        SENTINEL.write_text(
            "# Written by tests/integration/_seed_secrets.py.\n"
            "# Marks this repo root as an active test instance — the\n"
            "# rig overwrites stack.toml, users.toml, and .stack/ here.\n"
            "# Remove this file (plus the configs) to reclaim the repo\n"
            "# for a real stack.\n"
        )

    # ── Secrets ─────────────────────────────────────────────────────
    store = TomlSecretStore(REPO_ROOT / ".stack" / "secrets.toml")
    if not store.get("_", "ADMIN_PASSWORD"):
        store.set("global", "ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    for uid in TEST_USERS:
        key = f"USER_{uid.upper()}_PASSWORD"
        if not store.get("_", key):
            store.set("global", key, uid)


if __name__ == "__main__":
    try:
        seed()
    except TestInstanceConflict as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
