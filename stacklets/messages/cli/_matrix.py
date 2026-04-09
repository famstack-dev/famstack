"""
Shared Matrix client for the chat stacklet CLI.

Handles authentication, device identity persistence, and the core Matrix
API calls. Every CLI command that talks to Synapse uses this module —
it's the single point of contact with the Matrix protocol.

Device handling: Matrix assigns a device_id on each login. If you log in
repeatedly without reusing the device_id, each login creates a new device
and the user sees "new session" warnings. We persist the device_id and
access_token in .stack/messages/session-{user}.toml so the CLI always reuses the
same session. This keeps the device list clean.
"""

import json
import os
import random
import ssl
import sys
import time
try:
    import tomllib
except ModuleNotFoundError:
    from stack._vendor import tomli as tomllib
import urllib.error
import urllib.request
from pathlib import Path

# ── TLS ──────────────────────────────────────────────────────────────────────
# Synapse is on the LAN — skip certificate verification.

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _api(method, url, body=None, token=None):
    """Make an HTTP request to the Matrix API.

    Returns (status_code, parsed_json). On HTTP errors the status code
    and error body are returned — the caller decides what to do.
    """
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode())
        except Exception:
            err_body = {"errcode": "UNKNOWN", "error": e.reason}
        return e.code, err_body
    except urllib.error.URLError as e:
        return 0, {"errcode": "NETWORK", "error": f"Connection failed: {e.reason}"}
    except TimeoutError:
        return 0, {"errcode": "TIMEOUT", "error": "Request timed out"}

def _get(url, **kw):   return _api("GET", url, **kw)
def _post(url, body=None, **kw): return _api("POST", url, body=body, **kw)
def _put(url, body=None, **kw):  return _api("PUT", url, body=body, **kw)


# ── Session persistence ─────────────────────────────────────────────────────
#
# We store the admin's access_token and device_id so the CLI reuses the
# same Matrix session across invocations. This avoids creating a new device
# on every command and keeps the session list clean in Element.

def _session_path(repo_root, username):
    return Path(repo_root) / ".stack" / "messages" / f"session-{username}.toml"

def _load_session(repo_root, username):
    """Load a saved Matrix session (token + device_id), or None."""
    path = _session_path(repo_root, username)
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return tomllib.load(f)

