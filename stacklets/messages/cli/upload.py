"""
stack messages upload <room> <path> [--as <user>] — post a file to a room

Uploads a local file (PDF, image, anything) to a Matrix room as a file or
image message. This is the file counterpart to `stack messages send`, and
the building block for ingesting documents the way a family member would:
the archivist watches the documents room and files whatever lands there.

By default the file is posted as stacker-bot (the system account). Pass
`--as <user>` to post as a family member instead, e.g.

    stack messages upload documents payslip.pdf --as homer

The user's password is read from the secrets store (the same place the
account was created), so the sender shows up correctly in the timeline and
the archivist's "received from <name>" reply. Images (.png/.jpg) are sent
as m.image; everything else as m.file.
"""

HELP = "Upload a file to a chat room"

import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from _matrix import MatrixClient

# Extension → (mime type, Matrix msgtype). Images go as m.image so the
# archivist takes its vision path; everything else is m.file.
_MIME = {
    ".pdf": ("application/pdf", "m.file"),
    ".png": ("image/png", "m.image"),
    ".jpg": ("image/jpeg", "m.image"),
    ".jpeg": ("image/jpeg", "m.image"),
    ".webp": ("image/webp", "m.image"),
    ".gif": ("image/gif", "m.image"),
    ".txt": ("text/plain", "m.file"),
}


def _parse_argv(argv):
    """Pull (room, path, sender) out of argv. sender is None unless --as."""
    sender = None
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--as":
            if i + 1 >= len(argv):
                return None, None, None, "--as needs a username"
            sender = argv[i + 1]
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    if len(rest) < 2:
        return None, None, None, 'Usage: stack messages upload <room> <path> [--as <user>]'
    return rest[0], rest[1], sender, None


def _resolve_login(sender, secrets):
    """(username, password) for the sender. --as <user> reads
    global__USER_<NAME>_PASSWORD; the default is stacker-bot."""
    if sender:
        key = f"global__USER_{sender.upper()}_PASSWORD"
        password = secrets.get(key, "")
        if not password:
            return None, None, f"No password for '{sender}' in secrets ({key})"
        return sender, password, None
    bot_pass = (secrets.get("core__STACKER_BOT_PASSWORD")
                or secrets.get("messages__STACKER_BOT_PASSWORD", ""))
    if not bot_pass:
        return None, None, "stacker-bot not set up. Run 'stack up core' first."
    return "stacker-bot", bot_pass, None


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Messages is not running — start it with 'stack up messages'"}

    # sys.argv: ['stack', 'messages', 'upload', <room>, <path>, ...]
    room, path_str, sender, err = _parse_argv(sys.argv[3:])
    if err:
        return {"error": err}

    path = Path(path_str)
    if not path.is_file():
        return {"error": f"File not found: {path}"}

    mime, msgtype = _MIME.get(path.suffix.lower(), ("application/octet-stream", "m.file"))

    instance_dir = config.get("instance_dir", config.get("repo_root", "."))
    stack_cfg = config.get("stack", {})
    secrets = config.get("secrets", {})
    server_name = stack_cfg.get("messages", {}).get("server_name", "home")

    username, password, err = _resolve_login(sender, secrets)
    if err:
        return {"error": err}

    manifest = config.get("manifest", {})
    synapse_port = manifest.get("ports", {}).get("synapse", 42031)
    base_url = f"http://localhost:{synapse_port}"

    client = MatrixClient(base_url, server_name, instance_dir)
    if not client.login(username, password):
        return {"error": f"{username} can't log in — check the password in secrets."}

    # Best-effort join so a family member who isn't yet in the room can
    # still post. Harmless if already a member.
    client.join(room)

    data = path.read_bytes()
    mxc = client.upload_media(data, mime, path.name)
    if not mxc:
        return {"error": f"Media upload failed for {path.name}"}

    ok, detail = client.send_file(
        room, mxc, path.name, mime, msgtype=msgtype, size=len(data),
    )
    if ok:
        return {"ok": True, "room": room, "as": username,
                "file": path.name, "event_id": detail}
    return {"error": f"Failed to post file: {detail}"}
