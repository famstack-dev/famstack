"""famstack API — the host's single CLI bridge for containers.

Runs on the host as a launchd service (core owns it). It is the one bridge
between Docker containers and the host CLI, and its output mirrors the request,
so every consumer gets back what it speaks:

  * JSON object in  → runs `./stack <cmd> --json`  → JSON out.
    A lifecycle surface (status/list/up/down/logs, and the family) for
    trusted core tools: the stacker bot and the tools-server.
  * plaintext line in → runs `./stack <args>` in text mode → plaintext out.
    A curated read/domain surface (memory, docs, and the stacklet commands
    in stack.toml `[agent] allow`) for the agent, which wants a token-lean
    answer, not JSON, and must never reach lifecycle ops.

Format is auto-detected: a request that begins with `{` is JSON, anything else
is a plaintext command line. One service, two allowlists scoped by trust.

Every container that reaches the Mac reaches this port, so the format alone
says nothing about who sent a request. Each calling stacklet therefore has a
token of its own, generated like its other secrets (`[env] generate =
["STACK_API_TOKEN"]`) and given to its containers as `STACK_API_TOKEN`.
Core's opens the JSON surface, the agent's the plaintext one. `admit` checks
it where a request is read, before any command is chosen; nothing else here
knows about tokens. A request with no token, an unknown one or the other
surface's is refused and runs nothing, `discover` included.

    {"cmd": "list", "token": "<core's token>"}          core's tools server
    token <the agent's token> memory search tent        the agent's shim

Protocol (JSON):  client sends a JSON object + newline; server replies JSON + newline.
                  With `"stream": true` (`up`, `follow`, `runs`) the server
                  sends each event line of a run as the run writes it to its
                  log, then closes after the final `result` (ADR-020); `runs`
                  goes on until the client hangs up.
Protocol (text):  client sends a command line; server replies plaintext, then closes.

The tokens are read from the instance's secret store on every request, so
a token `stack up agent` makes after the API started works at once.
"""

import hmac
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from stack.agent_access import allowed_commands
from stack import host_api, passwords, runs
from stack.live import follow
from stack.live.envelope import RESULT, Event
from stack.schedule import split_command
from stack.secrets import TomlSecretStore
from stack.stack import Stack, configured_data_dir
from stack.stacklet_code import import_stacklets