def _toml_escape(value):
    """Escape a string value for safe inclusion in a TOML double-quoted string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')

def _save_session(repo_root, username, token, device_id, user_id):
    """Persist the Matrix session for reuse by future CLI calls."""
    path = _session_path(repo_root, username)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Matrix session for {username} — auto-generated, do not commit",
        f'access_token = "{_toml_escape(token)}"',
        f'device_id = "{_toml_escape(device_id)}"',
        f'user_id = "{_toml_escape(user_id)}"',
    ]
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)


# ── Matrix client ────────────────────────────────────────────────────────────

class MatrixClient:
    """Minimal Matrix client for the famstack CLI.

    Handles login with device persistence, room resolution, user creation,
    and message sending. Not a general-purpose Matrix SDK — just the
    operations famstack needs.
    """

    def __init__(self, base_url, server_name, repo_root):
        self.base_url = base_url.rstrip("/")
        self.server_name = server_name
        self.repo_root = repo_root
        self.token = None
        self.user_id = None
        self.device_id = None

    def _url(self, path):
        return f"{self.base_url}{path}"

    def _full_user(self, name):
        """Expand a bare username to a full Matrix user ID."""
        if name.startswith("@"):
            return name
        return f"@{name}:{self.server_name}"

    def _full_room(self, alias):
        """Expand a bare room name to a full Matrix room alias."""
        if alias.startswith("#") or alias.startswith("!"):
            return alias
        return f"#{alias}:{self.server_name}"

    # ── Authentication ───────────────────────────────────────────────────

    def login(self, username, password):
        """Log in with session reuse.

        Reuses an existing session if the token is still valid. If the
        saved token is expired or missing, performs a fresh login with
        a stable device_id so we don't create device spam.

        Each username gets its own session file in .stack/.
        """
        # Try the saved session first
        session = _load_session(self.repo_root, username)
        if session and session.get("access_token"):
            self.token = session["access_token"]
            self.device_id = session.get("device_id")
            self.user_id = session.get("user_id")
            # Validate the token is still alive
            status, _ = _get(self._url("/_matrix/client/v3/account/whoami"), token=self.token)
            if status == 200:
                return True

        # Fresh login — reuse device_id if we have one from a previous session
        body = {
            "type": "m.login.password",
            "user": username,
            "password": password,
        }
        if self.device_id:
            body["device_id"] = self.device_id

        status, resp = _post(self._url("/_matrix/client/v3/login"), body)
        if status != 200:
            return False

        self.token = resp["access_token"]
        self.device_id = resp["device_id"]
        self.user_id = resp["user_id"]
        _save_session(self.repo_root, username, self.token, self.device_id, self.user_id)
        return True

    def logout(self):
        """End the session. We don't actually call logout — we want the
        token to stay valid for the next CLI invocation."""
        pass

    # ── Rooms ────────────────────────────────────────────────────────────

    def resolve_room(self, alias):
        """Resolve a room alias to a room ID."""
        full = self._full_room(alias)
        encoded = urllib.request.quote(full)
        status, resp = _get(
            self._url(f"/_matrix/client/v3/directory/room/{encoded}"),
            token=self.token,
        )
        if status == 200:
            return resp["room_id"]
        return None

    def create_room(self, alias, name=None, topic=None, space=False):
        """Create a room and optionally make it a Space.

        Returns the room_id on success, None on failure. If the room
        alias already exists, returns None (idempotent callers should
        resolve first).
        """
        body = {
            "name": name or alias,
            "room_alias_name": alias,
            "preset": "private_chat",
            "initial_state": [{
                "type": "m.room.history_visibility",
                "content": {"history_visibility": "shared"},
            }],
        }
        if topic:
            body["topic"] = topic
        if space:
            body["creation_content"] = {"type": "m.space"}
        status, resp = _post(
            self._url("/_matrix/client/v3/createRoom"), body, token=self.token,
        )
        if status == 200:
            return resp["room_id"]
        # Room alias already taken — resolve it instead of failing
        if status == 400 and "in use" in resp.get("error", "").lower():
            return self.resolve_room(alias)
        return None

    def add_space_child(self, space_id, room_id):
        """Add a room as a child of a Space."""
        _put(
            self._url(f"/_matrix/client/v3/rooms/{space_id}/state/m.space.child/{room_id}"),
            {"via": [self.server_name], "suggested": True},
            token=self.token,
        )

    def join_user(self, room_id, user_id):
        """Force-join a user to a room via the Synapse admin API."""
        full = self._full_user(user_id)
        _post(
            self._url(f"/_synapse/admin/v1/join/{room_id}"),
            {"user_id": full},
            token=self.token,
        )

    # ── Users ────────────────────────────────────────────────────────────

    def create_user(self, username, password, displayname=None, admin=False):
        """Create a user via the Synapse admin API.

        Returns True if the user was created or already exists. The admin
        API uses PUT and is idempotent — calling it on an existing user
        updates their profile rather than failing.
        """
        full = self._full_user(username)
        body = {
            "password": password,
            "displayname": displayname or username,
            "admin": admin,
        }
        status, resp = _put(
            self._url(f"/_synapse/admin/v2/users/{full}"), body, token=self.token,
        )
        return status in (200, 201)

    def list_users(self):
        """List all non-deactivated users."""
        status, resp = _get(
            self._url("/_synapse/admin/v2/users?deactivated=false"),
            token=self.token,
        )
        if status == 200:
            return resp.get("users", [])
        return []

    # ── Messaging ────────────────────────────────────────────────────────

    def send(self, room, message, html=None):
        """Send a text message to a room (by alias or ID).

        Resolves aliases automatically. If html is provided, sends a
        formatted message with plain text fallback. Returns (ok, detail).
        """
        if room.startswith("!"):
            room_id = room
        else:
            room_id = self.resolve_room(room)
            if not room_id:
                return False, f"Room '{room}' not found"

        txn = f"{int(time.time() * 1000)}_{random.randint(0, 0xFFFFFFFF):08x}"
        body = {"msgtype": "m.text", "body": message}
        if html:
            body["format"] = "org.matrix.custom.html"
            body["formatted_body"] = html
        status, resp = _put(
            self._url(f"/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn}"),
            body,
            token=self.token,
        )
        if status == 200:
            return True, resp.get("event_id", "sent")
        return False, resp.get("error", "unknown error")
