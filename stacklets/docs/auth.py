"""Getting, and keeping, the Paperless API token.

Every write famstack makes to Paperless carries this token. The
archivist files documents with it (`core` renders it into the bot
runner as `PAPERLESS_TOKEN`), and the start hook seeds person tags and
the category taxonomy with it. It is not a secret we invent: Paperless
issues it against the admin's own credentials, which is what makes a
lost one always recoverable and a stale one always detectable.

That matters because the token used to be obtained during install and
never again. Paperless binds a token to its database, so a
`stack destroy docs` + `stack up docs` cycle invalidates it, and an
instance installed before this stacklet stored one has none at all.
Either way the install hook had already run for the last time: the
archivist quietly stopped being able to file anything, `stack up docs`
skipped its seeding without saying why, and the only cure was
re-running setup by hand.

Both hooks come through here now, so every `stack up docs` re-checks
the token it is holding and asks for a new one when the answer is no.
"""

from __future__ import annotations

DEFAULT_URL = "http://localhost:42020"


def ensure_api_token(ctx) -> str:
    """Return a token Paperless currently accepts, obtaining one if needed.

    Returns "" when no token could be had, which happens two ways: there
    are no admin credentials to authenticate as, or Paperless did not
    answer. Callers treat that as "do nothing this run" rather than as a
    failure, because the next start tries again and a stack coming up
    with Paperless still migrating its database is ordinary.
    """
    url = ctx.env.get("PAPERLESS_URL", DEFAULT_URL)

    stored = ctx.secret("API_TOKEN")
    if stored and _token_accepted(ctx, url, stored):
        return stored
    if stored:
        ctx.step("Stored API token is invalid — obtaining a new one")

    username = ctx.env.get("ADMIN_USER", "")
    password = ctx.secret("ADMIN_PASSWORD")
    if not (username and password):
        ctx.step("No admin credentials — skipping API token")
        return ""

    ctx.step("Obtaining API token...")
    try:
        data = ctx.http_post(
            f"{url}/api/token/",
            f"username={username}&password={password}",
        )
    except Exception as e:
        ctx.step(f"Could not obtain API token: {e}")
        return ""

    fresh = data.get("token", "")
    if not fresh:
        ctx.step("Unexpected response from Paperless token endpoint")
        return ""

    ctx.secret("API_TOKEN", fresh)
    ctx.step("API token saved")
    return fresh


def _token_accepted(ctx, url: str, token: str) -> bool:
    """True when Paperless still answers to this token.

    Deliberately cannot tell "rejected" from "unreachable", and does not
    need to: the caller's response to both is to ask for a new token,
    and that request fails too when Paperless is down. The run ends with
    the stored token untouched either way.
    """
    try:
        ctx.http_get(
            f"{url}/api/documents/",
            headers={"Authorization": f"Token {token}"},
        )
        return True
    except Exception:
        return False