# The API runs the stack's own stacklets, from the checkout it is in.
import_stacklets([Path(__file__).resolve().parent.parent])
from stacklets.core import scheduler  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STACK_BIN = REPO_ROOT / "stack"
API_HOST = os.environ.get("STACK_API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("STACK_API_PORT", "42001"))

ALLOWED_COMMANDS = {"status", "list", "config", "up", "down", "restart", "env", "logs", "family",
                    "chat_users", "configure", "discover", "firewall", "ai_recommended",
                    "ai_answer", "ai_check", "ai_switch", "ai_welcome", "ai_intro", "ai_key", "ai_new_key",
                    "devices", "devices_set", "passwords", "join_notice"}
# Read-only stacklet commands the dashboard shows, by the name it asks for.
# The firewall is read here, on the Mac: a container cannot see it.
STACKLET_READS = {"chat_users": ["messages", "users"], "firewall": ["core", "firewall"],
                  "ai_recommended": ["ai", "recommended", "show"],
                  # What the AI last answered the stack's one short question
                  # for the current `[ai]`, from the instance; asks nothing.
                  "ai_answer": ["ai", "check", "--last"],
                  # The AI's first words for the setup page: the text and its
                  # audio in the house voice, played in the browser, never here.
                  "ai_welcome": ["ai", "hello", "--audio"],
                  "ai_intro": ["ai", "intro", "--audio"],
                  # Whether the AI's key is still the placeholder; never the key.
                  "ai_key": ["ai", "key", "show"]}
NEEDS_STACKLET = {"up", "down", "restart", "env", "logs", "configure"}

# The plaintext surface (the agent): a curated set of read/domain commands matched
# by leading tokens. The agent is an LLM, so it must NOT reach the lifecycle
# commands above -- those stay on the JSON path used only by trusted core tools.
DOMAIN_ALLOW = [
    ["memory", "search"],
    # The family memory as linked cards: find by words, open one by its id,
    # follow its links. Read-only, straight off the brain and the vault's log.
    ["memory", "find"],
    ["memory", "card"],
    ["memory", "links"],
    # Reading a person only: `person add` writes the family's own
    # declarations, which stay with the family.
    ["memory", "person", "show"],
    ["memory", "topic"],
    ["memory", "lookup"],
    ["memory", "correspondents"],
    # The agent's only way to put something *into* the vault. It runs the
    # archivist's own pipeline, so what the agent files is classified,
    # attributed and mirrored exactly like a note pasted into a room.
    ["memory", "capture"],
    # ... and the way it changes one. A page is handed over whole and the
    # reply names what the edit did, so a rewrite that drops items says so.
    ["memory", "write"],
    # The family's lists: which exist, what is open on one, and one item
    # changed by name. The store matches the item and keeps the rest of
    # the file intact, so a tick cannot lose other state.
    ["memory", "list"],
    # What changed, when, and who did it. The vault is a git repo and has
    # always known this; nothing read it back until now.
    ["memory", "history"],
    ["docs", "show"],
    # Speaking aloud on the server Mac. It plays sound and stores nothing;
    # `ai switch` and `ai download` change the instance and stay out.
    ["ai", "say"],
    # Reminders and timers: a message the agent sends later, at a time the
    # family's scheduler keeps (`schedule_refusal` says how far it reaches).
    ["core", "schedule"],
]


# The account the agent may send as on a `messages` command. Without `--as`
# a message comes from the system account; with it, only from the agent
# itself. Writing as a person stays a tool for the terminal.
AGENT_SENDER = "agent"


def sender_refusal(args):
    """Why the agent may not run this command line, or None.

    Refuses a `messages` command whose `--as` names anyone but the agent,
    so an admin who lets the agent send messages has not let it write as
    a person. `--as` on other commands is who did something, not who
    speaks, and is left alone.
    """
    if args[:1] != ["messages"]:
        return None
    for i, word in enumerate(args):
        if word == "--as" and args[i + 1:i + 2] != [AGENT_SENDER]:
            return f"the agent sends only as itself (--as {AGENT_SENDER}), never as a person"
    return None


# The agent lists every scheduled entry, but adds and removes only its
# reminders, so it can neither replace nor delete an entry the family made.
SCHEDULE_VERBS = {"list", "add", "remove"}
REMINDER_PREFIX = "remind-"
# `core schedule add <name> <when>` come first, then the options and the command.
ADD_OPTIONS_START = 5


def schedule_refusal(args, domain_allow):
    """Why the agent may not run this `core schedule` line, or None.

    The scheduler runs a stacklet command later, as the admin, on the host.
    Through the agent it runs only what the agent may run now: a command it
    is allowed and that names no path on the host, or a message it sends as
    itself. The command is read as `core schedule add` reads it, because a
    second reading would check one command and store another.
    """
    if args[:2] != ["core", "schedule"]:
        return None
    verb = args[2] if len(args) > 2 else ""
    if verb not in SCHEDULE_VERBS:
        return f"'core schedule {verb}' is not allowed; the agent lists, adds and removes reminders"
    if verb == "list":
        return None
    name = args[3] if len(args) > 3 else ""
    if not name.startswith(REMINDER_PREFIX):
        return f"the agent adds and removes only its reminders, named {REMINDER_PREFIX}<...>"
    if verb == "remove":
        return None
    _options, command = split_command(args[ADD_OPTIONS_START:])
    if _hides_its_command(command):
        return OPTION_REFUSAL
    if _names_a_host_path(command):
        return HOST_PATH_REFUSAL
    if command[:2] == ["messages", "send"]:
        return sender_refusal(command)
    if command[:2] == ["core", "schedule"] or not any(command[:len(p)] == p for p in domain_allow):
        return f"'{' '.join(command[:2])}' is not allowed in a reminder"
    return None


def _instance_stack():
    """The instance as the CLI sees it, read fresh for every request.

    STACK_DIR names the instance dir, as it does for the CLI, and the
    subprocess below inherits it. The data dir is the one stack.toml
    names, where the migrations an agent's command waits for are recorded.
    """
    instance = Path(os.environ.get("STACK_DIR") or REPO_ROOT).expanduser()
    config = Stack(REPO_ROOT, instance, instance_dir=instance).config
    return Stack(REPO_ROOT, configured_data_dir(config), instance_dir=instance)


# `stack up` starts the stacklets before it runs their migrations, and the
# agent's commands act on their data with the new code. So a command waits
# until no installed stacklet has a migration left to run, up to a minute,
# which leaves the command room inside the client's 125 seconds.
MIGRATIONS_WAIT_SECS = 60
MIGRATIONS_POLL_SECS = 2


def _unmigrated_after_waiting():
    """What is still left to run once the wait is over; [] at once when nothing is."""
    stack = _instance_stack()
    deadline = time.monotonic() + MIGRATIONS_WAIT_SECS
    while (keys := stack.unmigrated()) and time.monotonic() < deadline:
        time.sleep(MIGRATIONS_POLL_SECS)
    return keys


def _domain_allow():
    """DOMAIN_ALLOW plus the stacklet commands the admin listed in
    `[agent] allow`. Only stacklet commands qualify, so no entry can add a
    lifecycle command (see stack/agent_access.py)."""
    return DOMAIN_ALLOW + allowed_commands(_instance_stack())


def handle_request(data):
    """Process a single JSON command by calling the stack CLI."""
    try:
        req = json.loads(data)
    except json.JSONDecodeError:
        return {"error": "Invalid JSON"}

    cmd = req.get("cmd", "")
    if cmd not in ALLOWED_COMMANDS:
        return {"error": f"Unknown command: {cmd}", "allowed": sorted(ALLOWED_COMMANDS)}

    # Discover — return the API surface (no auth, no stacklet needed)
    if cmd == "discover":
        return {
            "version": "0.2.1",
            "commands": {
                "status":   {"needs_stacklet": False, "description": "Server status overview"},
                "list":     {"needs_stacklet": False, "description": "List all stacklets and their state"},
                "config":   {"needs_stacklet": False, "description": "Show stack.toml configuration"},
                "up":       {"needs_stacklet": True,  "description": "Start a stacklet",
                             "options": ["stream", "surface"]},
                "down":     {"needs_stacklet": True,  "description": "Stop a stacklet"},
                "restart":  {"needs_stacklet": True,  "description": "Restart a stacklet"},
                "env":      {"needs_stacklet": True,  "description": "Render environment variables"},
                "logs":     {"needs_stacklet": True,  "description": "Get container logs", "options": ["tail", "grep"]},
                "family":   {"needs_stacklet": False, "description": "Show the family, or set it",
                             "options": ["action", "family"]},
                "chat_users": {"needs_stacklet": False,
                               "description": "Who has a chat account, and who has signed in"},
                "firewall": {"needs_stacklet": False,
                             "description": "Whether the Mac's firewall keeps phones away "
                                            "from the chat"},
                "configure": {"needs_stacklet": True, "options": ["answers"],
                              "description": "A stacklet's next setup form, or ready once the "
                                             "answers are applied"},
                "ai_recommended": {"needs_stacklet": False,
                                   "description": "The settings recommended for this Mac, "
                                                  "and the AI server the stack uses"},
                "ai_answer": {"needs_stacklet": False,
                              "description": "What the AI last answered the stack's one "
                                             "short question"},
                "ai_check": {"needs_stacklet": False,
                             "description": "Ask the AI one short question again, as "
                                            "'stack ai check' does, and keep the answer"},
                "ai_switch": {"needs_stacklet": False, "options": ["address", "key", "model"],
                              "description": "Use an AI server the stack does not manage, as "
                                             "'stack ai switch' does"},
                "ai_welcome": {"needs_stacklet": False,
                               "description": "The AI's welcome, as text and MP3 audio"},
                "ai_intro": {"needs_stacklet": False,
                             "description": "The AI's introduction in its own words, as text "
                                            "and MP3 audio"},
                "ai_key": {"needs_stacklet": False,
                           "description": "Whether the key of the AI the stack manages is "
                                          "still the placeholder"},
                "devices": {"needs_stacklet": False,
                            "description": "The devices paired with the home address"},
                "devices_set": {"needs_stacklet": False,
                                "options": ["action", "person", "name", "key_hash", "id"],
                                "description": "Add, remove or mark as seen one paired device"},
                "join_notice": {"needs_stacklet": False,
                                "options": ["kind", "browser", "system", "code", "since",
                                            "device", "by"],
                                "description": "Tell the Server room of a new device: a "
                                               "browser that waits, or one that was added"},
                "ai_new_key": {"needs_stacklet": False,
                               "description": "A new key for the AI the stack manages; the "
                                              "stacklets that use it restart on their own"},
                "passwords": {"needs_stacklet": False, "options": ["people"],
                              "description": "The stored password of each family member "
                                             "named, for the pages that may show it"},
            },
        }

    # `passwords` reads the stored password of each family member named
    # (`stack.passwords.of_people`), here on the Mac, where the secrets
    # are: the tools server has no access to them. It runs in this
    # process, so a password is never on a command line or in a CLI's
    # output.
    # SAFETY: the JSON surface answers core's token only (`admit`), and
    # the tools server asks only for the people a page may show. Never
    # log the request's answer.
    if cmd == "passwords":
        return {"passwords": passwords.of_people(_instance_stack(), req.get("people"))}

    sid = req.get("stacklet", "")
    if cmd in NEEDS_STACKLET and not sid:
        return {"error": f"'{cmd}' requires a 'stacklet' field"}

    # Build the CLI command
    args = [str(STACK_BIN), cmd, "--json"]
    if sid:
        args.insert(2, sid)

    # `family` reads, and with `"action": "set"` writes the `"family"` object
    # (`stack family set`). The object goes to the CLI on stdin: a command
    # line is visible to every process on the Mac, and limited in length.
    if cmd in STACKLET_READS:
        args = [str(STACK_BIN), *STACKLET_READS[cmd], "--json"]

    stdin = None
    if cmd == "family" and req.get("action"):
        if req["action"] != "set":
            return {"error": f"Unknown family action: {req['action']}. Use: set"}
        args = [str(STACK_BIN), "family", "set", "--from", "-", "--json"]
        stdin = json.dumps(req.get("family", {}))

    # `configure` answers a stacklet's setup forms (`stack configure --json`):
    # the next form, or ready once applied. The answers go on stdin, as the
    # family does, because one of them may be a secret.
    if cmd == "configure":
        args += ["--answers", "-"]
        stdin = json.dumps(req.get("answers", {}))

    # `ai_switch` is `stack ai switch <address>` for the setup page's "Use
    # another AI server". The key goes on stdin (`--key -`), as the setup
    # forms' answers do, because a command line is visible to every
    # process on the Mac. An address or a model that starts with "-"
    # would read as an option, so it is refused.
    if cmd == "ai_switch":
        address, model = req.get("address"), req.get("model", "")
        if not _plain_argument(address) or (model and not _plain_argument(model)):
            return {"error": "'ai_switch' requires an 'address', and a 'model' if any, "
                             "as text that does not start with '-'"}
        args = [str(STACK_BIN), "ai", "switch", address, "--key", "-", "--json"]
        if model:
            args += ["--model", model]
        stdin = str(req.get("key") or "") + "\n"

    # `ai_new_key` gives the AI a new key and returns it. The stacklets
    # that use it restart on their own (`--no-wait`): core is one, and it
    # runs the caller, which must get the answer first.
    if cmd == "ai_new_key":
        args = [str(STACK_BIN), "ai", "key", "new", "--no-wait", "--json"]

    # `ai_check` asks the AI server `[ai]` names one short question and keeps
    # the answer for the setup page: its "Check again" for a server the
    # stack does not run. A failed answer exits 1 and still prints it.
    if cmd == "ai_check":
        args = [str(STACK_BIN), "ai", "check", "--json"]

    # The list of paired devices has one writer, `stack devices` (ADR-023
    # D9): the tools server reads the list through a
    # read-only mount and changes it only here. A field that starts with
    # "-" would read as an option, so it is refused. The key's hash is no
    # secret: the device's key itself never leaves the tools server.
    if cmd == "devices":
        args = [str(STACK_BIN), "devices", "list", "--json"]
    if cmd == "devices_set":
        args = _devices_set_args(req)
        if isinstance(args, dict):
            return args

    # The Server room's notice of a new device (ADR-023 D10):
    # the tools server sends fields only, and `stack devices notice` writes
    # the text, so no text of a browser reaches the chat.
    if cmd == "join_notice":
        args = _join_notice_args(req)
        if isinstance(args, dict):
            return args

    # Logs-specific options
    if cmd == "logs":
        tail = req.get("tail", 200)
        if tail:
            args += ["--tail", str(tail)]
        grep = req.get("grep")
        if grep:
            args += ["--grep", grep]

    try:
        result = subprocess.run(
            args, input=stdin, capture_output=True, text=True, timeout=300,
            cwd=str(REPO_ROOT), env=_one_answer_env(cmd, req),
        )
        # Try to parse as JSON, fall back to raw output
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                "ok": result.returncode == 0,
                "output": result.stdout.strip(),
                "error": result.stderr.strip() if result.returncode != 0 else "",
            }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out: stack {cmd} {sid}"}
    except Exception as e:
        return {"error": str(e)}


