"""Reachability probe for an OpenAI-compatible endpoint.

Stdlib only (urllib) so the host CLI can call it during setup without
pulling the OpenAI SDK. Hits ``{base_url}/models`` and reports whether the
endpoint is up, whether it needs auth, and which model ids it lists.
"""

from __future__ import annotations

import dataclasses
import json
import ssl
import urllib.error
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
