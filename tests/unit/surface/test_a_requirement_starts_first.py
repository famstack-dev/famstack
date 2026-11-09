"""A requirement starts first: `stack up` sets up what a stacklet needs, when nobody has to answer.

A stacklet names the stacklets it needs in `requires`; the memory needs
Code, which keeps its pages. A requirement that is not set up yet is
started first, in the same run, when its first start asks nothing and
needs no person at the Mac. Its steps show in the run of the stacklet that
was asked for. A requirement that asks questions, or needs the Mac
(`attended_setup`), is still refused with its command, because a run
started from a screen could not answer it. A requirement that another
run is starting is waited for, never refused.

Driven through `stack.cli.main` as the shell runs it, over a made-up
checkout: `wiki` needs `store`, which asks nothing; `notes` needs
`asking`, whose first start asks in the terminal; `album` needs `desk`,
whose setup needs a person at the Mac. Docker is stood in for as in
test_one_run_per_stacklet.py, whose held first run this file reuses for
the concurrent start.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from stack.runs import EXIT_ALREADY_RUNNING

from .test_one_run_per_stacklet import FirstRun, no_docker, stack  # noqa: F401

# Counts its runs, one line each, so a test sees whether store was set up again.
COUNTS_ITS_INSTALLS = """
import os
from pathlib import Path
def run(ctx):
    if os.environ.get("HOLD_RUN"):
        import sys
        ctx.step("Holding the run")
        sys.stdin.readline()
    log = Path(ctx.env["STACK_DATA_DIR_STORE"])
    log.mkdir(parents=True, exist_ok=True)
    with (log / "installs").open("a") as f:
        f.write("installed\\n")
"""

STACKLETS = {
    "store": 'name = "Store"\n[env.defaults]\nSTACK_DATA_DIR_STORE = "{data_dir}/store"\n',
    "wiki": 'name = "Wiki"\nrequires = ["store"]\n',
    "asking": 'name = "Asking"\n',
    "notes": 'name = "Notes"\nrequires = ["asking"]\n',
    "desk": 'name = "Desk"\nattended_setup = true\n',
    "album": 'name = "Album"\nrequires = ["desk"]\n',
}


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """The made-up checkout, nothing set up yet. Returns its directory."""
    from stack import cli
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\ntimezone = "Europe/Berlin"\n')
    (tmp_path / ".stack").mkdir()
    (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')
    for sid, manifest in STACKLETS.items():
        stacklet = tmp_path / "stacklets" / sid
        (stacklet / "hooks").mkdir(parents=True)
        (stacklet / "stacklet.toml").write_text(f'id = "{sid}"\n{manifest}')
        (stacklet / "docker-compose.yml").write_text(f"services:\n  {sid}:\n    image: busybox\n")
    (tmp_path / "stacklets" / "store" / "hooks" / "on_install.py").write_text(COUNTS_ITS_INSTALLS)
    (tmp_path / "stacklets" / "asking" / "hooks" / "on_configure.sh").write_text(
        "#!/bin/sh\nread -r answer\n")
    # `stack list` asks Docker which containers exist: none.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text("#!/bin/sh\necho '[]'\n")
    (bin_dir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STACK_DIR", raising=False)
    monkeypatch.delenv("STACK_RUN_SURFACE", raising=False)
    monkeypatch.setattr(cli, "_notify", lambda _stck, _msg: None)
    return tmp_path


def set_up(capfd, monkeypatch) -> set[str]:
    """The stacklets `stack list` reports as set up."""
    code, out, _ = stack(capfd, monkeypatch, "list", "--json")
    assert code == 0
    return {s["id"] for s in json.loads(out)["stacklets"] if s.get("enabled")}


def installs(root: Path) -> int:
    """How often store's setup ran."""
    log = root / "data" / "store" / "installs"
    return len(log.read_text().splitlines()) if log.exists() else 0


def steps(out: str) -> list[str]:
    """The step texts of a run's events, in order."""
    events = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return [e["data"]["text"] for e in events if e["type"] == "step.started"]


# ── A requirement that asks nothing ──────────────────────────────────────

def test_a_requirement_that_asks_nothing_is_started_first(checkout, capfd, monkeypatch):
    """Given store is not set up, when `stack up wiki` runs, then store is
    set up first in the same run, and both are set up afterwards."""
    code, out, _ = stack(capfd, monkeypatch, "up", "wiki", "--events")

    assert code == 0
    assert installs(checkout) == 1
    assert {"store", "wiki"} <= set_up(capfd, monkeypatch)
    # Store's own steps show in wiki's run: its containers start too.
    assert steps(out).count("Starting containers") == 2


def test_a_requirement_already_set_up_is_left_alone(checkout, capfd, monkeypatch):
    """Resting state: store set up before is not set up again by wiki's start."""
    assert stack(capfd, monkeypatch, "up", "store")[0] == 0

    code, _, _ = stack(capfd, monkeypatch, "up", "wiki")

    assert code == 0
    assert installs(checkout) == 1


# ── A requirement that needs someone ─────────────────────────────────────

@pytest.mark.parametrize(("sid", "needed"), [("notes", "asking"), ("album", "desk")])
def test_a_requirement_that_needs_someone_is_still_refused(checkout, capfd, monkeypatch, sid, needed):
    """Given the requirement asks questions (asking) or needs a person at the
    Mac (desk), then the start is refused and names the requirement, and
    neither is set up."""
    code, out, _ = stack(capfd, monkeypatch, "up", sid, "--events")

    result = json.loads(out.splitlines()[-1])["data"]
    assert code != 0
    assert needed.title() in result["error"]
    assert not {sid, needed} & set_up(capfd, monkeypatch)


# ── A requirement another run is starting ────────────────────────────────

def test_a_requirement_another_run_is_starting_is_waited_for(checkout, capfd, monkeypatch):
    """Given `stack up store` runs in another process, when `stack up wiki`
    starts, then it waits for that run instead of refusing, and store is
    set up once."""
    results: dict = {}
    with FirstRun(checkout, stacklet="store") as first:
        def start_wiki():
            results["wiki"] = stack(capfd, monkeypatch, "up", "wiki")[0]
        wiki = threading.Thread(target=start_wiki)
        wiki.start()
        assert first.finish() == 0
        wiki.join(timeout=60)

    assert results["wiki"] == 0
    assert results["wiki"] != EXIT_ALREADY_RUNNING
    assert installs(checkout) == 1
    assert "wiki" in set_up(capfd, monkeypatch)

