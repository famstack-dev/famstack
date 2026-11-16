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

Two answers to `up` are not the command's own result:

- A stacklet whose manifest sets `attended_setup` and that is not set up
  yet is not started. Its first run needs a person at the Mac (macOS asks
  for permissions, the setup asks questions), so the bot names the command
  to run in the terminal. It reads that from the stacklets as `stack list`
  reports them, so the bot keeps no list of stacklet ids.
- A start that another run of the stacklet refuses (the run lock,
  `stack.runs`) is reported as busy, with where that run comes from and
  since when. It is not a failure: nothing was changed.

The same busy reply comes for `up`, `down` and `restart` while a run of
any other stacklet goes on. Runs go one at a time; the host API runs the
room's command so that it does not wait its turn, because one answer must
come within the API's limit (`stack.runs`, `STACK_RUN_QUEUE`).
"""

import json
import os

from loguru import logger
from nio import AsyncClient, RoomMessageText

from stack.bot import MicroBot
from stack.live import client as host_api

# Longer than the host API's own limit on a command (300 s), so a slow
# `stack up` ends with the API's answer rather than the bot giving up first.
API_TIMEOUT_SECS = 330

COMMANDS = {
    "status": {"cmd": "status"},
    "config": {"cmd": "config"},
}
STACKLET_COMMANDS = {"up", "down", "restart"}

# Where the room's runs come from, as another run's refusal names them.
SURFACE = "server room"
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
    """The host API's answer to `request`, or `{"error": ...}` saying why
    there is none. Blocks until the command ends."""
    return host_api.call(request, timeout=API_TIMEOUT_SECS)


def _needs_the_mac(sid):
    """Whether the first `stack up` of `sid` needs a person at the Mac:
    its manifest sets `attended_setup` and it is not set up yet. An id
    `stack list` does not know is left to `up`, which names the error."""
    for row in _call_api({"cmd": "list"}).get("stacklets", []):
        if row.get("id") == sid:
            return bool(row.get("attended_setup")) and not row.get("enabled")
    return False


def _attended_setup_reply(sid, words):
    """The answer to `stack up <sid>` for a setup that needs the Mac."""
    return words["attended_setup"].format(sid=sid)


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
    text = "\n".join(ln for ln in lines if not ln.startswith("> ")).strip()

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
        return {"cmd": cmd, "stacklet": parts[1], "surface": SURFACE}

    return None


# ── The bot's words ───────────────────────────────────────────────────────

# Everything the bot says on its own, by the stack's language (`LANGUAGE`,
# from `[core] language`); English for any other language. An error the
# host API returns is the command's own text and is quoted as it came.
_WORDS = {
    "en": {
        "help": (
            "**Available commands**\n\n"
            "- `stack status`: see what's running\n"
            "- `stack up <name>`: start a service\n"
            "- `stack down <name>`: stop a service\n"
            "- `stack restart <name>`: restart a service\n"
            "- `stack config`: show the current configuration\n"
            "- `stack help`: this message\n\n"
            "**Services:** photos, docs, ai, chatai, bots"),
        "attended_setup": ("**{sid}** needs its first setup at the Mac: it asks questions, "
                           "and macOS asks for permissions along the way.\n"
                           "Run `stack up {sid}` in the terminal."),
        "admins_only": "Only admins can run that command. Try `status` or `list`.",
        "starting": "▶️ Starting **{sid}**...",
        "stopping": "⏸️ Stopping **{sid}**...",
        "restarting": "🔄 Restarting **{sid}**...",
        "done": "Done.",
        "available": "Available: ",
        "disk": "Disk {pct}% · {free} GB free",
        "no_config": "No config available.",
        "busy": "The stacklet is busy",
    },
    "de": {
        "help": (
            "**Befehle**\n\n"
            "- `stack status`: was gerade läuft\n"
            "- `stack up <Name>`: einen Dienst starten\n"
            "- `stack down <Name>`: einen Dienst stoppen\n"
            "- `stack restart <Name>`: einen Dienst neu starten\n"
            "- `stack config`: die aktuelle Konfiguration zeigen\n"
            "- `stack help`: diese Hilfe\n\n"
            "**Dienste:** photos, docs, ai, chatai, bots"),
        "attended_setup": ("**{sid}** wird beim ersten Mal am Mac eingerichtet: Die Einrichtung "
                           "stellt Fragen, und macOS fragt nach Berechtigungen.\n"
                           "Gib im Terminal `stack up {sid}` ein."),
        "admins_only": "Diesen Befehl dürfen nur Admins ausführen. Probier `status` oder `list`.",
        "starting": "▶️ **{sid}** startet...",
        "stopping": "⏸️ **{sid}** wird gestoppt...",
        "restarting": "🔄 **{sid}** startet neu...",
        "done": "Fertig.",
        "available": "Verfügbar: ",
        "disk": "Festplatte {pct}% · {free} GB frei",
        "no_config": "Keine Konfiguration vorhanden.",
        "busy": "Für diesen Dienst läuft gerade schon ein anderer Befehl",
    },
}


def _words():
    """The bot's texts in the stack's language (`_WORDS`)."""
    return _WORDS.get(os.environ.get("LANGUAGE", "en"), _WORDS["en"])


def _format_status(result, words):
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
        lines.append(words["available"] + ", ".join(f"`stack up {s}`" for s in available))

    host = result.get("host", {})
    if host.get("disk_pct") or host.get("mem_used"):
        lines.append("")
    if host.get("disk_pct"):
        pct = host["disk_pct"]
        icon = "💾" if pct < 80 else "⚠️" if pct < 90 else "🚨"
        lines.append(f"{icon} " + words["disk"].format(pct=pct, free=host.get("disk_free", "?")))
    if host.get("mem_used"):
        lines.append(f"🧠 RAM {host['mem_used']} / {host.get('mem_total', '?')} GB")

    return "\n".join(lines)


def _format_config(result, words):
    """Format config output as a code block."""
    if result.get("error"):
        return f"❌ {result['error']}"

    output = result.get("output", "").strip()
    if output:
        return f"```\n{output}\n```"
    return words["no_config"]


def _format_result(cmd, result, words):
    """Format an API response for chat, in `words`' language."""
    if cmd == "status":
        return _format_status(result, words)
    if cmd == "config":
        return _format_config(result, words)

    # Mutating commands
    if "running" in result:
        return _format_busy(result, words)
    if result.get("ok"):
        icon = {"up": "✅", "down": "⏸️", "restart": "🔄"}.get(cmd, "✅")
        return f"{icon} {words['done']}"
    if result.get("error"):
        return f"❌\n```\n{result['error']}\n```"
    return f"```\n{json.dumps(result, indent=2)}\n```"


