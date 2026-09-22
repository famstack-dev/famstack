"""Reachability probe for an OpenAI-compatible endpoint.

Stdlib only (urllib) so the host CLI can call it during setup without
pulling the OpenAI SDK. Hits ``{base_url}/models`` and reports whether the
endpoint is up, whether it needs auth, and which model ids it lists.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

# LAN endpoints often serve self-signed TLS; the AI endpoint is trusted by
# configuration (the user pointed us at it), so skip verification here.
_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE


@dataclasses.dataclass
class ProbeResult:
    reachable: bool
    needs_auth: bool = False
    models: list = dataclasses.field(default_factory=list)


def probe(url: str, key: str = "", *, timeout: float = 3.0) -> ProbeResult:
    """Hit ``{url}/models`` and report what we find.

    Never raises — an unreachable endpoint is a normal, expected state
    during setup (AI not up yet), not an error the caller must handle.
    """
    models_url = f"{url.rstrip('/')}/models"
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(models_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as resp:
            data = json.loads(resp.read().decode())
            model_ids = [m.get("id", "") for m in data.get("data", [])]
            return ProbeResult(reachable=True, models=model_ids)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return ProbeResult(reachable=False, needs_auth=True)
        return ProbeResult(reachable=False)
    except Exception:
        return ProbeResult(reachable=False)


def transcribes(url: str, key: str = "", *, timeout: float = 3.0) -> bool:
    """Whether ``{url}/audio/transcriptions`` exists.

    Asked with an empty POST, so nothing is uploaded and nothing is
    transcribed. A server with the endpoint rejects the request for the
    missing file (400, 415 or 422); one without it answers 404 or 405.
    """
    req = urllib.request.Request(
        f"{url.rstrip('/')}/audio/transcriptions", data=b"", method="POST",
        headers={"Authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL):
            return True
    except urllib.error.HTTPError as e:
        return e.code in (400, 415, 422)
    except Exception:
        return False


# Tailscale hands out addresses from the carrier-grade NAT range, which
# Python does not count as private.
_TAILNET = ipaddress.ip_network("100.64.0.0/10")


def stays_home(url: str) -> bool:
    """Whether every address the host of ``url`` resolves to is on the
    home network: loopback, private, link-local or a Tailscale tailnet.

    Anything else is a server run by someone else, and what the stack
    sends it (document text, notes, voice) leaves the house. A name
    that does not resolve is not assumed to be at home.
    """
    host = urllib.parse.urlsplit(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not (ip.is_loopback or ip.is_private or ip.is_link_local
                or ip in _TAILNET):
            return False
    return bool(infos)
