"""famstack API — the host's single CLI bridge for containers.

Runs on the host as a launchd service (core owns it). It is the one bridge
between Docker containers and the host CLI, and its output mirrors the request,
so every consumer gets back what it speaks:

  * JSON object in  → runs `./stack <cmd> --json`  → JSON out.
    A lifecycle surface (status/list/up/down/logs) for trusted core tools —
    the stacker bot and the tools-server.
  * plaintext line in → runs `./stack <args>` in text mode → plaintext out.
    A curated read/domain surface (memory, docs, and the stacklet commands
    in stack.toml `[agent] allow`) for the agent, which wants a token-lean
    answer, not JSON, and must never reach lifecycle ops.

Format is auto-detected: a request that begins with `{` is JSON, anything else
is a plaintext command line. One service, two allowlists scoped by trust.

Protocol (JSON):  client sends a JSON object + newline; server replies JSON + newline.
                  With `"stream": true` (`up` only) the server sends each event
                  line of the run as the CLI writes it, then closes after the
                  final `result` (ADR-020).
Protocol (text):  client sends a command line; server replies plaintext, then closes.
"""

import json
import os
import queue
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
from stack.output import event_line
from stack.stack import Stack, configured_data_dir
from stack.stacklet_code import import_stacklets

# The API runs the stack's own stacklets, from the checkout it is in.
import_stacklets([Path(__file__).resolve().parent.parent])
from stacklets.core import scheduler  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STACK_BIN = REPO_ROOT / "stack"
API_HOST = os.environ.get("STACK_API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("STACK_API_PORT", "42001"))

ALLOWED_COMMANDS = {"status", "list", "config", "up", "down", "restart", "env", "logs", "discover"}
NEEDS_STACKLET = {"up", "down", "restart", "env", "logs"}

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


def schedule_refusal(args, domain_allow):
    """Why the agent may not run this `core schedule` line, or None.

    The scheduler runs a stacklet command later, as the admin. Through the
    agent it runs only what the agent may run now: a command it is allowed,
    or a message it sends as itself.
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
    command = args[args.index("--") + 1:] if "--" in args else []
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
                "up":       {"needs_stacklet": True,  "description": "Start a stacklet", "options": ["stream"]},
                "down":     {"needs_stacklet": True,  "description": "Stop a stacklet"},
                "restart":  {"needs_stacklet": True,  "description": "Restart a stacklet"},
                "env":      {"needs_stacklet": True,  "description": "Render environment variables"},
                "logs":     {"needs_stacklet": True,  "description": "Get container logs", "options": ["tail", "grep"]},
            },
        }

    sid = req.get("stacklet", "")
    if cmd in NEEDS_STACKLET and not sid:
        return {"error": f"'{cmd}' requires a 'stacklet' field"}

    # Build the CLI command
    args = [str(STACK_BIN), cmd, "--json"]
    if sid:
        args.insert(2, sid)

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
            args, capture_output=True, text=True, timeout=300,
            cwd=str(REPO_ROOT),
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


# ── A run followed while it happens ──────────────────────────────────────
# A JSON request with "stream": true gets the run's event lines as the CLI
# writes them (`stack <cmd> <id> --events`), then the connection closes after
# the final `result`. Without it the reply stays one line, which is all the
# tools server and the stacker bot read.

# The commands a caller may follow while they run. The one place that widens
# streaming: a command added here has to write events with `--events`, as
# `stack up` does.
STREAMED_COMMANDS = {"up"}

# How long a streamed run may say nothing before the API ends it. Not a limit
# on the run: one that keeps reporting goes on as long as it needs, image
# pulls and model downloads included. The longest `stack up` stays quiet is a
# hook's command, which the CLI stops itself after 1200 s (`ctx.shell` and
# its siblings in stack/hooks.py; image pulls stop at 600 s, health checks at
# their manifest's timeout). A minute more lets those fire first, so the CLI
# reports its own error, and this only ends a run that hangs past all of them.
STREAM_SILENCE_SECS = 1200 + 60

# How much of the CLI's stderr a result written by the API carries: the end
# of a traceback says why the run fell over, the rest is progress.
STDERR_TAIL_LINES = 5


def _wants_stream(data):
    """The request, when it asks for a stream; None for every other request."""
    try:
        req = json.loads(data)
    except json.JSONDecodeError:
        return None
    return req if isinstance(req, dict) and req.get("stream") else None


def stream_request(req):
    """The reply to a request with "stream": true, each line as it exists.

    A command that does not stream is refused with one line, as any other
    refusal is. Otherwise the run's own event lines. When the run ends
    without writing its `result`, because it fell over or fell silent, the
    API writes one, so the last line a caller reads is always a result.
    """
    cmd = req.get("cmd", "")
    if cmd not in STREAMED_COMMANDS:
        allowed = ", ".join(sorted(STREAMED_COMMANDS))
        yield json.dumps({"error": f"'{cmd}' does not stream; only {allowed} does"}) + "\n"
        return
    sid = req.get("stacklet", "")
    if not sid:
        yield json.dumps({"error": f"'{cmd}' requires a 'stacklet' field"}) + "\n"
        return
    yield from _relay([str(STACK_BIN), cmd, sid, "--events"], f"stack {cmd} {sid}")


def _relay(argv, what):
    """Run `argv` and yield each line it writes on stdout, ending with a result.

    The run gets its own session, so ending it ends what it started too
    (compose, a hook's command).
    """
    with tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=stderr,
            encoding="utf-8", errors="replace",
            cwd=str(REPO_ROOT), start_new_session=True,
        )
        ended, last_id = yield from _forward(proc.stdout)
        if ended == SILENT:
            _end(proc)
        proc.wait()
        if ended == RESULT:
            return
        error = (f"{what}: stopped hearing from it for {STREAM_SILENCE_SECS} s, so ended it"
                 if ended == SILENT else f"{what} ended without a result (exit {proc.returncode})")
        yield event_line(last_id + 1, "result", {"error": error, "output": _tail(stderr)})


# How `_forward` saw a run end: with its result, with its stdout closed
# before one, or with nothing written for STREAM_SILENCE_SECS.
RESULT, CLOSED, SILENT = "result", "closed", "silent"


def _forward(stream):
    """Yield each line of `stream` up to the run's `result`; return how it
    ended and the last event id.

    A reader thread hands the lines over, so waiting for the next one can
    time out: silence is how a hung run shows.
    """
    lines = queue.Queue()
    threading.Thread(target=_pump, args=(stream, lines), daemon=True).start()
    last_id = 0
    while True:
        try:
            line = lines.get(timeout=STREAM_SILENCE_SECS)
        except queue.Empty:
            return SILENT, last_id
        if line is None:
            return CLOSED, last_id
        yield line
        last_id, event_type = _id_and_type(line, last_id)
        if event_type == "result":
            return RESULT, last_id


def _pump(stream, lines):
    """Put each line of `stream` on `lines`, then None once it closes."""
    for line in stream:
        lines.put(line)
    lines.put(None)


def _id_and_type(line, last_id):
    """The `id` and `type` of an event line; the API reads nothing else of it."""
    try:
        event = json.loads(line)
        return event.get("id", last_id), event.get("type")
    except (json.JSONDecodeError, AttributeError):
        return last_id, None


def _end(proc):
    """Stop a run and everything in its session: politely, then for good."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    except ProcessLookupError:
        proc.wait()


def _tail(stderr):
    """The last lines the run wrote on stderr."""
    stderr.seek(0)
    text = stderr.read().decode(errors="replace").strip()
    return "\n".join(text.splitlines()[-STDERR_TAIL_LINES:])


def _send_each(conn, lines):
    """Send each line as it comes.

    A caller that hangs up does not stop the run (ADR-020: the state lives
    in the run, not in the connection). Its lines are still read to the
    end, so it finishes, is reaped, and is still ended if it falls silent.
    """
    connected = True
    for line in lines:
        if not connected:
            continue
        try:
            conn.sendall(line.encode())
        except OSError:
            connected = False


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


# Options that name a path on the host. This API runs on the host, and the
# agent's lines are written by a model that reads email and web pages, so a
# path would let it read, or file into the memory, any file on the Mac.
HOST_PATH_OPTIONS = {"--vault", "--file"}
# Commands whose arguments are a path on the host, whoever allowed them.
HOST_PATH_COMMANDS = [["messages", "upload"]]


def _names_a_host_path(args):
    return (any(a.split("=", 1)[0] in HOST_PATH_OPTIONS for a in args)
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
    if _names_a_host_path(args):
        return _with_exit("error: a path on the host is not allowed from the agent "
                          "(--vault, --file, messages upload)\n", 126)
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


def handle_client(conn):
    """Handle a single client connection; the reply mirrors the request format."""
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

        # Consumer-driven output: a JSON envelope gets a JSON reply (the ops
        # tools); a plaintext command line gets plaintext (the agent).
        if data.startswith("{") and (req := _wants_stream(data)) is not None:
            _send_each(conn, stream_request(req))
        elif data.startswith("{"):
            conn.sendall((json.dumps(handle_request(data)) + "\n").encode())
        else:
            conn.sendall(handle_plaintext(data).encode())
    except Exception as e:
        try:
            conn.sendall(json.dumps({"error": str(e)}).encode() + b"\n")
        except Exception:
            pass
    finally:
        conn.close()


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
