"""Keep the rate limits famstack relaxes in Synapse's config.

Synapse's defaults are sized for a public homeserver with strangers on
it. On a family server the ones that bite are the per-account limits:
an account may receive five invites in a burst and then one every 333
seconds, so a bot that sets up a sixth room for the same person waits
five and a half minutes before the invite goes through.

`homeserver.yaml` is generated once, at install, and never again, so a
limit added to the installer would reach new servers only. This hook
runs on every start and adds whichever of these keys the file lacks.
A key already present is the admin's and is left exactly as it is.
"""

from __future__ import annotations

import json
from pathlib import Path

RATE_LIMITS = {
    "rc_login": {
        "address": {"per_second": 1, "burst_count": 20},
        "account": {"per_second": 1, "burst_count": 20},
        "failed_attempts": {"per_second": 0.5, "burst_count": 20},
    },
    "rc_message": {"per_second": 5, "burst_count": 30},
    "rc_admin_redaction": {"per_second": 5, "burst_count": 30},
    # per_room keeps Synapse's default; it guards one room, not one person.
    "rc_invites": {
        "per_user": {"per_second": 1, "burst_count": 20},
        "per_issuer": {"per_second": 1, "burst_count": 20},
    },
}


def run(ctx):
    conf = Path(ctx.stack.data) / "messages" / "synapse" / "homeserver.yaml"
    if not conf.exists():
        return {"ok": True}

    # The installer writes JSON, which Synapse reads as YAML. A file an
    # admin rewrote as YAML cannot be parsed without a YAML library, and
    # it is theirs to maintain, so it is left as it is.
    try:
        config = json.loads(conf.read_text())
    except ValueError:
        return {"ok": True, "message": f"{conf} is not JSON; rate limits left as they are"}

    missing = {k: v for k, v in RATE_LIMITS.items() if k not in config}
    if not missing:
        return {"ok": True}

    conf.write_text(json.dumps({**config, **missing}, indent=2))
    return {"ok": True, "message": f"added Synapse rate limits: {', '.join(missing)}"}
