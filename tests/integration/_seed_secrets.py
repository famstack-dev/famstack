"""Seed the global secrets that `stack init` normally creates interactively.

Idempotent — never overwrites existing values. Called by both the
pytest fixture (`paperless` / `matrix`) and the `stacktests` CLI
wrapper before any `stack up`, so stacklets find the admin password
during on_install_success regardless of entry point.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
INSTANCE_DIR = HERE / "instance"

sys.path.insert(0, str(REPO_ROOT / "lib"))

from stack.secrets import TomlSecretStore

TEST_USERS = ("homer", "marge", "bart", "lisa")
TEST_ADMIN_PASSWORD = "testpass"


def seed() -> None:
    store = TomlSecretStore(INSTANCE_DIR / ".stack" / "secrets.toml")
    if not store.get("_", "ADMIN_PASSWORD"):
        store.set("global", "ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    for uid in TEST_USERS:
        key = f"USER_{uid.upper()}_PASSWORD"
        if not store.get("_", key):
            store.set("global", key, uid)


if __name__ == "__main__":
    seed()
