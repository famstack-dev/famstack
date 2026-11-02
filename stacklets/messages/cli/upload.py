"""
stack messages upload <room> <path> [--as <user>] [--caption <text>] — post a file to a room

Uploads a local file (PDF, image, anything) to a Matrix room as a file or
image message. This is the file counterpart to `stack messages send`, and
the building block for ingesting documents the way a family member would:
the archivist watches the documents room and files whatever lands there.

By default the file is posted as stacker-bot (the system account). Pass
`--as <user>` to post as a family member instead, e.g.

    stack messages upload documents payslip.pdf --as homer

`--caption <text>` posts the file with a caption, as a phone does when
the sender types something under the picture.

The user's password is read from the secrets store (the same place the
account was created), so the sender shows up correctly in the timeline and
the archivist's "received from <name>" reply. Images (.png/.jpg) are sent
as m.image, audio (.ogg/.opus/.m4a/.mp3/.wav) as m.audio so it is treated
as a voice message and transcribed; everything else as m.file.

An unencrypted room gets the file over plain HTTP, from the host. In an
encrypted room the file must be encrypted, which the host cannot do: it
goes, with the message, to the sender's command-line device in the bot
runner (core must be up), as `stack messages send` does. That device
encrypts it, uploads only the ciphertext and posts it.
"""

HELP = "Upload a file to a chat room"

import sys
from pathlib import Path

from ._matrix import MatrixClient, encryption, file_content, resolve_login, send_encrypted

# Extension → (mime type, Matrix msgtype). Images go as m.image so the
# archivist takes its vision path. Audio goes as m.audio, which is what a
# phone's mic button sends, so the framework transcribes it and the bots
# see the words — the only way to exercise voice from the terminal.
# Everything else is m.file.
_MIME = {
    ".pdf": ("application/pdf", "m.file"),
    ".png": ("image/png", "m.image"),
    ".jpg": ("image/jpeg", "m.image"),
    ".jpeg": ("image/jpeg", "m.image"),
    ".webp": ("image/webp", "m.image"),
    ".gif": ("image/gif", "m.image"),
    ".txt": ("text/plain", "m.file"),
    ".ogg": ("audio/ogg", "m.audio"),
    ".opus": ("audio/ogg", "m.audio"),
    ".m4a": ("audio/mp4", "m.audio"),
    ".mp3": ("audio/mpeg", "m.audio"),
    ".wav": ("audio/wav", "m.audio"),
}


_USAGE = "Usage: stack messages upload <room> <path> [--as <user>] [--caption <text>]"


def _parse_argv(argv):
    """Pull (room, path, sender, caption) out of argv, then an error or None."""
    options = {"--as": None, "--caption": ""}
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] in options:
            if i + 1 >= len(argv):
                return None, None, None, "", f"{argv[i]} needs a value"
            options[argv[i]] = argv[i + 1]
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    if len(rest) < 2:
        return None, None, None, "", _USAGE
    return rest[0], rest[1], options["--as"], options["--caption"], None


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Messages is not running — start it with 'stack up messages'"}

    # sys.argv: ['stack', 'messages', 'upload', <room>, <path>, ...]
    room, path_str, sender, caption, err = _parse_argv(sys.argv[3:])
    if err or room is None or path_str is None:
        return {"error": err or _USAGE}

    path = Path(path_str)
    if not path.is_file():
        return {"error": f"File not found: {path}"}

    mime, msgtype = _MIME.get(path.suffix.lower(), ("application/octet-stream", "m.file"))

    instance_dir = config.get("instance_dir", config.get("repo_root", "."))
    stack_cfg = config.get("stack", {})
    secrets = config.get("secrets", {})
    server_name = stack_cfg.get("messages", {}).get("server_name", "home")

    account, err = resolve_login(sender, secrets, stack_cfg)
    if account is None:
        return {"error": err}

    manifest = config.get("manifest", {})
    synapse_port = manifest.get("ports", {}).get("synapse", 42031)
    base_url = f"http://localhost:{synapse_port}"

    client = MatrixClient(base_url, server_name, instance_dir)
    if not client.login(account.login, account.password):
        return {"error": f"{account.login} can't log in: check the password in secrets."}

    # Best-effort join so a family member who isn't yet in the room can
    # still post. Harmless if already a member.
    client.join(room)

    # The room is settled before any byte leaves the host: a file meant
    # for an encrypted room must never reach the media repository in the clear.
    room_id = room if room.startswith("!") else client.resolve_room(room)
    if not room_id:
        return {"error": f"Room '{room}' not found"}
    encrypted, err = encryption(client, room_id, f"room '{room}'")
    if err:
        return {"error": err}

    data = path.read_bytes()
    if encrypted:
        content = file_content(None, path.name, mime, msgtype=msgtype, size=len(data),
                               caption=caption)
        ok, detail = send_encrypted(account, client.user_id, room_id, content,
                                    attachment=data)
        return _answer(ok, detail, room, account, path)

    mxc = client.upload_media(data, mime, path.name)
    if not mxc:
        return {"error": f"Media upload failed for {path.name}"}

    ok, detail = client.send_file(
        room_id, mxc, path.name, mime, msgtype=msgtype, size=len(data),
        caption=caption,
    )
    return _answer(ok, detail, room, account, path)


def _answer(ok, detail, room, account, path):
    """The command's result for a post that returned `(ok, event id or error)`."""
    if ok:
        return {"ok": True, "room": room, "as": account.login,
                "file": path.name, "event_id": detail}
    return {"error": f"Failed to post file: {detail}"}
