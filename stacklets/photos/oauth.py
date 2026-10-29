"""Immich login through the stack's OIDC provider.

Immich keeps its OAuth settings in its system config, in the database,
and does not read them from the environment. Its config file
(IMMICH_CONFIG_FILE) is no alternative: it replaces the whole system
config and makes every setting in the admin UI read-only. So the
settings go in through the admin API, `GET` and `PUT /api/system-config`.

The stacklet owns five keys of the `oauth` section: whether it is on,
the issuer, the client credentials and the button text. Scope, claims,
auto signup and everything outside `oauth` stay as Immich defaults them
or as the admin set them. The password login is never touched, so an
account that is not linked yet keeps working.

Without credentials nothing is written. That keeps a login an admin set
up by hand in the Immich UI. The cost: after `stack destroy` of the
provider, Immich keeps its OAuth settings and shows a button for an
issuer that is gone, until an admin turns it off. Once the credentials
are gone, the stacklet cannot tell its own settings from hand-made
ones, and switching off an admin's setup would be worse.

Stdlib only, like every host-side hook: Immich is reached through
`immich.py`.
"""

import json

from . import immich

BUTTON = "Sign in with family account"


# ── What the section should say ───────────────────────────────────────

def desired(env: dict) -> dict | None:
    """The OAuth keys this stacklet owns, or None without credentials."""
    issuer = env.get("OIDC_ISSUER", "")
    client_id = env.get("OIDC_CLIENT_ID", "")
    secret = env.get("OIDC_CLIENT_SECRET", "")
    if not (issuer and client_id and secret):
        return None
    return {
        "enabled": True,
        "issuerUrl": issuer,
        "clientId": client_id,
        "clientSecret": secret,
        "buttonText": BUTTON,
    }


def changed_keys(current: dict, want: dict) -> list[str]:
    """Names of the owned keys whose value differs. Names only: one of
    them is the client secret, and this list is printed."""
    return [k for k, v in want.items() if current.get(k) != v]


# ── Talking to Immich ─────────────────────────────────────────────────

def sync(base_url: str, admin_email: str, admin_password: str, env: dict, step) -> None:
    """Bring Immich's OAuth section in line with the provider's credentials.

    Prints one line when something changed and nothing otherwise, since
    this runs on every `stack up photos`. Errors are reported, not
    raised: a failed sync must not stop the stacklet, Immich and its
    password login still work, and the next run tries again.

    The session it opens it closes, whatever the outcome, because Immich
    lists every open session as a device of the tech admin.
    """
    want = desired(env)
    if want is None:
        return
    try:
        # The same login setup.py uses. The config endpoints need an
        # admin session, and the tech admin is the one account the
        # stack always has the password for.
        status, token = immich.login(base_url, admin_email, admin_password)
        if token is None:
            step(f"Immich OAuth settings not updated: the login answered {status}")
            return
        try:
            _apply(base_url, token, want, step)
        finally:
            immich.logout(base_url, token)
    except OSError as e:  # URLError, a refused connection, a timeout
        step(f"Immich OAuth settings not updated: {getattr(e, 'reason', e)}")


def _apply(base_url, token, want, step):
    """Write the owned keys that differ, in the session `token` holds."""
    status, config = immich.request("GET", f"{base_url}/api/system-config", token=token)
    if status != 200:
        step(_not_updated(status, config))
        return
    keys = changed_keys(config.get("oauth", {}), want)
    if not keys:
        return
    # PUT takes the whole config: send back what Immich returned,
    # with only the owned keys replaced.
    config["oauth"] = {**config.get("oauth", {}), **want}
    status, answer = immich.request("PUT", f"{base_url}/api/system-config", config, token=token)
    if status != 200:
        step(_not_updated(status, answer))
        return
    step(f"Immich login through the OIDC provider: {', '.join(keys)} updated")


def _not_updated(status, answer):
    return f"Immich OAuth settings not updated: {status} {json.dumps(answer)[:160]}"
