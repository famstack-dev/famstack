"""Start the host-side stack-runner on every `up` (idempotent).

The runner is a small host process the containerized agent reaches over TCP
(host.docker.internal:42099) to run an allowlisted set of `stack` commands - only
the host has the CLI fully configured, and a container cannot run the host binary
directly. This hook makes sure the runner is up; it does nothing if the port is
already listening. A crash is recovered on the next `up`; a launchd job would be
the production-hardening step. See host/README.md.
"""

import os
import socket
import subprocess
import sys
from pathlib import Path

from stack.prompt import done

ADDR = "127.0.0.1:42099"


def _listening(addr: str) -> bool:
    host, port = addr.rsplit(":", 1)
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def run(ctx):
    if _listening(ADDR):
        return
    repo = Path(__file__).resolve().parents[3]
    runner = repo / "stacklets" / "agent" / "host" / "stack_runner.py"
    log_dir = Path(os.environ.get("STACK_DATA_DIR", os.path.expanduser("~/famstack-data"))) / "agent"
    log_dir.mkdir(parents=True, exist_ok=True)
    logf = open(log_dir / "runner.log", "a")
    # start_new_session detaches the runner from the CLI's process group so it
    # survives after `stack up` returns.
    subprocess.Popen(
        [sys.executable, str(runner)],
        cwd=str(repo),
        env=dict(os.environ, STACK_RUNNER_ADDR=ADDR),
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    done("Started the agent's host CLI runner")
