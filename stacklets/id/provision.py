"""Provisioning for the id stacklet (Pocket ID).

Two jobs, both idempotent, both through Pocket ID's admin API with the
generated STATIC_API_KEY:

1. People. Every entry in users.toml gets a Pocket ID account (admins
   from role = "admin"). New accounts get a one-time login link written
   to {ID_ONBOARDING_DIR}/<username>.txt, mode 600. The person opens
   it, registers a passkey, done. Links are never printed.

2. OIDC clients. Every stacklet whose manifest has an [oidc] table gets
   a client named after it, with the callback URLs rendered against that
   stacklet's public URL. Client id and secret land in the famstack
   secrets store as id__CLIENT_<STACKLET>_ID / _SECRET, so the stacklet
   can reference {id__CLIENT_<STACKLET>_SECRET} in its env.defaults.

Stdlib only, like every host-side hook.
"""

import collections
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from stack.users import user_id

API = "http://127.0.0.1:42100/api"
LINK_TTL_SECONDS = 7 * 24 * 3600


class PocketID:
    def __init__(self, api_key: str):
        self.key = api_key

    def _call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            API + path, data=data, method=method,
            headers={"X-API-KEY": self.key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    @staticmethod
    def _rows(res):
        return res.get("data", res) if isinstance(res, dict) else res

    def users(self):
        return self._rows(self._call("GET", "/users?pagination[limit]=200"))

    def create_user(self, username, email, first, last, is_admin):
        return self._call("POST", "/users", {
            "username": username, "email": email, "emailVerified": True,
            "firstName": first, "lastName": last, "displayName": f"{first} {last}".strip(),
            "isAdmin": is_admin,
        })

    def one_time_token(self, uid):
        return self._call("POST", f"/users/{uid}/one-time-access-token",
                          {"ttl": LINK_TTL_SECONDS})["token"]

    def clients(self):
        return self._rows(self._call("GET", "/oidc/clients?pagination[limit]=200"))

    def create_client(self, name, callbacks):
        return self._call("POST", "/oidc/clients", {
            "name": name, "callbackURLs": callbacks, "logoutCallbackURLs": [],
            "isPublic": False, "pkceEnabled": False, "isGroupRestricted": False,
        })

    def update_callbacks(self, cid, name, callbacks):
        return self._call("PUT", f"/oidc/clients/{cid}", {
            "name": name, "callbackURLs": callbacks, "logoutCallbackURLs": [],
            "isPublic": False, "pkceEnabled": False, "isGroupRestricted": False,
        })

    def rename_user(self, uid, username, email, first, last, is_admin=True):
        # The API requires an email on update (REQUIRE_USER_EMAIL default).
        return self._call("PUT", f"/users/{uid}", {
            "username": username, "email": email, "firstName": first, "lastName": last,
            "displayName": f"{first} {last}".strip(), "isAdmin": is_admin,
        })

    def create_secret(self, cid):
        return self._call("POST", f"/oidc/clients/{cid}/secrets", {})["secret"]


def _api(ctx):
    key = ctx.env.get("STATIC_API_KEY") or ctx.stack.secrets.get("id", "STATIC_API_KEY")
    if not key:
        ctx.step("No STATIC_API_KEY for id, skipping provisioning")
        return None
    return PocketID(key)


def provision_users(ctx):
    api = _api(ctx)
    if not api:
        return
    try:
        existing = {u["username"] for u in api.users()}
    except urllib.error.URLError as e:
        ctx.step(f"Pocket ID API not reachable, skipping users: {e}")
        return
    out_dir = Path(ctx.env["ID_ONBOARDING_DIR"])
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    base_url = ctx.env.get("ID_URL", "").rstrip("/")
    created = 0
    for u in ctx.users:
        uid = user_id(u)
        if uid in existing:
            continue
        parts = (u.get("name") or uid).split()
        first, last = parts[0], " ".join(parts[1:])
        try:
            user = api.create_user(uid, u.get("email"), first, last, u.get("role") == "admin")
            token = api.one_time_token(user["id"])
        except urllib.error.HTTPError as e:
            ctx.step(f"Pocket ID refused user {uid}: {e.code} {e.read().decode()[:120]}")
            continue
        link_file = out_dir / f"{uid}.txt"
        link_file.write_text(f"{base_url}/lc/{token}\n")
        os.chmod(link_file, 0o600)
        created += 1
        ctx.step(f"Pocket ID account {uid} created, login link in {link_file}")
    if not created:
        ctx.step("Pocket ID accounts: all present")


def provision_clients(ctx):
    api = _api(ctx)
    if not api:
        return
    try:
        by_name = {c["name"]: c for c in api.clients()}
    except urllib.error.URLError as e:
        ctx.step(f"Pocket ID API not reachable, skipping clients: {e}")
        return
    stack = ctx.stack
    template_vars = stack._build_template_vars()
    for s in stack.discover():
        oidc = s.get("manifest", {}).get("oidc")
        if not oidc:
            continue
        sid = s["id"]
        vars_for = collections.defaultdict(str, template_vars)
        vars_for["url"] = stack._public_url(sid, s.get("port", 0))
        callbacks = [c.format_map(vars_for) for c in oidc.get("callbacks", [])]
        name = oidc.get("name") or s.get("name") or sid
        key_id, key_secret = f"CLIENT_{sid.upper()}_ID", f"CLIENT_{sid.upper()}_SECRET"
        try:
            client = by_name.get(name)
            if client is None:
                client = api.create_client(name, callbacks)
                by_name[name] = client
                ctx.step(f"OIDC client '{name}' created for {sid}")
            elif sorted(client.get("callbackURLs") or []) != sorted(callbacks):
                api.update_callbacks(client["id"], name, callbacks)
                ctx.step(f"OIDC client '{name}': callbacks updated")
            stack.secrets.set("id", key_id, client["id"])
            if not stack.secrets.get("id", key_secret):
                stack.secrets.set("id", key_secret, api.create_secret(client["id"]))
                ctx.step(f"OIDC client '{name}': secret stored as id__{key_secret}")
        except urllib.error.HTTPError as e:
            ctx.step(f"Pocket ID refused client '{name}': {e.code} {e.read().decode()[:160]}")


SERVICE_ACCOUNT = "famstack-automation"
SERVICE_EMAIL = "automation@home.local"  # same convention as stackadmin@home.local


def name_service_account(ctx, api):
    """Pocket ID creates 'static-api-user-<random>' for the static API
    key, with a fixed internal id. The name is not configurable at
    creation, but a rename persists. Give it a name that says what it
    is when it shows up in the user list."""
    for u in api.users():
        if u["username"].startswith("static-api-user-"):
            api.rename_user(u["id"], SERVICE_ACCOUNT, SERVICE_EMAIL, "famstack", "automation")
            ctx.step(f"Service account renamed to {SERVICE_ACCOUNT}")


def provision(ctx):
    # Accounts are created only when stack.toml says so:
    #   [id]
    #   provision_users = true
    # Off by default: a stale users.toml would otherwise turn into real
    # accounts with login links on the next 'stack up id'.
    if str(ctx.stack._cfg("id", "provision_users", False)).lower() == "true":
        provision_users(ctx)
    else:
        ctx.step("Account provisioning off ([id] provision_users = true enables it)")
    api = _api(ctx)
    if api:
        try:
            name_service_account(ctx, api)
        except urllib.error.HTTPError as e:
            ctx.step(f"Service account rename refused: {e.code} {e.read().decode()[:120]}")
        except (urllib.error.URLError, KeyError) as e:
            ctx.step(f"Service account rename skipped: {e}")
    provision_clients(ctx)