# The commands that are or start a run of a stacklet, answered in one line
# within the limit above: the Server room's `up`, `down` and `restart`, and
# `ai_switch`, whose `managed` starts the ai stacklet.
RUNS_ANSWERED_AT_ONCE = {"up", "down", "restart", "ai_switch"}


def _one_answer_env(cmd, req):
    """The environment of a command run for a one-line answer: a run in it
    does not wait its turn behind another run of the stack (`stack.runs`,
    one at a time), because that can take longer than the caller waits.
    While another run goes on it is refused at once, and its answer names
    that run. The caller's surface names this run in the other's refusals."""
    if cmd not in RUNS_ANSWERED_AT_ONCE:
        return None
    return {**os.environ, runs.QUEUE_VARIABLE: "no", runs.SURFACE_VARIABLE: _surface(req)}


def _devices_set_args(req):
    """The command line of one `devices_set` request, or its refusal.

        {"cmd": "devices_set", "action": "remove", "id": "d-3f9a2c1b"}
        # [stack, "devices", "remove", "d-3f9a2c1b", "--json"]
    """
    action = req.get("action")
    if action == "add":
        person, name, key_hash = req.get("person", ""), req.get("name"), req.get("key_hash")
        if not (person == "" or _plain_argument(person)) or not _plain_argument(name) \
                or not _plain_argument(key_hash):
            return {"error": "'devices_set' add requires 'person' ('' for the household), "
                             "'name' and 'key_hash' as text that does not start with '-'"}
        return [str(STACK_BIN), "devices", "add", "--person", person, "--name", name,
                "--key-hash", key_hash, "--json"]
    if action in ("remove", "seen"):
        if not _plain_argument(req.get("id")):
            return {"error": f"'devices_set' {action} requires the device's 'id'"}
        return [str(STACK_BIN), "devices", action, req["id"], "--json"]
    return {"error": f"Unknown devices_set action: {action}. Use: add, remove, seen"}


