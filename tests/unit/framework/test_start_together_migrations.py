"""Stacklets started together: memory's migrations survive core's recreate.

The setup page's "Start all three" starts photos, documents and memory at
once: three `stack up` runs, each a process of its own. A first run ends by
recreating core (`_refresh_core`), because the bot runner finds a new bot
only when it starts. Core's card door on this Mac is closed while core is
recreated, and memory's migrations write through that door. A migration
that knocks while the door is closed fails, and the bot runner then holds
every bot until a migration that nothing runs again has run.

Each run here is its own process, as on the Mac, so the run locks between
them are the real ones (`stack.runs`). Docker is the stand-in, in each
run's process: a `docker compose up` of core closes the door for a moment,
as a recreate does. The door is a stand-in on localhost that answers 200,
and closes the connection without an answer while core is down, which is
what the Mac's door did. memory is fabricated: a health check through the
door, an `on_start_ready` that lasts until core's recreate has begun, and
one migration that asks the door through the memory's client. Started
together, memory starts a moment ahead, and photos and docs start once it
is healthy: the laptop's order, where memory was healthy when the
documents start recreated core.

What the bot runner waits on is `Stack.unmigrated()`: the records alone,
read as the runner reads them.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# How long core's door stays closed while core is recreated. A real
# recreate takes seconds; this one is long enough that a run which does not
# wait for core knocks while it is closed.
RECREATE_SECONDS = 1.0

# How long memory's start stays busy at most, waiting for a recreate of
# core to begin: longer than two recreates' head start, short enough to
# cost little when none comes.
MEMORY_BUSY_SECONDS = 2

# One `stack up <id>` run, as `./stack up <id>` runs it, with Docker
# answered in its own process: core runs, no container carries a commit
# stamp, and a `compose up` of core is a recreate that closes the door.
RUN = textwrap.dedent("""
    import argparse, sys, time
    from pathlib import Path
    from unittest.mock import patch

    from stack import Stack, docker
    from stack.cli import handle_up
    from stack.output import TerminalOutput

    root, stacklet, recreate = Path(sys.argv[1]), sys.argv[2], float(sys.argv[3])

    def compose_up(compose_file, env=None):
        if Path(compose_file).parent.name == "core":
            # Down until then. Compose returns once the containers exist,
            # halfway; core's card door answers a moment later.
            (root / "core-down").write_text(str(time.time() + recreate))
            time.sleep(recreate / 2)
        return 0, ""

    # The health checks' poll interval, shortened: the stand-in's core
    # boots in a fraction of a second, not in the seconds the real one takes.
    real_wait = docker.wait_for_health
    def wait_for_health(url, timeout=120, interval=3, headers=None):
        return real_wait(url, timeout=timeout, interval=0.05, headers=headers)


    with patch.multiple(docker,
                        ensure_network=lambda: ("ok", None),
                        compose_pull=lambda *a, **k: (0, ""),
                        compose_build=lambda *a, **k: (0, ""),
                        compose_up=compose_up,
                        wait_for_health=wait_for_health,
                        running_project_ids=lambda: {"core"},
                        container_commits=lambda: {}):
        stack = Stack(root=root, data=root / "data", output=TerminalOutput())
        handle_up(stack, argparse.Namespace(stacklet=[stacklet], no_voice=False))
""")

# memory's start: healthy through the door, then, when the runs start
# together, busy until a recreate of core has begun, as on the laptop, for
# MEMORY_BUSY_SECONDS at most: with the runs in a row none begins while
# memory runs. Then its migration, which reads the curator's state
# through core's door with the client every memory migration uses.
ON_START_READY = textwrap.dedent("""
    import time
    from pathlib import Path

    def run(ctx):
        root = Path(ctx.stack.root)
        (root / "memory-ready").touch()
        if not (root / "together").exists():
            return
        deadline = time.monotonic() + {busy}
        while not (root / "core-down").exists() and time.monotonic() < deadline:
            time.sleep(0.005)
