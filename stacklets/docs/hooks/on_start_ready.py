"""Docs on_start_ready — seed person tags after Paperless is healthy.

Runs on every `stack up docs`, after health checks pass. Ensures
person tags in Paperless stay in sync with users.toml. Idempotent --
skips existing tags, creates new ones for users added since last run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from seed import seed_person_tags

PAPERLESS_URL = "http://localhost:42020"


def run(ctx):
    token = ctx.secret("API_TOKEN")
    if not token:
        return

    users = ctx.users
    if not users:
        return

    seed_person_tags(PAPERLESS_URL, token, users, step=ctx.step)
