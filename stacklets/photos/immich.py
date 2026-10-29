"""Immich's HTTP API, as the photos commands and hooks on the host call it.

One small client over urllib, so host code stays stdlib-only. A request
answers `(status, parsed JSON)`, an HTTP error included, so the caller
decides what a status means; only a network failure raises. Certificate
checks are off because Immich is reached on localhost or the LAN.

A caller authenticates with a session token (`token`, from a login) or
an API key (`api_key`). A session it opened it ends with `logout`,
because Immich lists every open session as a device of the account.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
import uuid

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

TIMEOUT_SECONDS = 15


def request(method, url, body=None, token=None, api_key=None):
    """Send `body` as JSON and answer (status, parsed JSON answer).

    An empty answer (a 204) reads as `{}`. An error answer that is not
    JSON reads as `{"message": <reason>}`.
    """
    data = json.dumps(body).encode() if body else None
    return _send(method, url, data, "application/json", token, api_key)


def upload(url, field, filename, content, content_type, token=None):
    """POST one file as multipart form data, as Immich's upload routes
    take it, and answer (status, parsed JSON answer)."""
    boundary = uuid.uuid4().hex
    data = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    return _send("POST", url, data, f"multipart/form-data; boundary={boundary}", token, None)


def login(base_url, email, password):
    """Log in and answer (status, session token or None)."""
    status, resp = request("POST", f"{base_url}/api/auth/login",
                           {"email": email, "password": password})
    return status, resp.get("accessToken") if status in (200, 201) else None


def logout(base_url, token):
    """End a session that `login` opened. A failure is ignored: the
    session then expires on its own."""
    if token:
        request("POST", f"{base_url}/api/auth/logout", token=token)


def _send(method, url, data, content_type, token, api_key):
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS, context=_SSL) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except ValueError:
            return e.code, {"message": e.reason}
