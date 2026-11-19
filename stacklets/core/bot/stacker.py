"""Stacker — CLI bridge bot for Server Room.

Listens for commands in Server Room and executes them via the famstack
API socket. Type a stack command and Stacker runs it on the host.

Supported commands:
  status              System overview
  list                All stacklets with state
  up <stacklet>       Start a stacklet
  down <stacklet>     Stop a stacklet

  devices allow <code> [--as <person> | --shared <name>]
                      Let a browser that waits to join in
  devices dismiss <code>
                      End its request, as "Not now" on the Devices page

Commands are recognized when they start with "stack". A reply `allow`
(`allow as marge`, `allow shared Living room`) to the stack's own notice
of a waiting browser is the same `devices allow`, with the notice's code;
a reply `dismiss` is `devices dismiss`.
Everything else is ignored.

Allowing a waiting browser (ADR-023 D20). An admin without a paired
device has only the chat: the notice of a waiting browser reaches them
here, and the allow page needs a paired device. So the room runs `stack
devices allow` through the host API, which asks core's tools server, the
one owner of waiting browsers. Three rules keep a stranger's browser out:

- Only an admin allows or dismisses, also when no admins are configured, unlike the
  other commands: a member of the room who is no admin is refused.
- Without `--as` or `--shared` the browser becomes the device of the
  admin who wrote, never of the family's first admin.
- A reply takes its code only from the famstack envelope of a waiting
  notice that this bot's own account sent (`MicroBot._reply_parent_envelope`),
  never from a message's text, so a reply to anything else allows nothing.
  A reaction allows nothing either: one tap without words is the
  push-to-approve pattern ADR-023 avoids.

Two answers to `up` are not the command's own result:

- A stacklet whose manifest sets `attended_setup` and that is not set up
  yet is not started. Its first run needs a person at the Mac (macOS asks
  for permissions, the setup asks questions), so the bot names the command
  to run in the terminal.
- A stacklet that prepares (`on_prepare`, as the AI downloads its models)
  and is not set up yet is not started either. Its first preparation takes
  far longer than the bot waits for one answer, and the room's run does
  not wait its turn, so it would hold the stack's one run all that time.
  The bot names the setup page, which follows the run as a job, and the
  terminal command.

The bot reads both from the stacklets as `stack list` reports them, so it
keeps no list of stacklet ids.
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
import shlex

from loguru import logger
from nio import AsyncClient, RoomMessageText

from stack.bot import MicroBot
from stack.devices.notice import WAITS_EVENT
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

# The commands that answer a waiting browser: allow it, or end its
# request. For admins only, always.
ALLOW = "devices_allow"
DISMISS = "devices_dismiss"
ANSWERS = {ALLOW, DISMISS}


def _admin_users():
    """The Matrix ids of the admins, who may run mutating commands (up,
    down, restart, devices allow and dismiss): the bare user ids of
    `STACK_ADMIN_USER_IDS` on `MATRIX_SERVER_NAME`. If empty, all users in
    the room may run any command but `devices allow` and `dismiss`."""
    server_name = os.environ.get("MATRIX_SERVER_NAME", "home")
    return {f"@{uid.strip()}:{server_name}"
            for uid in os.environ.get("STACK_ADMIN_USER_IDS", "").split(",") if uid.strip()}


def _may_run(sender, cmd):
    """Whether `sender` may run `cmd` here: a read-only command anyone; a
    mutating one an admin, or anyone while no admins are configured; a
    `devices allow` or `dismiss` an admin only."""
    admins = _admin_users()
    if cmd in ANSWERS:
        return sender in admins
    return cmd in READ_ONLY_COMMANDS or not admins or sender in admins


def _call_api(request):
    """The host API's answer to `request`, or `{"error": ...}` saying why
    there is none. Blocks until the command ends."""
    return host_api.call(request, timeout=API_TIMEOUT_SECS)


def _first_start_reply(sid, words):
    """The answer to `stack up <sid>` when its first start is not for the
    room, or None to run it: a setup that needs a person at the Mac
    (`attended_setup`), or a preparation that takes long (`prepares`),
    while `sid` is not set up yet. An id `stack list` does not know is
    left to `up`, which names the error."""
    for row in _call_api({"cmd": "list"}).get("stacklets", []):
        if row.get("id") != sid or row.get("enabled"):
            continue
        if row.get("attended_setup"):
            return words["attended_setup"].format(sid=sid)
        if row.get("prepares"):
            return words["prepares"].format(sid=sid)
    return None


def _without_fallback(text):
    """`text` without a Matrix reply fallback: the quoted lines of the
    original message would otherwise re-trigger commands ("> stack up docs")."""
    return "\n".join(ln for ln in text.strip().split("\n") if not ln.startswith("> ")).strip()


def _parse_command(text):
    """Parse a message into an API request. Returns None if not a command.

    Requires the 'stack' prefix: stack status, stack up photos, etc.
    Messages without the prefix are ignored so normal chat doesn't
    accidentally trigger commands.

        _parse_command("stack devices allow K7MP-4XQD --shared 'Living room'")
        # {"cmd": "devices_allow", "code": "K7MP-4XQD", "as": "", "shared": "Living room"}
    """
    text = _without_fallback(text)

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

    if cmd == "devices" and len(parts) >= 2 and parts[1].lower() == "allow":
        return _allow_command(text)

    if cmd == "devices" and len(parts) >= 2 and parts[1].lower() == "dismiss":
        return {"cmd": DISMISS, "code": parts[2] if len(parts) >= 3 else ""}

    return None


def _allow_command(text):
    """`stack devices allow <code> [--as <person>] [--shared <name>]` as an
    API request; the code "" when the message names none, which the
    command refuses with what to type. A name in quotes stays one name."""
    try:
        words = shlex.split(text)[2:]
    except ValueError:
        words = text.split()[2:]
    request = {"cmd": ALLOW, "code": "", "as": "", "shared": ""}
    while words:
        word = words.pop(0)
        if word == "--as" and words:
            request["as"] = words.pop(0).lower()
        elif word == "--shared" and words:
            request["shared"] = words.pop(0)
        elif not request["code"]:
            request["code"] = word
    return request


def _is_reply(event):
    """Whether `event` replies to another message. A bare `allow` or
    `dismiss` that replies to nothing is ordinary chat and gets no answer."""
    relates = (getattr(event, "source", None) or {}).get("content", {}).get("m.relates_to") or {}
    return bool((relates.get("m.in_reply_to") or {}).get("event_id"))


def _parse_reply(text):
    """A reply `allow`, `allow as <person>`, `allow shared <name>` or
    `dismiss` as an API request without its code, which only the
    replied-to notice gives; None for any other text.

        _parse_reply("allow shared Living room")
        # {"cmd": "devices_allow", "code": "", "as": "", "shared": "Living room"}
        _parse_reply("dismiss")
        # {"cmd": "devices_dismiss", "code": ""}
    """
    words = _without_fallback(text).split()
    if [w.lower() for w in words] == ["dismiss"]:
        return {"cmd": DISMISS, "code": ""}
    if not words or words[0].lower() != "allow":
        return None
    request = {"cmd": ALLOW, "code": "", "as": "", "shared": ""}
    if len(words) == 1:
        return request
    if words[1].lower() == "as" and len(words) == 3:
        return {**request, "as": words[2].lower()}
    if words[1].lower() == "shared" and len(words) >= 3:
        return {**request, "shared": " ".join(words[2:])}
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
            "- `stack devices allow <code>`: let a browser that waits to join in "
            "(`--as <name>`, `--shared <name>`); or reply `allow` to its notice\n"
            "- `stack devices dismiss <code>`: end its request; or reply `dismiss`\n"
            "- `stack help`: this message\n\n"
            "**Services:** photos, docs, ai, chatai, bots"),
        "attended_setup": ("**{sid}** needs its first setup at the Mac: it asks questions, "
                           "and macOS asks for permissions along the way.\n"
                           "Run `stack up {sid}` in the terminal."),
        "prepares": ("**{sid}** downloads and installs for a while on its first start, longer "
                     "than the chat waits for an answer. Start it at the Mac: on the setup page "
                     "(`stack devices admin-link --page setup` prints its link) or with "
                     "`stack up {sid}` in the terminal."),
        "admins_only": "Only admins can run that command. Try `status` or `list`.",
        "not_a_notice": ("`allow` and `dismiss` work as a reply to the notice of a browser "
                         "that waits to join. Or write `stack devices allow <code>`."),
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
            "- `stack devices allow <Code>`: einen wartenden Browser zulassen "
            "(`--as <Name>`, `--shared <Name>`); oder auf seine Nachricht mit `allow` antworten\n"
            "- `stack devices dismiss <Code>`: seine Anfrage beenden; oder mit `dismiss` antworten\n"
            "- `stack help`: diese Hilfe\n\n"
            "**Dienste:** photos, docs, ai, chatai, bots"),
        "attended_setup": ("**{sid}** wird beim ersten Mal am Mac eingerichtet: Die Einrichtung "
                           "stellt Fragen, und macOS fragt nach Berechtigungen.\n"
                           "Gib im Terminal `stack up {sid}` ein."),
        "prepares": ("Beim ersten Start lädt **{sid}** eine Weile herunter und installiert. "
                     "Das dauert länger, als der Chat auf eine Antwort wartet. Starte es am Mac: "
                     "auf der Einrichtungsseite (den Link gibt `stack devices admin-link --page "
                     "setup` aus) oder mit `stack up {sid}` im Terminal."),
        "admins_only": "Diesen Befehl dürfen nur Admins ausführen. Probier `status` oder `list`.",
        "not_a_notice": ("`allow` und `dismiss` wirken als Antwort auf die Nachricht, dass ein "
                         "Browser zu famstack möchte. Oder schreib `stack devices allow <Code>`."),
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
    if cmd in ANSWERS:
        return f"✅ {result['line']}" if result.get("line") else f"❌ {result.get('error', '')}"

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
    """The Server room's bot: runs `stack` commands for the room through
    the host API and answers each one in a reply.

        Homer: stack devices allow K7MP-4XQD
        Stacker: ✅ Chrome on a Mac is now Homer's.
    """

    name = "stacker-bot"

    def register_callbacks(self, client: AsyncClient) -> None:
        self.add_event_callback(self._on_message, RoomMessageText)

    async def _on_message(self, room, event: RoomMessageText) -> None:
        request = _parse_command(event.body) or (_is_reply(event) and _parse_reply(event.body))
        if not request:
            return

        cmd = request.get("cmd", "")
        sid = request.get("stacklet", "")
        words = _words()

        # SAFETY: mutating commands require an admin; `devices allow` and
        # `dismiss` an admin always (`_may_run`), checked before the notice
        # is read.
        if not _may_run(event.sender, cmd):
            await self._send_reply(room.room_id, event, words["admins_only"])
            return

        if cmd in ANSWERS:
            request = await self._with_notice_code(room, event, request)
            if request is None:
                await self._send_reply(room.room_id, event, words["not_a_notice"])
                return

        logger.info("[stacker] Command from {}: {}", event.sender, request)

        if cmd == "help":
            await self._send_reply(room.room_id, event, words["help"])
            return

        if cmd == "up" and sid and (reply := _first_start_reply(sid, words)):
            await self._send_reply(room.room_id, event, reply)
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

    async def _with_notice_code(self, room, event, request):
        """`request` (a `devices allow` or `dismiss`) completed: the code of
        the notice a reply answers, and for an allow that names nobody the
        admin who wrote it as the person. None for a reply to anything but
        a waiting notice this bot's own account sent."""
        if request["cmd"] == ALLOW and not request["as"] and not request["shared"]:
            request = {**request, "as": event.sender.split(":", 1)[0].lstrip("@")}
        if request["code"]:
            return request
        # SAFETY: the code comes only from the envelope of the stack's own
        # notice (`_reply_parent_envelope` checks the sender is this bot),
        # never from a message's text, so nobody can choose the code.
        envelope = await self._reply_parent_envelope(room.room_id, event)
        if not envelope or envelope.get("type") != WAITS_EVENT:
            return None
        code = (envelope.get("data") or {}).get("code")
        return {**request, "code": code} if isinstance(code, str) and code else None

    async def _send_reply(self, room_id, event, text):
        """Reply to `event` with `text` via the framework's formatted send.

        Thin convenience over `MicroBot._send` — derives the reply
        relation from the triggering event. Markdown→HTML, the reply
        relation, and the single `_room_send` seam all live in the
        framework now; this no longer hand-rolls HTML.
        """
        await self._send(room_id, text, reply_to=event.event_id)