def _join_notice_args(req):
    """The command line of one `join_notice` request, or its refusal. A
    field the tools server leaves empty is left out; the command checks
    each one against its fixed list.

        {"cmd": "join_notice", "kind": "waits", "browser": "Chrome", "system": "Mac",
         "code": "K7MP4XQD", "since": "21:14"}
        # [stack, "devices", "notice", "--kind", "waits", "--browser", "Chrome", ...]
        {"cmd": "join_notice", "kind": "added", "device": "d-3f9a2c1b", "by": "mac"}
        # [stack, "devices", "notice", "d-3f9a2c1b", "--kind", "added", "--by", "mac", "--json"]
    """
    kind = req.get("kind")
    if kind not in ("waits", "added"):
        return {"error": "'join_notice' requires 'kind': waits or added"}
    args = [str(STACK_BIN), "devices", "notice"]
    if kind == "added":
        if not _plain_argument(req.get("device")):
            return {"error": "'join_notice' added requires the device's 'id'"}
        args.append(req["device"])
    args += ["--kind", kind]
    names = ("by",) if kind == "added" else ("browser", "system", "code", "since")
    for name in names:
        value = req.get(name) or ""
        if value and not _plain_argument(value):
            return {"error": f"'join_notice' takes '{name}' as text that does not start with '-'"}
        if value:
            args += [f"--{name}", value]
    return args + ["--json"]