def _format_busy(result, words):
    """A start refused by another run of the stacklet (`stack.runs`):
    the refusal names where that run comes from and since when."""
    hints = result.get("hints") or []
    wait = f" {hints[0]}" if hints else ""
    return f"⏳ {result.get('error', words['busy'])}.{wait}"


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
        words = _words()

        # Permission check — mutating commands require admin
        if ADMIN_USERS and cmd not in READ_ONLY_COMMANDS:
            if event.sender not in ADMIN_USERS:
                await self._send_reply(room.room_id, event, words["admins_only"])
                return

        logger.info("[stacker] Command from {}: {}", event.sender, request)

        if cmd == "help":
            await self._send_reply(room.room_id, event, words["help"])
            return

        if cmd == "up" and sid and _needs_the_mac(sid):
            await self._send_reply(room.room_id, event, _attended_setup_reply(sid, words))
            return

        # Acknowledge slow commands before executing
        if cmd == "up" and sid:
            await self._send_reply(room.room_id, event, words["starting"].format(sid=sid))
        elif cmd == "down" and sid:
            await self._send_reply(room.room_id, event, words["stopping"].format(sid=sid))
        elif cmd == "restart" and sid:
            await self._send_reply(room.room_id, event, words["restarting"].format(sid=sid))

        # Mutating commands can recreate this very container (`stack up`
        # ends in a core refresh that force-recreates the bot-runner), so
        # the handler may never return. Ack the event BEFORE executing:
        # a mid-command restart then never replays the command. Commands
        # are at-most-once; captures elsewhere stay at-least-once.
        if cmd in STACKLET_COMMANDS:
            await self.ack_event(room.room_id, event)

        # Execute
        result = _call_api(request)
        response = _format_result(cmd, result, words)

        await self._send_reply(room.room_id, event, response)

    async def _send_reply(self, room_id, event, text):
        """Reply to `event` with `text` via the framework's formatted send.

        Thin convenience over `MicroBot._send` — derives the reply
        relation from the triggering event. Markdown→HTML, the reply
        relation, and the single `_room_send` seam all live in the
        framework now; this no longer hand-rolls HTML.
        """
        await self._send(room_id, text, reply_to=event.event_id)
