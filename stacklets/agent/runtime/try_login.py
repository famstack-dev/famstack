"""One login attempt as the agent's chat account, leaving no device behind.

Core's bot runner creates the account, and core refreshes at the end of
`./stack up`, after this container has started. nanobot tries its password once
and never again, so entrypoint.sh repeats this attempt and starts nanobot only
after one works.

A successful login creates a device on the server, and every device of the
account is sent the room keys of encrypted rooms. So the attempt logs out again:
nanobot's own login, right after, makes the one device the account keeps.

Run by entrypoint.sh, not a shim: try_login.py
Exit code 0: the account accepted the login. 1: not yet, for any reason (the
account does not exist, the password is refused, the server does not answer).
The server gives the same M_FORBIDDEN for a missing account and a wrong
password, so the two cannot be told apart here.

Reads AGENT_MATRIX_HOMESERVER, AGENT_BOT_ID, AGENT_MATRIX_SERVER_NAME and
AGENT_MATRIX_PASSWORD from the environment. The password stays out of the
arguments and the output: a process list and the container's log show both.
Standard library only, because nanobot exposes no login to reuse from here.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

REQUEST_TIMEOUT_SECONDS = 10


def post(url: str, body: dict, token: str | None = None) -> dict | None:
    """POST JSON and return the parsed answer, or None for any refusal or failure."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def main() -> int:
    """Log in and straight out again. Returns the exit code described above."""
    client = os.environ["AGENT_MATRIX_HOMESERVER"].rstrip("/") + "/_matrix/client/v3"
    user_id = f"@{os.environ['AGENT_BOT_ID']}:{os.environ['AGENT_MATRIX_SERVER_NAME']}"
    login = post(f"{client}/login", {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": user_id},
        "password": os.environ.get("AGENT_MATRIX_PASSWORD", ""),
        "initial_device_display_name": "agent login check",
    })
    if not login or "access_token" not in login:
        return 1
    # The login worked, which is the answer. A failed logout leaves one unused
    # device and is no reason to hold nanobot back.
    post(f"{client}/logout", {}, token=login["access_token"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
