"""Start the famstack API server via launchd.

The API server runs on the host, accepts JSON commands on TCP port 42001,
and calls the stack CLI to execute them. Docker containers (bots, dashboard)
reach it via host.docker.internal:42001.
"""

import time
from pathlib import Path

PLIST_LABEL = "dev.famstack.api"
API_PORT = 42001


def run(ctx):
    repo_root = Path(ctx.stack.root)
    api_script = repo_root / "stacklets" / "core" / "famstack-api.py"
    log_dir = Path(ctx.stack.data) / "core" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    agents_dir = Path.home() / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    # Wrapper script — launchd doesn't inherit PATH
    wrapper = Path(ctx.stack.data) / "core" / "famstack-api"
    wrapper.write_text(
        f"#!/bin/bash\n"
        f'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"\n'
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

    # Check if API is already running and healthy
    import socket as sock
    try:
        s = sock.socket(sock.AF_INET, sock.SOCK_STREAM)
        s.connect(("127.0.0.1", API_PORT))
        s.close()
        ctx.step("famstack API already running")
        return
    except Exception:
        pass

    try:
        ctx.shell(f'launchctl unload "{plist_path}"')
    except RuntimeError:
        pass
    ctx.shell(f'launchctl load "{plist_path}"')

    # Wait for port to be available
    ctx.step("Starting famstack API...")
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            s = sock.socket(sock.AF_INET, sock.SOCK_STREAM)
            s.connect(("127.0.0.1", API_PORT))
            s.close()
            ctx.step("famstack API ready")
            return
        except Exception:
            time.sleep(0.5)

    ctx.step("famstack API may still be starting")
