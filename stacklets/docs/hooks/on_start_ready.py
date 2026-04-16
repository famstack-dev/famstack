"""Docs on_start_ready — seed person tags and taxonomy after Paperless is healthy.

Runs on every `stack up docs`, after health checks pass. Ensures
person tags and category taxonomy stay in sync. Idempotent -- skips
existing entries, creates new ones for users or categories added
since last run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from seed import seed_person_tags, seed_taxonomy


def run(ctx):
    token = ctx.secret("API_TOKEN")
    if not token:
        return

    url = ctx.env.get("PAPERLESS_URL", "http://localhost:42020")

    seed_person_tags(url, token, ctx.users or [], step=ctx.step)

    language = ctx.env.get("LANGUAGE", "en")
    seed_taxonomy(url, token, language, step=ctx.step)
