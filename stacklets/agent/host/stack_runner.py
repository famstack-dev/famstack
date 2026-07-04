#!/usr/bin/env python3
"""Host-side runner: an allowlisted, plaintext bridge from the containerized
agent to the `stack` CLI on the host.

The agent (nanobot, in its container) cannot run the host `stack` binary
directly, and the CLI only works with the host's full config. So this small
process listens on a unix socket, and for each request:

  1. reads one plaintext command line (a `stack` subcommand + args),
  2. checks it against an allowlist (the agent is bound to sanctioned commands,
     never arbitrary shell),
  3. runs `./stack <args>` here on the host,
  4. returns stdout verbatim.

Why a unix socket and plaintext (not HTTP or MCP): plaintext is token-lean for
an LLM (the CLI already prints for humans, no JSON tax), a socket is faster and
simpler than an HTTP service, and there is no protocol overhead. The socket is
bind-mounted into the agent container; a thin `stack` wrapper there forwards to
it (see the container-side client).

Read-only vault queries only, for now. Actions (e.g. striking a todo) get added
to ALLOW deliberately, one reviewed command at a time.
"""

import os
import socket
import subprocess
from pathlib import Path

# stacklets/agent/host/stack_runner.py -> repo root is three parents up.
REPO = Path(__file__).resolve().parents[3]

# We listen on a loopback TCP port, not a unix socket. A socket file placed in a
# bind-mounted directory does NOT bridge the container<->host boundary on macOS
# (Docker Desktop / OrbStack): the container sees the file but connecting to it
# never reaches the host listener. Containers reach host-native loopback services
# through host.docker.internal (the same path famstack uses for oMLX and whisper),
# so the runner binds 127.0.0.1 and the container connects to
# host.docker.internal:PORT. It is still a raw plaintext line protocol - no HTTP,
# no JSON - so it stays token-lean.
HOST, PORT = os.environ.get("STACK_RUNNER_ADDR", "127.0.0.1:42099").rsplit(":", 1)

# Each entry is a command prefix the agent may run. A request is allowed only if
# its leading tokens match one of these exactly.
ALLOW = [
    ["memory", "search"],
    ["memory", "topic"],
    ["memory", "lookup"],
    ["memory", "correspondents"],
]

MAX_REQUEST = 8192
CMD_TIMEOUT = 30


def is_allowed(args: list[str]) -> bool:
    return any(args[: len(p)] == p for p in ALLOW)


def handle(line: str) -> str:
    """Validate one command line and run it, returning plaintext output."""
    args = line.strip().split()
    if not args:
        return "error: empty command\n"
    if not is_allowed(args):
        allowed = ", ".join(" ".join(p) for p in ALLOW)
        return f"error: '{' '.join(args[:2])}' is not allowed. Allowed: {allowed}\n"
    try:
        r = subprocess.run(
            ["./stack", *args], cwd=REPO, capture_output=True, text=True, timeout=CMD_TIMEOUT,
        )
        return r.stdout or r.stderr or "(no output)\n"
    except subprocess.TimeoutExpired:
        return "error: command timed out\n"
    except Exception as e:  # never crash the runner on one bad request
        return f"error: {e}\n"


def serve() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, int(PORT)))
    srv.listen(8)
    print(f"[stack-runner] listening on {HOST}:{PORT}", flush=True)
    while True:
        conn, _ = srv.accept()
        with conn:  # closes the connection on every path
            try:
                data = conn.recv(MAX_REQUEST).decode(errors="replace")
                conn.sendall(handle(data).encode())
                conn.shutdown(socket.SHUT_WR)  # flush + signal EOF so the client's read loop ends
            except OSError:
                pass  # client hung up
            except Exception as e:
                try:
                    conn.sendall(f"error: {e}\n".encode())
                except OSError:
                    pass


if __name__ == "__main__":
    serve()