def _plain_argument(value) -> bool:
    """Whether `value` is text that goes on a command line as an argument,
    never as an option: not empty, no leading "-", no line break.

        _plain_argument("192.168.1.20:11434")    # True
        _plain_argument("--whisper=evil")        # False
    """
    return (isinstance(value, str) and bool(value.strip()) and not value.startswith("-")
            and "\n" not in value)


# ── Runs, started here and followed from their logs ─────────────────────
# Every run of a stacklet writes its events to the stacklet's run log on
# this Mac and holds its run lock until it ends (`stack.run_log`,
# `stack.runs`), whoever started it. A caller follows a run from there
# (`stack.live.follow`), so a start from the terminal shows as one from the
# dashboard does, and a caller that went away goes on where it was:
#
#   {"cmd": "up", "stacklet": "photos", "surface": "dashboard", "stream": true}
#       starts `stack up photos` on its own and follows its run. A run of
#       photos that goes on already is followed instead; no second run
#       starts.
#   {"cmd": "follow", "stacklet": "photos", "after": 0, "stream": true}
#       the current or last run's events above `after`, a `result` last
#   {"cmd": "runs", "stream": true}
#       a `run` event for each run going on, then for each that starts;
#       an empty line while there is none, so the caller knows this is alive
#
# Without "stream" the reply stays one line, which is all the tools server
# and the stacker bot read for every other command.

# The commands a caller may follow as a stream.
STREAMED_COMMANDS = {"up", "follow", "runs"}

# How long a run the API started may say nothing before the API ends it. Not
# a limit on the run: one that keeps reporting goes on as long as it needs,
# image pulls and model downloads included. The longest `stack up` stays
# quiet is a hook's command, which the CLI stops itself after 1200 s
# (`ctx.shell` and its siblings in stack/hooks.py; image pulls stop at 600 s,
# health checks at their manifest's timeout). A minute more lets those fire
# first, so the CLI reports its own error, and this only ends a run that
# hangs past all of them. A run started elsewhere is followed, never ended.
STREAM_SILENCE_SECS = 1200 + 60

# How often the `runs` feed says it is alive while no run starts. The
# caller gives up on the API after a few of these are missing.
RUNS_HEARTBEAT_SECS = 30

# How long a started `stack up` may take to begin its run (to take its
# lock). Before that it only reads its configuration.
BEGIN_SECS = 60

# How much of the command's stderr a result written here carries.
STDERR_TAIL_LINES = 5

# Where a run was started, as a refused second run names it ("photos is
# already starting (from the dashboard, ...)"). A caller names itself; a
# word or two, nothing else, because the text lands in that message.
SURFACE = re.compile(r"[a-z][a-z ]{0,29}")
DEFAULT_SURFACE = "host API"

# What a stacklet id looks like, for the requests that name one here.
STACKLET_ID = re.compile(r"[a-z0-9_]+")


def _instance_dir():
    """The instance whose runs are read: STACK_DIR, as the CLI has it."""
    return Path(os.environ.get("STACK_DIR") or REPO_ROOT).expanduser()


