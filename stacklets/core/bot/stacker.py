"""Stacker — CLI bridge bot for Server Room.

Listens for commands in Server Room and executes them via the famstack
API socket. Type a stack command and Stacker runs it on the host.

Supported commands:
  status              System overview
  list                All stacklets with state
  up <stacklet>       Start a stacklet
  down <stacklet>     Stop a stacklet

Commands are recognized when they start with "stack" or are bare
command names (status, list, up, down). Everything else is ignored.
"""

import json
import os
import socket as sock

from loguru import logger
from nio import AsyncClient, RoomMessageText

from microbot import MicroBot

API_HOST = os.environ.get("STACK_API_HOST", "host.docker.internal")
API_PORT = int(os.environ.get("STACK_API_PORT", "42001"))

COMMANDS = {
    "status": {"cmd": "status"},
    "config": {"cmd": "config"},
}
STACKLET_COMMANDS = {"up", "down", "restart"}
READ_ONLY_COMMANDS = {"status", "config", "help"}

# Admin users who can run mutating commands (up, down, restart).
# Built from bare user IDs + server name into full Matrix user IDs.
# If empty, all users in the room can run any command.
_server_name = os.environ.get("MATRIX_SERVER_NAME", "home")
ADMIN_USERS = {
    f"@{uid.strip()}:{_server_name}"
    for uid in os.environ.get("STACK_ADMIN_USER_IDS", "").split(",")
    if uid.strip()
}


def _call_api(request):
    """Send a JSON command to the famstack API and return the response."""
    try:
        s = sock.socket(sock.AF_INET, sock.SOCK_STREAM)
        s.connect((API_HOST, API_PORT))
        s.sendall((json.dumps(request) + "\n").encode())

        chunks = []
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        s.close()
        return json.loads(b"".join(chunks))
    except Exception as e:
        return {"error": str(e)}


def _parse_command(text):
    """Parse a message into an API request. Returns None if not a command.

    Requires the 'stack' prefix: stack status, stack up photos, etc.
    Messages without the prefix are ignored so normal chat doesn't
    accidentally trigger commands.
    """
    text = text.strip()

    # Strip Matrix reply fallback — quoted lines from the original message
    # would otherwise re-trigger commands (e.g. "> stack up docs")
    lines = text.split("\n")
    text = "\n".join(l for l in lines if not l.startswith("> ")).strip()

    if not text.lower().startswith("stack "):
        return None

    text = text[6:].strip()
    parts = text.split()
    if not parts:
        return None

    cmd = parts[0].lower()

    if cmd == "help":
        return {"cmd": "help"}

    if cmd in COMMANDS:
        return COMMANDS[cmd].copy()

    if cmd in STACKLET_COMMANDS and len(parts) >= 2:
        return {"cmd": cmd, "stacklet": parts[1]}

    return None


HELP_TEXT = (
    "**Available commands**\n\n"
    "- `stack status` — see what's running\n"
    "- `stack up <name>` — start a service\n"
    "- `stack down <name>` — stop a service\n"
    "- `stack restart <name>` — restart a service\n"
    "- `stack config` — show current configuration\n"
    "- `stack help` — this message\n\n"
    "**Services:** photos, docs, ai, chatai, bots"
)


def _format_status(result):
    """Format a status result as a readable message."""
    if result.get("error"):
        return f"❌ {result['error']}"

    name = result.get("name", "famstack")
    version = result.get("version", "?")
    lines = [f"📊 **{name}** `{version}`", ""]

    online = []
    degraded = []
    stopped = []
    available = []
    for s in result.get("stacklets", []):
        sname = s.get("name", s.get("id", "?"))
        sid = s.get("id", "?")
        if s.get("online"):
            online.append(sname)
        elif s.get("degraded"):
            degraded.append(sname)
        elif s.get("enabled"):
            stopped.append(sname)
        elif not s.get("always_on"):
            available.append(sid)

    if online:
        lines.append("🟢 " + ", ".join(f"**{n}**" for n in online))
    if degraded:
        lines.append("🟡 " + ", ".join(f"**{n}**" for n in degraded))
    if stopped:
        lines.append("🔴 " + ", ".join(f"**{n}**" for n in stopped))
    if available:
        lines.append("")
        lines.append("Available: " + ", ".join(f"`stack up {s}`" for s in available))

    host = result.get("host", {})
    if host.get("disk_pct") or host.get("mem_used"):
        lines.append("")
    if host.get("disk_pct"):
        pct = host["disk_pct"]
        icon = "💾" if pct < 80 else "⚠️" if pct < 90 else "🚨"
        lines.append(f"{icon} Disk {pct}% · {host.get('disk_free', '?')} GB free")
    if host.get("mem_used"):
        lines.append(f"🧠 RAM {host['mem_used']} / {host.get('mem_total', '?')} GB")

    return "\n".join(lines)


