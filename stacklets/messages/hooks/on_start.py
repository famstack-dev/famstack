"""Keep the config files the installer writes once in step with the stack.

`homeserver.yaml` and `element-config.json` are generated at install and
never again, so a change made later reaches neither. This hook runs on
every start and brings these up to date.

Rate limits. Synapse's defaults are sized for a public homeserver with
strangers on it. On a family server the ones that bite are the
per-account limits: an account may receive five invites in a burst and
then one every 333 seconds, so a bot that sets up a sixth room for the
same person waits five and a half minutes before the invite goes
through. Whichever of the relaxed keys the file lacks is added. A key
already present is the admin's and is left exactly as it is.

Finding people. Synapse's user search lists only accounts that share a
room with the one searching, so a child found neither a parent it had
no room with yet nor the agent, which families talk to in a direct
chat. Everyone on a family server is family or one of its bots, so the
search covers every account on it. Added the same way as the limits.

Element's homeserver. Element finds Synapse through `base_url`, which is
the URL the stack hands out for Synapse and changes with it: a move to
domain mode, or HTTPS turned on. An Element served over https that still
calls an http homeserver is blocked by the browser. `base_url` follows
the rendered `SYNAPSE_PUBLIC_URL`; the rest of the file stays the
admin's.
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

# Everyone on the server may find everyone by name, the agent included.
USER_DIRECTORY = {"user_directory": {"search_all_users": True}}


def run(ctx):
    synapse_dir = Path(ctx.stack.data) / "messages" / "synapse"
    notes = [note for note in (
        _add_family_settings(synapse_dir / "homeserver.yaml"),
        _follow_homeserver_url(synapse_dir / "element-config.json",
                               ctx.env.get("SYNAPSE_PUBLIC_URL", "")),
    ) if note]
    return {"ok": True, **({"message": "; ".join(notes)} if notes else {})}


def _add_family_settings(conf: Path) -> str:
    if not conf.exists():
        return ""

    # The installer writes JSON, which Synapse reads as YAML. A file an
    # admin rewrote as YAML cannot be parsed without a YAML library, and
    # it is theirs to maintain, so it is left as it is.
    try:
        config = json.loads(conf.read_text())
    except ValueError:
        return f"{conf} is not JSON; rate limits and user search left as they are"

    missing = {k: v for k, v in {**RATE_LIMITS, **USER_DIRECTORY}.items() if k not in config}
    if not missing:
        return ""

    conf.write_text(json.dumps({**config, **missing}, indent=2))
    return f"added Synapse settings: {', '.join(missing)}"


def _follow_homeserver_url(conf: Path, synapse_url: str) -> str:
    if not synapse_url or not conf.exists():
        return ""
    try:
        config = json.loads(conf.read_text())
    except ValueError:
        return f"{conf} is not JSON; Element's homeserver left as it is"

    homeserver = config.setdefault("default_server_config", {}).setdefault("m.homeserver", {})
    if homeserver.get("base_url") == synapse_url:
        return ""
    homeserver["base_url"] = synapse_url
    conf.write_text(json.dumps(config, indent=2))
    return f"Element now connects to {synapse_url}"