class _Start:
    """`stack up <stacklet>`, started on its own, in its own session.

    The command goes on when the caller hangs up: the run's state is in its
    log, not in a connection (ADR-020). Its stdout and stderr go to files,
    read only when it ended before its run began.

        started = _Start("photos", "dashboard")
        started.run()        # the run going on now, or None
        started.outcome()    # the command's result, when there is no run
    """

    def __init__(self, stacklet, surface):
        self.stacklet = stacklet
        self.instance = _instance_dir()
        self._before = follow.current(self.instance, stacklet)
        self._out = tempfile.TemporaryFile()
        self._err = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(
            [str(STACK_BIN), "up", stacklet, "--events"],
            stdin=subprocess.DEVNULL, stdout=self._out, stderr=self._err,
            cwd=str(REPO_ROOT), start_new_session=True,
            env={**os.environ, runs.SURFACE_VARIABLE: surface},
        )
        # Reaped as it ends, whether or not anyone still follows it.
        threading.Thread(target=self.proc.wait, daemon=True).start()

    def run(self):
        """The `run` event's data of the run going on once the command took
        its lock, or of the run that refused it; None when the command
        ended, or did not begin within BEGIN_SECS, without one."""
        deadline = time.monotonic() + BEGIN_SECS
        while self.proc.poll() is None and time.monotonic() < deadline:
            if (run := self._new_run()) is not None:
                return run
            time.sleep(follow.POLL_SECS)
        if (run := self._new_run()) is not None:
            return run
        if self.proc.returncode == runs.EXIT_ALREADY_RUNNING:
            return follow.current(self.instance, self.stacklet)
        return None

    def ours(self):
        """Whether the run going on is this command's: it was not refused."""
        return self.proc.poll() != runs.EXIT_ALREADY_RUNNING

    def outcome(self):
        """The result of a command that began no run: its own, or one that
        says how it ended."""
        self._out.seek(0)
        for line in reversed(self._out.read().decode(errors="replace").splitlines()):
            if (event := Event.from_line(line)) and event.type == RESULT:
                return event
        if self.proc.poll() is None:
            error = f"stack up {self.stacklet} did not begin its run within {BEGIN_SECS} s"
        else:
            error = f"stack up {self.stacklet} ended without a result (exit {self.proc.returncode})"
        return Event.now(1, RESULT, {"error": error, "output": self._tail()})

    def explained(self, result):
        """A result `follow` wrote for this command's run, with what only
        the starter knows: a silent run is ended here, and an ended one
        has an exit code and the end of its stderr."""
        how = result.data.get(follow.STOPPED)
        if not how or not self.ours():
            return result
        what = f"stack up {self.stacklet}"
        if how == follow.SILENT:
            _end(self.proc)
            error = f"{what}: stopped hearing from it for {STREAM_SILENCE_SECS} s, so ended it"
        else:
            try:
                code = self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                return result
            error = f"{what} ended without a result (exit {code})"
        return Event(result.id, RESULT, result.time,
                     {**result.data, "error": error, "output": self._tail()})

    def _new_run(self):
        run = follow.current(self.instance, self.stacklet)
        return run if run is not None and run != self._before else None

    def _tail(self):
        """The last lines the command wrote on stderr: the end of a
        traceback says why it fell over, the rest is progress."""
        self._err.seek(0)
        text = self._err.read().decode(errors="replace").strip()
        return "\n".join(text.splitlines()[-STDERR_TAIL_LINES:])


def _end(proc):
    """Stop a run and everything in its session: politely, then for good."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _surface(req):
    surface = req.get("surface")
    return surface if isinstance(surface, str) and SURFACE.fullmatch(surface) else DEFAULT_SURFACE


def stream_request(req):
    """The reply to a request with "stream": true, each line as it exists.

    A command that does not stream is refused with one line, as any other
    refusal is. `up` and `follow` end with one `result`, the run's own or
    one written for a run that stopped or fell silent. `runs` never ends.
    """
    cmd = req.get("cmd", "")
    if cmd not in STREAMED_COMMANDS:
        allowed = ", ".join(sorted(STREAMED_COMMANDS))
        yield json.dumps({"error": f"'{cmd}' does not stream; only {allowed} do"}) + "\n"
        return
    if cmd == "runs":
        for run in follow.starts(_instance_dir(), heartbeat_secs=RUNS_HEARTBEAT_SECS):
            yield "\n" if run is None else run.to_line()
        return
    sid = req.get("stacklet", "")
    if not STACKLET_ID.fullmatch(sid):
        yield json.dumps({"error": f"'{cmd}' requires a 'stacklet' field with a stacklet id"}) + "\n"
        return
    if cmd == "follow":
        after = req.get("after", 0)
        after = after if isinstance(after, int) else 0
        for event in follow.follow(_instance_dir(), sid, after=after):
            yield event.to_line()
        return
    yield from _up_stream(sid, _surface(req))


def _up_stream(sid, surface):
    """`stack up <sid>` started, then the run's events from its log, a
    `result` last; the command's own result when it began no run."""
    started = _Start(sid, surface)
    if started.run() is None:
        yield started.outcome().to_line()
        return
    silence = STREAM_SILENCE_SECS if started.ours() else None
    for event in follow.follow(started.instance, sid, silence_secs=silence):
        yield (started.explained(event) if event.type == RESULT else event).to_line()


def _send_each(conn, lines, *, to_the_end):
    """Send each line as it comes.

    With `to_the_end`, a caller that hangs up does not stop the reading:
    a run this API started is still followed to its end, so it is still
    ended if it falls silent. Otherwise reading stops with the caller.
    """
    for line in lines:
        try:
            conn.sendall(line.encode())
        except OSError:
            if not to_the_end:
                return
            for _ in lines:
                pass
            return


