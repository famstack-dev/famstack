"""famstack API — the host's single CLI bridge for containers.

Runs on the host as a launchd service (core owns it). It is the one bridge
between Docker containers and the host CLI, and its output mirrors the request,
so every consumer gets back what it speaks:

  * JSON object in  → runs `./stack <cmd> --json`  → JSON out.
    A lifecycle surface (status/list/up/down/logs) for trusted core tools —
    the stacker bot and the tools-server.
  * plaintext line in → runs `./stack <args>` in text mode → plaintext out.
    A curated read/domain surface (memory, docs) for the agent, which wants a
    token-lean answer, not JSON, and must never reach lifecycle ops.

Format is auto-detected: a request that begins with `{` is JSON, anything else
is a plaintext command line. One service, two allowlists scoped by trust.

Protocol (JSON):  client sends a JSON object + newline; server replies JSON + newline.
Protocol (text):  client sends a command line; server replies plaintext, then closes.
"""

import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

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
    ["memory", "person"],
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
    # What changed, when, and who did it. The vault is a git repo and has
    # always known this; nothing read it back until now.
    ["memory", "history"],
    ["docs", "show"],
]

# Reads under an allowed prefix that are actually writes. `memory topic <slug>
# todo` is a read the agent needs, but `todo add|strike` under the same prefix
# is a second way to change a list, and given both the model picks the per-item
# verb even for a structural edit -- asked to split a list in two it ticked off
# two unrelated items and described a split that never happened. People and
# scripts keep these verbs on the host CLI, where a deterministic non-LLM path
# is worth having; the agent gets one way, and the refusal says which.
DOMAIN_DENY = [
    (("memory", "topic"), ("add", "strike", "unstrike")),
]
DENY_HINT = (
    "error: the agent does not change lists item by item. Read the page with "
    "read_file on vault/family/<topic>/todos.md, then write_file the complete "
    "new contents to the same path. Ticking off is '- [x]'; a second list is a "
    "'## ' heading.\n"
)


def _is_denied_write(args):
    """True when an allowed prefix is being used for a write the agent owns
    through the page-rewrite path instead."""
    return any(
        tuple(args[:len(prefix)]) == prefix and any(v in args for v in verbs)
        for prefix, verbs in DOMAIN_DENY
    )


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
                "up":       {"needs_stacklet": True,  "description": "Start a stacklet"},
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


def handle_plaintext(line):
    """Run one allowlisted `stack` command in the CLI's text mode.

    The token-lean counterpart to `handle_request`: the agent sends a plaintext
    command line and gets the CLI's normal text output back (no `--json`), so its
    context stays small. Confined to `DOMAIN_ALLOW` -- never lifecycle ops.

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
    if not any(args[:len(p)] == p for p in DOMAIN_ALLOW):
        allowed = ", ".join(" ".join(p) for p in DOMAIN_ALLOW)
        return _with_exit(
            f"error: '{' '.join(args[:2])}' is not allowed. Allowed: {allowed}\n", 126,
        )
    if _is_denied_write(args):
        return _with_exit(DENY_HINT, 126)
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
        if data.startswith("{"):
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

    while running:
        try:
            conn, _ = sock.accept()
            t = threading.Thread(target=handle_client, args=(conn,), daemon=True)
            t.start()
        except OSError:
            break

    print("famstack API stopped", file=sys.stderr)


if __name__ == "__main__":
    main()
