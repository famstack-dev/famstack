"""Start the famstack API server via launchd.

The API server runs on the host, accepts JSON commands on TCP port 42001,
and calls the stack CLI to execute them. Docker containers (bots, dashboard)
reach it via host.docker.internal:42001.

Every core start writes the wrapper and the plist, then decides:

    nothing answers on the port         load the plist, wait for the port
    it answers, on the checkout's code  leave it running, so the
                                        dashboard's live runs keep going
    it answers, on older code           unload and load it again: "Restarted
                                        the famstack API: its code changed"
    it answers, but wrote no stamp      the same: "Restarted the famstack
                                        API: it predates the code stamp"

launchd never restarts a running process because its code changed on disk,
so without the third case an update left the old host API answering until
the Mac restarted. Whether the code is older is `Stack.host_api_stale()`,
from the commit the host API noted at its start; this hook only acts on it.
"""

import os
import socket
import time
from pathlib import Path

from stack import host_api

PLIST_LABEL = "dev.famstack.api"
# The host API listens where STACK_API_PORT says, 42001 by default; the
# hook reads the same variable, so it asks the port the API listens on.
API_PORT = int(os.environ.get("STACK_API_PORT", "42001"))
START_SECONDS = 5


def run(ctx):
    # The bot runner bind-mounts the extension tree, so the source has to
    # exist before compose starts. Created here rather than left to the
    # Docker daemon, which would make it root-owned on some runtimes. On
    # an instance with no extensions this is the empty stand-in, not a
    # directory in the admin's home.
    ctx.stack.extension_mount_source.mkdir(parents=True, exist_ok=True)

    repo_root = Path(ctx.stack.root)
    api_script = repo_root / "stacklets" / "core" / "famstack-api.py"
    log_dir = Path(ctx.stack.data) / "core" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    agents_dir = Path.home() / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    # Wrapper script — launchd doesn't inherit PATH. Every command the API
    # runs gets this one, hooks included, and they call macOS's own tools
    # from the system folders (`mount` in /sbin, `diskutil` in /usr/sbin).
    wrapper = Path(ctx.stack.data) / "core" / "famstack-api"
    wrapper.write_text(
        f"#!/bin/bash\n"
        f'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"\n'
        f'export PYTHONPATH="{repo_root}/lib"\n'
        f'exec python3 "{api_script}"\n'
    )
    wrapper.chmod(0o755)

    plist_path = agents_dir / f"{PLIST_LABEL}.plist"
    plist_path.write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        f'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        f'<plist version="1.0">\n'
        f'<dict>\n'
        f'  <key>Label</key>\n'
        f'  <string>{PLIST_LABEL}</string>\n'
        f'  <key>RunAtLoad</key>\n'
        f'  <true/>\n'
        f'  <key>KeepAlive</key>\n'
        f'  <true/>\n'
        f'  <key>ProgramArguments</key>\n'
        f'  <array>\n'
        f'    <string>{wrapper}</string>\n'
        f'  </array>\n'
        f'  <key>StandardOutPath</key>\n'
        f'  <string>{log_dir}/famstack-api.log</string>\n'
        f'  <key>StandardErrorPath</key>\n'
        f'  <string>{log_dir}/famstack-api.log</string>\n'
        f'</dict>\n'
        f'</plist>\n'
    )

    if not _answers():
        _load(ctx, plist_path)
        _wait_for_the_port(ctx, "famstack API ready")
    elif not host_api.has_stamp(ctx.stack.instance_dir):
        # A host API from before the stamp runs code nobody can name, and
        # the update to the stamp is the one that brings the token check.
        _load(ctx, plist_path)
        _wait_for_the_port(ctx, "Restarted the famstack API: it predates the code stamp")
    elif ctx.stack.host_api_stale():
        _load(ctx, plist_path)
        _wait_for_the_port(ctx, "Restarted the famstack API: its code changed")
    else:
        ctx.step("famstack API already running")


# ── launchd and the port ─────────────────────────────────────────────────

def _answers() -> bool:
    """Whether something accepts a connection on the host API's port."""
    try:
        with socket.create_connection(("127.0.0.1", API_PORT), timeout=1):
            return True
    except OSError:
        return False


def _load(ctx, plist_path: Path) -> None:
    """(Re)load the host API's plist; a running one is stopped first.

    `unload` fails when launchd has no such job yet, which is the first
    start, so its failure is not one."""
    try:
        ctx.shell(f'launchctl unload "{plist_path}"')
    except RuntimeError:
        pass
    ctx.shell(f'launchctl load "{plist_path}"')


def _wait_for_the_port(ctx, ready: str) -> None:
    """Wait up to START_SECONDS for the host API; say `ready` once it answers."""
    ctx.step("Starting famstack API...")
    deadline = time.time() + START_SECONDS
    while time.time() < deadline:
        if _answers():
            ctx.step(ready)
            return
        time.sleep(0.5)
    ctx.step("famstack API may still be starting")