# How a failed command reports itself over the plaintext protocol, which
# otherwise carries only the CLI's text. Silence here is not neutral: the agent
# reads a usage message as a result and retries the same call forever, which is
# how one mis-parsed search argument became an unbounded tool loop. Appended
# only on failure, so successful output stays byte-identical.
EXIT_MARKER = "stack-exit:"


def _with_exit(text, code):
    """The reply body, plus a status line when the command failed."""
    if not code:
        return text
    if not text.endswith("\n"):
        text += "\n"
    return f"{text}{EXIT_MARKER} {code}\n"


# Options that name a path on the host, with the leading words of the
# commands they do that in (none: every command). This API runs on the host,
# and the agent's lines are written by a model that reads email and web
# pages, so a path would let it read, or file into the memory, any file on
# the Mac. `--from` is a path only to `memory write`: to `photos album` it
# is an album.
HOST_PATH_OPTIONS = [
    ([], {"--vault", "--file"}),
    (["memory", "write"], {"--from"}),
    (["photos", "import"], {"--source"}),
]
# Commands whose arguments are a path on the host, whoever allowed them.
HOST_PATH_COMMANDS = [["messages", "upload"]]
HOST_PATH_REFUSAL = ("a path on the host is not allowed from the agent (--vault, --file, "
                     "memory write --from, photos import --source, messages upload)")


def _sets_option(arg, names):
    """Whether `arg` sets one of `names`, written out or shortened.

    argparse accepts any unambiguous prefix of a long option, so `--vau` is
    `--vault` to `memory search`. Matching the name exactly let it through.
    """
    given = arg.split("=", 1)[0]
    return given.startswith("--") and len(given) > 2 and any(n.startswith(given) for n in names)


# The checks read a command by the position of its words. The CLI ignores an
# option it does not know, so in `photos --x import` the command is `import`,
# in a place the checks do not look. Help runs nothing.
OPTION_REFUSAL = "an option between a stacklet and its command is not allowed"
HELP_OPTIONS = {"-h", "--help"}


def _hides_its_command(args):
    """Whether an option stands where the stacklet's command belongs."""
    return len(args) > 1 and args[1].startswith("-") and args[1] not in HELP_OPTIONS


def _names_a_host_path(args):
    options = set().union(*(names for words, names in HOST_PATH_OPTIONS
                            if args[:len(words)] == words))
    return (any(_sets_option(a, options) for a in args)
            or any(args[:len(c)] == c for c in HOST_PATH_COMMANDS))


def handle_plaintext(line):
    """Run one allowlisted `stack` command in the CLI's text mode.

    The token-lean counterpart to `handle_request`: the agent sends a plaintext
    command line and gets the CLI's normal text output back (no `--json`), so its
    context stays small. Confined to `DOMAIN_ALLOW` and the stacklet commands
    the admin allowed in stack.toml -- never lifecycle ops.

    Every path that is not a command that ran and succeeded reports a non-zero
    status, refusals included. A refusal the caller cannot distinguish from an
    answer is a retry loop waiting to happen.
    """
    try:
        args = shlex.split(line)
    except ValueError as e:
        return _with_exit(f"error: could not parse command ({e})\n", 2)
    if not args:
        return _with_exit("error: empty command\n", 2)
    if _hides_its_command(args):
        return _with_exit(f"error: {OPTION_REFUSAL}\n", 126)
    if _names_a_host_path(args):
        return _with_exit(f"error: {HOST_PATH_REFUSAL}\n", 126)
    domain_allow = _domain_allow()
    if not any(args[:len(p)] == p for p in domain_allow):
        allowed = ", ".join(" ".join(p) for p in domain_allow)
        return _with_exit(
            f"error: '{' '.join(args[:2])}' is not allowed. Allowed: {allowed}\n", 126,
        )
    refusal = sender_refusal(args) or schedule_refusal(args, domain_allow)
    if refusal:
        return _with_exit(f"error: {refusal}\n", 126)
    if unmigrated := _unmigrated_after_waiting():
        # 75, EX_TEMPFAIL: the same command succeeds once `stack up` has run them.
        return _with_exit(f"error: the stack is migrating its data ({', '.join(unmigrated)}); "
                          "ask again in a few minutes\n", 75)
    try:
        r = subprocess.run(
            [str(STACK_BIN), *args], capture_output=True, text=True,
            timeout=120, cwd=str(REPO_ROOT),
        )
        return _with_exit(r.stdout or r.stderr or "(no output)\n", r.returncode)
    except subprocess.TimeoutExpired:
        return _with_exit("error: command timed out\n", 124)
    except Exception as e:
        return _with_exit(f"error: {e}\n", 1)


# ── Callers and their tokens ───────────────────────────────────────────────
#
# The one place that knows about tokens. Each surface opens to one calling
# stacklet's token, read from the secret store as `stack up` wrote it.

