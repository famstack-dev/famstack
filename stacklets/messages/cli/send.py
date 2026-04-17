"""
stack messages send <room> "message" — send a message to a Matrix room

Sends a plain text message to the specified room as the admin user.
The room can be a bare alias (e.g. 'chat') or a full Matrix room ID.

This is the building block other stacklets use for notifications:
  - photos could notify #notifications when a backup completes
  - docs could notify when a new document is archived
  - core could notify on update events

The session is persisted in .famstack/messages-session.toml so repeated
sends don't create new devices or require re-authentication.
"""

HELP = "Send a message to a chat room"

import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from _matrix import MatrixClient


def _simple_markdown_to_html(text):
    """Convert a small subset of markdown to HTML. No dependencies.

    Supports: **bold**, `code`, - list items, blank line paragraphs.
    Returns None if the text has no formatting (send as plain text).
    """
    import re
    if not any(c in text for c in ("**", "- ", "`", "\n")):
        return None

    lines = text.split("\n")
    html_lines = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        # List item
        if stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            item = stripped[2:]
            item = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", item)
            item = re.sub(r"`(.+?)`", r"<code>\1</code>", item)
            html_lines.append(f"<li>{item}</li>")
            continue

        # Close list if we were in one
        if in_list:
            html_lines.append("</ul>")
            in_list = False

        # Blank line
        if not stripped:
            continue

        # Regular line — apply inline formatting
        line_html = stripped
        line_html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line_html)
        line_html = re.sub(r"`(.+?)`", r"<code>\1</code>", line_html)
        html_lines.append(f"<p>{line_html}</p>")

    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def run(args, stacklet, config):
    """Called by the main stack CLI: 'stack messages send <room> "message"'.

    The room and message come from positional args that argparse doesn't
    know about — we pull them from sys.argv directly since the stacklet
    CLI plugin system passes the full args namespace.
    """
    if not config["is_healthy"]():
        return {"error": "Messages is not running — start it with 'stack up messages'"}

    # Parse room and message from the remaining argv
    # sys.argv looks like: ['stack', 'messages', 'send', '<room>', '<message>']
    argv = sys.argv[3:]  # skip 'stack', 'messages', 'send'
    if len(argv) < 2:
        return {"error": "Usage: stack messages send <room> \"message\""}

    room = argv[0]
    message = " ".join(argv[1:])

    instance_dir = config.get("instance_dir", config.get("repo_root", "."))
    stack_cfg = config.get("stack", {})
    secrets = config.get("secrets", {})
    server_name = stack_cfg.get("messages", {}).get("server_name", "home")

    # Use stacker-bot for sending — it's the system notification account
    # Password lives in core (new convention) or messages (legacy)
    bot_pass = (secrets.get("core__STACKER_BOT_PASSWORD")
                or secrets.get("messages__STACKER_BOT_PASSWORD", ""))
    if not bot_pass:
        return {"error": "stacker-bot not set up. Run 'stack up core' first."}

    manifest = config.get("manifest", {})
    synapse_port = manifest.get("ports", {}).get("synapse", 42031)
    base_url = f"http://localhost:{synapse_port}"
    client = MatrixClient(base_url, server_name, instance_dir)

    if not client.login("stacker-bot", bot_pass):
        return {"error": "stacker-bot can't log in. Run 'stack messages setup' first."}

    html = _simple_markdown_to_html(message)

    ok, detail = client.send(room, message, html=html)
    if ok:
        return {"ok": True, "room": room, "event_id": detail}
    else:
        return {"error": f"Failed to send: {detail}"}
