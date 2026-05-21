"""famstack API — Unix socket server wrapping the stack CLI.

Runs on the host as a launchd service. Accepts JSON commands on a Unix
socket, shells out to ./stack with --json, returns the result. This is
the bridge between Docker containers (bots, dashboard) and the host.

Protocol:
  Client sends a JSON object followed by a newline.
  Server responds with a JSON object followed by a newline.

Commands:
  {"cmd": "status"}                     → stack status --json
  {"cmd": "list"}                       → stack list --json
  {"cmd": "up", "stacklet": "photos"}   → stack up photos --json
  {"cmd": "down", "stacklet": "photos"} → stack down photos --json
  {"cmd": "logs", "stacklet": "photos"}  → stack logs photos --json --tail 200
  {"cmd": "logs", "stacklet": "photos", "grep": "ERROR"}
                                        → stack logs photos --json --tail 200 --grep ERROR

The socket path defaults to /tmp/famstack.sock. Bind-mount it into
containers that need host access.
"""

import json
import os
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


def handle_client(conn):
    """Handle a single client connection."""
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

        result = handle_request(data)
        response = json.dumps(result) + "\n"
        conn.sendall(response.encode())
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