""").replace("{busy}", str(MEMORY_BUSY_SECONDS))

MIGRATION = textwrap.dedent('''
    """Give every note its sources."""
    from pathlib import Path
    from stack.memory import MemoryAPI

    def run(ctx):
        MemoryAPI.from_hook(ctx).pending()
        with (Path(ctx.stack.root) / "migrated").open("a") as f:
            f.write("memory/0001\\n")
''')


# ── Core's door, on localhost ────────────────────────────────────────────

def _down(root: Path) -> bool:
    """Whether core is being recreated: `core-down` names the time it is back."""
    try:
        return time.time() < float((root / "core-down").read_text() or 0)
    except (FileNotFoundError, ValueError):
        return False


class _Door(BaseHTTPRequestHandler):
    """Core on localhost: its dashboard's health check and its card door.
    Answers every GET with 200 and an empty state; while `core-down`
    exists, the card door (`/cards/`) closes the connection without an
    answer, and the dashboard answers, as the Mac's core did within a
    second of its recreate."""

    root: Path

    def do_GET(self) -> None:
        if self.path.startswith("/cards/") and _down(self.root):
            self.close_connection = True
            return
        body = b'{"state": "ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def door(tmp_path):
    handler = type("Door", (_Door,), {"root": tmp_path})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()
    thread.join()


# ── The instance ─────────────────────────────────────────────────────────

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _instance(root: Path, port: int) -> None:
    """Core set up and running; photos, docs and memory never started."""
    door = f"http://127.0.0.1:{port}"
    _write(root / "stack.toml",
           f'[core]\ntimezone = "Europe/Berlin"\ncards_port = {port}\n')
    _write(root / ".stack" / "secrets.toml", 'global__ADMIN_PASSWORD = "donuts"\n')
    (root / ".stack" / "core.setup-done").touch()
    core = root / "stacklets" / "core"
    _write(core / "stacklet.toml",
           f'id = "core"\nname = "core"\n\n[health]\nurl = "{door}/dashboard/api/health"\n'
           'expect = "200"\n')
    _write(core / "docker-compose.yml", "services: {}\n")
    for sid in ("photos", "docs"):
        _write(root / "stacklets" / sid / "stacklet.toml", f'id = "{sid}"\nname = "{sid}"\n')
    memory = root / "stacklets" / "memory"
    _write(memory / "stacklet.toml",
           f'id = "memory"\nname = "memory"\n\n[health]\nurl = "{door}/cards/api/pending"\n'
           'expect = "200"\n')
    _write(memory / "hooks" / "on_start_ready.py", ON_START_READY)
    _write(memory / "migrations" / "0001_give_notes_their_sources.py", MIGRATION)


def _start(root: Path, stacklet: str) -> subprocess.Popen:
    """`stack up <stacklet>` in a process of its own, as the host API starts it."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root),
           "LANG": "C.UTF-8", "PYTHONPATH": str(REPO_ROOT / "lib"),
           "STACK_RUN_SURFACE": "dashboard"}
    return subprocess.Popen(
        [sys.executable, "-c", RUN, str(root), stacklet, str(RECREATE_SECONDS)],
        cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _ended(run: subprocess.Popen) -> tuple[int, str]:
    out, _ = run.communicate(timeout=60)
    return run.returncode, out


def _until_exists(path: Path, run: subprocess.Popen) -> None:
    """Return once `path` exists; fail when `run` ended before it did."""
    deadline = time.monotonic() + 30
    while not path.exists():
        assert run.poll() is None, _ended(run)
        assert time.monotonic() < deadline, f"{path.name} never came"
        time.sleep(0.01)


def _unmigrated(root: Path) -> list[str]:
    """What the bot runner waits for, read as it reads it."""
    from stack import Stack
    from stack.output import CollectorOutput
    return Stack(root=root, data=root / "data", output=CollectorOutput()).unmigrated()


def _migrated(root: Path) -> list[str]:
    path = root / "migrated"
    return path.read_text().splitlines() if path.exists() else []


THREE = ("photos", "docs", "memory")


def test_started_together_memory_migrates_and_the_bots_go(tmp_path, door):
    """Given a fresh install, when photos, docs and memory start together
    and the first two recreate core while memory comes up, then every run
    succeeds, memory's migration runs once, and nothing is left for the
    bot runner to wait for.

    The setup page's "Start all three" does this; before, memory's
    migration knocked on core's closed door and failed for good.
    """
    _instance(tmp_path, door)

    (tmp_path / "together").touch()
    memory = _start(tmp_path, "memory")
    _until_exists(tmp_path / "memory-ready", memory)
    others = {sid: _start(tmp_path, sid) for sid in ("photos", "docs")}
    ended = {sid: _ended(run) for sid, run in {**others, "memory": memory}.items()}

    assert {sid: code for sid, (code, _) in ended.items()} == dict.fromkeys(THREE, 0), ended
    assert _migrated(tmp_path) == ["memory/0001"]
    assert _unmigrated(tmp_path) == []


def test_a_start_that_recreates_core_ends_once_the_card_door_answers(tmp_path, door):
    """Given memory runs, when docs' first start recreates core, then the
    start ends only once core's card door answers again, not when core's
    own health check first does: the next run in the queue (memory's
    migrations through the door, a bot-runner command) finds core back.
    """
    _instance(tmp_path, door)
    (tmp_path / ".stack" / "memory.setup-done").touch()
    (tmp_path / ".stack" / "memory.up").write_text("unknown\n")

    code, out = _ended(_start(tmp_path, "docs"))

    assert code == 0, out
    with urllib.request.urlopen(f"http://127.0.0.1:{door}/cards/api/pending", timeout=5) as answer:
        assert answer.status == 200


def test_started_one_by_one_memory_migrates_and_the_bots_go(tmp_path, door):
    """Given a fresh install, when photos, docs and memory start one after
    the other, each after the previous run ended, then memory's migration
    runs once and nothing is left for the bot runner to wait for.

    Each run recreates core before it ends, so no recreate overlaps
    memory's run.
    """
    _instance(tmp_path, door)

    for sid in THREE:
        code, out = _ended(_start(tmp_path, sid))
        assert code == 0, (sid, out)

    assert _migrated(tmp_path) == ["memory/0001"]
    assert _unmigrated(tmp_path) == []