TOKEN_NAME = "STACK_API_TOKEN"
TOKEN_WORD = "token"
JSON, PLAINTEXT = "JSON", "plaintext"
# The JSON surface starts, stops and configures stacklets and renders their
# environment, so only core's own tools (its tools server and the bots of
# its bot runner) open it. The agent is an LLM: its token opens only the
# plaintext surface, whose allowlist still applies.
SURFACE_CALLER = {JSON: "core", PLAINTEXT: "agent"}
TOKEN_REFUSAL = "the host API answers only a caller that sends its own token"


def admit(data):
    """The request's surface and the request without its token, when the
    token opens that surface; None, after a line in the log, when not.

    The format decides the surface once, here: a JSON object carries the
    token in its field `token`, a plaintext line starts with `token <value> `
    before the command. What comes back no longer holds the token, so no
    reply and no log line can repeat it. The comparison takes constant time,
    and a token the secret store does not have matches nothing.

        admit('{"cmd": "list", "token": "<core\'s>"}')    ("JSON", {"cmd": "list"})
        admit("token <the agent's> memory search tent")   ("plaintext", "memory search tent")
        admit('{"cmd": "list"}')                           None
        admit("token <core's> memory search tent")        None
    """
    if data.startswith("{"):
        surface, request = JSON, _json_object(data)
        token = request.pop("token", None) if request is not None else None
    else:
        word, token, request = (data.split(None, 2) + ["", ""])[:3]
        surface, token = PLAINTEXT, token if word == TOKEN_WORD else None
    if request is not None and _opens(token, SURFACE_CALLER[surface]):
        return surface, request
    # The log is a file on the Mac: it names the refusal, never a token.
    print(f"host API: refused a {surface} request without its caller's token",
          file=sys.stderr, flush=True)
    return None


def _json_object(data):
    """`data` parsed, when it is a JSON object; None when it is not."""
    try:
        request = json.loads(data)
    except json.JSONDecodeError:
        return None
    return request if isinstance(request, dict) else None


def _opens(token, caller):
    """Whether `token` is `caller`'s token, compared in constant time."""
    secrets = TomlSecretStore(_instance_dir() / ".stack" / "secrets.toml").all()
    expected = secrets.get(f"{caller}__{TOKEN_NAME}")
    return (isinstance(token, str) and isinstance(expected, str) and bool(expected)
            and hmac.compare_digest(token.encode(), expected.encode()))


def _token_refusal(data):
    """The refusal in the request's own format; plaintext exits 126 as the
    allowlist's refusals do."""
    if data.startswith("{"):
        return json.dumps({"error": TOKEN_REFUSAL}) + "\n"
    return _with_exit(f"error: {TOKEN_REFUSAL}\n", 126)


# ── Connections ────────────────────────────────────────────────────────────

def handle_client(conn):
    """Handle a single client connection; the reply mirrors the request format.

    Nothing runs before `admit` has seen the caller's token."""
    try:
        chunks = []
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break

        data = b"".join(chunks).decode().strip()
        if not data:
            return

        admitted = admit(data)
        if admitted is None:
            conn.sendall(_token_refusal(data).encode())
            return

        # Consumer-driven output: a JSON envelope gets a JSON reply (the ops
        # tools); a plaintext command line gets plaintext (the agent).
        surface, request = admitted
        if surface == JSON and request.get("stream"):
            _send_each(conn, stream_request(request), to_the_end=request.get("cmd") == "up")
        elif surface == JSON:
            conn.sendall((json.dumps(handle_request(json.dumps(request))) + "\n").encode())
        else:
            conn.sendall(handle_plaintext(request).encode())
    except Exception as e:
        try:
            conn.sendall(json.dumps({"error": str(e)}).encode() + b"\n")
        except Exception:
            pass
    finally:
        conn.close()


def _running_commit() -> str:
    """The checkout's commit as this process starts, as `stack up` stamps a
    container (`git rev-parse --short HEAD`); "unknown" outside a git tree."""
    try:
        r = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((API_HOST, API_PORT))
    sock.listen(5)

    running = True

    def shutdown(*_):
        nonlocal running
        running = False
        sock.close()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    print(f"famstack API listening on {API_HOST}:{API_PORT}", file=sys.stderr)
    # The commit this process runs, for core's start to compare with the
    # checkout: launchd starts it again on its own, from whatever is on disk.
    host_api.record_start(_instance_dir(), _running_commit())
    # The scheduler runs in this process: the one host service that runs the
    # stack CLI, on request and on time.
    stop_scheduler = scheduler.start(Path(os.environ.get("STACK_DIR") or REPO_ROOT).expanduser())

    while running:
        try:
            conn, _ = sock.accept()
            t = threading.Thread(target=handle_client, args=(conn,), daemon=True)
            t.start()
        except OSError:
            break

    stop_scheduler.set()
    print("famstack API stopped", file=sys.stderr)


if __name__ == "__main__":
    main()
