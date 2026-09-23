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

Stdlib only, like every host-side hook.
"""

import json
import urllib.error
import urllib.request

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

def _call(base_url, method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{base_url}{path}", method=method, headers=headers,
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"null")


def sync(base_url: str, admin_email: str, admin_password: str, env: dict, step) -> None:
    """Bring Immich's OAuth section in line with the provider's credentials.

    Prints one line when something changed and nothing otherwise, since
    this runs on every `stack up photos`. Errors are reported, not
    raised: a failed sync must not stop the stacklet, Immich and its
    password login still work, and the next run tries again.
    """
    want = desired(env)
    if want is None:
        return
    try:
        # The same login setup.py uses. The config endpoints need an
        # admin session, and the tech admin is the one account the
        # stack always has the password for.
        token = _call(base_url, "POST", "/api/auth/login",
                      {"email": admin_email, "password": admin_password})["accessToken"]
        config = _call(base_url, "GET", "/api/system-config", token=token)
        keys = changed_keys(config.get("oauth", {}), want)
        if not keys:
            return
        # PUT takes the whole config: send back what Immich returned,
        # with only the owned keys replaced.
        config["oauth"] = {**config.get("oauth", {}), **want}
        _call(base_url, "PUT", "/api/system-config", config, token=token)
        step(f"Immich login through the OIDC provider: {', '.join(keys)} updated")
    except urllib.error.HTTPError as e:
        step(f"Immich OAuth settings not updated: {e.code} {e.read().decode()[:160]}")
    except urllib.error.URLError as e:
        step(f"Immich OAuth settings not updated: {e.reason}")
