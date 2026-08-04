"""Docs on_start_ready — seed person tags and taxonomy after Paperless is healthy.

Runs on every `stack up docs`, after health checks pass. Ensures
person tags and category taxonomy stay in sync. Idempotent -- skips
existing entries, creates new ones for users or categories added
since last run.

It also makes sure there is a working API token to seed with. This
hook used to read one and give up silently when it found none, which
is the state any instance predating the stored token was in: seeding
skipped every start, and the archivist -- which gets the same token
through rendered container env -- could not file a document. Paperless
will issue a replacement whenever asked, so there is nothing to give
up about. See auth.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from auth import ensure_api_token
from seed import seed_person_tags, seed_taxonomy


def run(ctx):
    token = ensure_api_token(ctx)
    if not token:
        return

    url = ctx.env.get("PAPERLESS_URL", "http://localhost:42020")

    seed_person_tags(url, token, ctx.users or [], step=ctx.step)

    language = ctx.env.get("LANGUAGE", "en")
    seed_taxonomy(url, token, language, step=ctx.step)