def _format_config(result):
    """Format config output as a code block."""
    if result.get("error"):
        return f"❌ {result['error']}"

    output = result.get("output", "").strip()
    if output:
        return f"```\n{output}\n```"
    return "No config available."


def _format_result(cmd, result):
    """Format an API response for chat."""
    if cmd == "status":
        return _format_status(result)
    if cmd == "config":
        return _format_config(result)

    # Mutating commands
    if result.get("ok") or result.get("success"):
        icon = {"up": "✅", "down": "⏸️", "restart": "🔄"}.get(cmd, "✅")
        return f"{icon} Done."
    if result.get("error"):
        return f"❌\n```\n{result['error']}\n```"
    return f"```\n{json.dumps(result, indent=2)}\n```"


class StackerBot(MicroBot):
    name = "stacker-bot"

    def register_callbacks(self, client: AsyncClient) -> None:
        self.add_event_callback(self._on_message, RoomMessageText)

    async def _on_message(self, room, event: RoomMessageText) -> None:
        request = _parse_command(event.body)
        if not request:
            return

        cmd = request.get("cmd", "")
        sid = request.get("stacklet", "")

        # Permission check — mutating commands require admin
        if ADMIN_USERS and cmd not in READ_ONLY_COMMANDS:
            if event.sender not in ADMIN_USERS:
                await self._send_reply(room.room_id, event,
                    "Only admins can run that command. Try `status` or `list`.")
                return

        logger.info("[stacker] Command from {}: {}", event.sender, request)

        if cmd == "help":
            await self._send_reply(room.room_id, event, HELP_TEXT)
            return

        # Interactive stacklets can't be installed headlessly from chat
        TERMINAL_ONLY = {"ai"}
        if cmd == "up" and sid in TERMINAL_ONLY:
            await self._send_reply(room.room_id, event,
                f"**{sid}** needs interactive setup (hardware detection, downloads).\n"
                f"Run `stack up {sid}` in the terminal.")
            return

        # Acknowledge slow commands before executing
        if cmd == "up" and sid:
            await self._send_reply(room.room_id, event, f"▶️ Starting **{sid}**...")
        elif cmd == "down" and sid:
            await self._send_reply(room.room_id, event, f"⏸️ Stopping **{sid}**...")
        elif cmd == "restart" and sid:
            await self._send_reply(room.room_id, event, f"🔄 Restarting **{sid}**...")

        # Execute
        result = _call_api(request)
        response = _format_result(cmd, result)

        await self._send_reply(room.room_id, event, response)

    async def _send_reply(self, room_id, event, text):
        """Send a reply to a message."""
        import re
        # Simple markdown to HTML
        html = text
        # Code blocks first (before line break conversion)
        html = re.sub(r"```\n?(.*?)\n?```", r"<pre><code>\1</code></pre>", html, flags=re.DOTALL)
        html = re.sub(r"`(.+?)`", r"<code>\1</code>", html)
        html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html)
        # Only convert line breaks outside <pre> blocks
        parts = re.split(r"(<pre>.*?</pre>)", html, flags=re.DOTALL)
        html = "".join(p if p.startswith("<pre>") else p.replace("\n", "<br/>") for p in parts)

        content = {
            "msgtype": "m.text",
            "body": text,
            "format": "org.matrix.custom.html",
            "formatted_body": html,
            "m.relates_to": {
                "m.in_reply_to": {"event_id": event.event_id},
            },
        }
        await self._client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content=content,
        )
