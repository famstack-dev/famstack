"""A JSON HTTP client: a thin wrapper around `urllib.request`.

One client per service, holding its address and token; one request per
call, closed when the answer is read:

    from stack.http_json import JsonClient, Unreachable

    memory = JsonClient("http://stack-memory-curator:8090", token=token)
    answer = memory.post("/commits", {"kind": "chore(lists)", ...})
    if answer.ok:
        answer.body["commit"]
    else:
        answer.detail      # what the service said went wrong, in its words

An HTTP error status is an answer, not an exception: what a 409 means is the
caller's to decide. `Unreachable` is raised only when there is no answer at
all: nothing listens at the address, the name does not resolve, or the
service took longer than the client's timeout.

Problem documents (RFC 7807, `{"status", "detail"}`) are read for `detail`;
a body that is not JSON comes back as `{"detail": <its text>}`.

Standard library only, because the host CLI uses it too.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# Long enough for a write that commits and pushes to Forgejo; a service
# that takes longer is treated as not answering.
TIMEOUT_S = 30.0

# How much of a body that is not JSON is kept for a person to read.
_TEXT_KEPT = 300


@dataclass(frozen=True)
class Answer:
    """A service's answer: its status, its JSON body, and its headers by
    lower-case name (a version's `etag`)."""

    status: int
    body: dict = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def detail(self) -> str:
        """What the service said about the request, for a person."""
        said = self.body.get("detail") or self.body.get("title") or self.body.get("error")
        return str(said) if said else f"the service answered {self.status}"


class Unreachable(Exception):
    """No answer at all from `url`; `reason` says what happened instead."""

    def __init__(self, url: str, reason: object):
        self.url = url
        self.reason = str(reason)
        super().__init__(f"{url} does not answer ({self.reason})")


class JsonClient:
    """JSON requests to the service at `base_url`, with its bearer `token`."""

    def __init__(self, base_url: str, *, token: str = "", timeout: float = TIMEOUT_S):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def get(self, path: str, *, headers: dict[str, str] | None = None) -> Answer:
        return self.request("GET", path, headers=headers)

    def post(self, path: str, body: object, *, headers: dict[str, str] | None = None) -> Answer:
        return self.request("POST", path, body, headers=headers)

    def patch(self, path: str, body: object, *, headers: dict[str, str] | None = None) -> Answer:
        return self.request("PATCH", path, body, headers=headers)

    def request(self, method: str, path: str, body: object = None, *,
                headers: dict[str, str] | None = None) -> Answer:
        """Send `body` as JSON to `path` and return the service's answer;
        `Unreachable` when none comes within the client's timeout."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        sent = {"Accept": "application/json",
                **({"Content-Type": "application/json"} if data is not None else {}),
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
                **(headers or {})}
        req = urllib.request.Request(url, data=data, method=method, headers=sent)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return Answer(response.status, _json(response.read()), _headers(response.headers))
        except urllib.error.HTTPError as refused:
            with refused:
                return Answer(refused.code, _json(refused.read()), _headers(refused.headers))
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as e:
            raise Unreachable(url, getattr(e, "reason", e)) from e


def _headers(sent) -> dict[str, str]:
    return {name.lower(): value for name, value in sent.items()}


def _json(raw: bytes) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {"detail": raw.decode("utf-8", errors="replace").strip()[:_TEXT_KEPT]}
    return value if isinstance(value, dict) else {"value": value}
