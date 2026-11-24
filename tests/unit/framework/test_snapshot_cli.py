"""`stack snapshot create <id>` and `stack snapshot list`, as the admin runs them.

Before a change, the admin takes a snapshot of a stacklet: the stack
stops it, clones its state into `.snapshots/<time>/`, writes the manifest
and starts it again. `stack snapshot list` shows the snapshots. These
tests run `./stack` as the admin does, as a process of its own
(`python -m stack`), from a fabricated checkout with one stacklet, `docs`.

Docker is the stand-in `docker_with_projects`, which follows `compose up`
and `compose stop` as a real daemon does, so the stack sees `docs` stop
and start again. `tmutil` is a stand-in that writes down its arguments,
so no test marks a folder of this Mac for Time Machine. The data folder
and the instance are real files in the test's folder.

Each `./stack` command is a process that starts in about 0.3 seconds, so
a test that runs three or four of them takes one to two seconds.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

# How long one `./stack` command may take here. A snapshot of the small
# test folder takes well under a second.
STACK_SECONDS = 60

DOCS_TOML = """
id = "docs"
name = "Documents"
category = "test"

[[backup.archive]]
name = "media"
path = "{data_dir}/docs/paperless/media"
"""

DOCS_COMPOSE = """
name: stack-docs
services:
  paperless:
    image: ghcr.io/paperless-ngx/paperless-ngx:2.20.1
    container_name: stack-docs-paperless
"""


@dataclass
class Ran:
    code: int
    out: str
    err: str

    def json(self) -> dict:
        return json.loads(self.out)


class Checkout:
    """A checkout with the docs stacklet set up, its instance and its data.

    `stack(...)` runs `./stack` against it, as the admin does.
    """

    def __init__(self, root: Path, monkeypatch, docker):
        self.root = root / "checkout"
        self.instance = root / "instance"
        self.data = root / "data"
        self.docker = docker
        docs = self.root / "stacklets" / "docs"
        docs.mkdir(parents=True)
        (docs / "stacklet.toml").write_text(DOCS_TOML)
        (docs / "docker-compose.yml").write_text(DOCS_COMPOSE)
        (self.instance / ".stack").mkdir(parents=True)
        (self.instance / ".stack" / "docs.setup-done").touch()
        (root / "extensions").mkdir()
        (self.instance / "stack.toml").write_text(
            f'[core]\nname = "stack"\ndomain = ""\nhost = "127.0.0.1"\n'
            f'data_dir = "{self.data}"\nextension_dirs = ["{root / "extensions"}"]\n'
            'timezone = "America/Chicago"\nlanguage = "en"\n')
        (self.instance / "users.toml").write_text('[[users]]\nid = "homer"\nrole = "admin"\n')
        (self.instance / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "donuts"\n')
        live = self.data / "docs"
        (live / "postgres").mkdir(parents=True)
        (live / "postgres" / "base.dat").write_text("homer's invoices")
        (live / "paperless" / "media").mkdir(parents=True)
        (live / "paperless" / "media" / "dentist-bill.pdf").write_text("%PDF")
        monkeypatch.setenv("STACK_DIR", str(self.instance))

    def stack(self, *args: str) -> Ran:
        """`./stack <args>` as a process of its own, from the checkout's
        folder, with the stand-ins first on `PATH`."""
        done = subprocess.run([sys.executable, "-m", "stack", *args], cwd=self.root,
                              env={**os.environ, "PYTHONPATH": str(REPO / "lib")},
                              capture_output=True, text=True, timeout=STACK_SECONDS)
        return Ran(done.returncode, done.stdout, done.stderr)

    def snapshot_folders(self) -> list[str]:
        folder = self.data / ".snapshots"
        return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


@pytest.fixture
def checkout(tmp_path, monkeypatch, docker_with_projects) -> Checkout:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tmutil").write_text(f'#!/bin/sh\necho "$@" >> "{bin_dir / "tmutil.log"}"\n')
    (bin_dir / "tmutil").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir), prepend=os.pathsep)
    docker = docker_with_projects(docs="running")
    docker.follows_compose()
    return Checkout(tmp_path, monkeypatch, docker)


# ── Taking a snapshot ────────────────────────────────────────────────────

def test_create_stops_docs_clones_its_state_and_starts_it_again(checkout):
    """Given the running docs stacklet, when the admin runs `stack snapshot
    create docs`, then the stack stops docs, clones its data folder and the
    configuration into `.snapshots/<time>/`, writes the manifest, and
    starts docs again."""
    ran = checkout.stack("snapshot", "create", "docs", "--json")

    assert ran.code == 0, ran.err
    answer = ran.json()
    folder = checkout.data / ".snapshots" / answer["snapshot"]
    assert (folder / "docs" / "postgres" / "base.dat").read_text() == "homer's invoices"
    assert (folder / "stack" / "stack.toml").is_file()
    assert (folder / "stack" / "users.toml").is_file()
    assert (folder / "stack" / ".stack" / "secrets.toml").is_file()
    manifest = json.loads((folder / "MANIFEST.json").read_text())
    assert manifest["kind"] == "snapshot"
    assert manifest["stacklets"]["docs"]["up"] is True
    assert "paperless" in manifest["stacklets"]["docs"]["images"]
    # Stopped before the clone, started after it.
    steps = checkout.docker.compose_steps()
    assert steps.index("stop") < steps.index("up")
    assert checkout.docker.state_of("docs") == "running"
    assert answer["started"] == ["docs"]


def test_create_leaves_out_the_archive_sources(checkout):
    """Given docs, which declares its originals as `[[backup.archive]]`,
    when the admin takes its snapshot, then the snapshot has no file of
    them."""
    answer = checkout.stack("snapshot", "create", "docs", "--json").json()

    folder = checkout.data / ".snapshots" / answer["snapshot"]
    assert not (folder / "docs" / "paperless" / "media").exists()


def test_a_stopped_stacklet_is_not_started(checkout):
    """Given a stopped docs, when the admin takes its snapshot, then the
    snapshot is taken and docs stays stopped."""
    checkout.docker.running(docs="stopped")
    checkout.docker.forget()

    ran = checkout.stack("snapshot", "create", "docs", "--json")

    assert ran.code == 0, ran.err
    assert checkout.docker.compose_steps() == []
    assert checkout.docker.state_of("docs") == "stopped"
    manifest = json.loads((checkout.data / ".snapshots" / ran.json()["snapshot"]
                           / "MANIFEST.json").read_text())
    assert manifest["stacklets"]["docs"]["up"] is False


def test_a_take_that_fails_starts_the_stacklet_again(checkout):
    """Given a take that fails (`.snapshots` cannot be made), when the
    admin takes the snapshot of the running docs, then the command fails
    and names why, and docs runs again: the family keeps its page."""
    (checkout.data / ".snapshots").write_text("not a folder")

    ran = checkout.stack("snapshot", "create", "docs", "--json")

    assert ran.code == 1
    assert ".snapshots" in ran.json()["error"]
    assert checkout.docker.state_of("docs") == "running"


def test_an_unknown_stacklet_stops_nothing(checkout):
    """Given no stacklet `dosc`, when the admin takes its snapshot, then
    the command fails and nothing stops."""
    checkout.docker.forget()

    ran = checkout.stack("snapshot", "create", "docs", "dosc", "--json")

    assert ran.code == 1
    assert "dosc" in ran.json()["error"]
    assert checkout.docker.compose_steps() == []
    assert checkout.snapshot_folders() == []


# ── The list ─────────────────────────────────────────────────────────────

def test_list_shows_each_snapshot_newest_first(checkout):
    """Given two snapshots, when the admin runs `stack snapshot list`, then
    it shows each one, newest first, with its time, stacklets and release."""
    first = checkout.stack("snapshot", "create", "docs", "--json").json()["snapshot"]
    second = checkout.stack("snapshot", "create", "docs", "--json").json()["snapshot"]

    listed = checkout.stack("snapshot", "list", "--json").json()["snapshots"]

    assert [s["name"] for s in listed] == [second, first]
    assert listed[0]["stacklets"] == ["docs"]
    assert listed[0]["version"]
    assert listed[0]["taken"].startswith(second[:10])
    pretty = checkout.stack("snapshot", "list")
    assert pretty.code == 0
    names = [line.split()[0] for line in pretty.out.splitlines() if line.strip()]
    assert names == [second, first]


def test_list_without_snapshots_says_so(checkout):
    """Given no snapshot, when the admin runs `stack snapshot list`, then
    the list is empty, and the person reads that there are none yet."""
    assert checkout.stack("snapshot", "list", "--json").json() == {"snapshots": []}
    assert "No snapshots yet." in checkout.stack("snapshot", "list").out


def test_a_half_snapshot_is_not_listed(checkout):
    """Given a take that stopped before its manifest, when the admin runs
    `stack snapshot list`, then it is not in the list."""
    (checkout.data / ".snapshots" / "2026-11-24T2114Z" / "docs").mkdir(parents=True)
    (checkout.data / ".snapshots" / ".taking-2026-11-24T2120Z" / "docs").mkdir(parents=True)

    assert checkout.stack("snapshot", "list", "--json").json() == {"snapshots": []}


# ── `.snapshots/` is nobody's data ───────────────────────────────────────

def test_the_snapshots_folder_is_no_stacklet(checkout):
    """Given a snapshot in the data folder, when the stack lists its
    stacklets, then `.snapshots` is not among them, and docs is listed as
    before."""
    before = checkout.stack("list", "--json")
    checkout.stack("snapshot", "create", "docs", "--json")

    after = checkout.stack("list", "--json")

    assert after.code == 0, after.err
    assert ".snapshots" not in after.out
    assert _ids(after.json()) == _ids(before.json()) == ["docs"]


def test_no_shipped_archive_source_holds_the_snapshots_folder():
    """Given the stacklets the release ships, then no `[[backup.archive]]`
    source is the data folder itself or holds `.snapshots/`: the backup's
    sync copies only those sources, so it never copies a snapshot."""
    from stack.snapshot.store import SNAPSHOTS_FOLDER, archive_paths

    repo = Path(__file__).resolve().parents[3]
    data = Path("/data")
    for toml in sorted((repo / "stacklets").glob("*/stacklet.toml")):
        for path in archive_paths(toml.parent, data):
            assert SNAPSHOTS_FOLDER not in path.parts
        declared = toml.read_text()
        assert 'path      = "{data_dir}"' not in declared
        assert 'path = "{data_dir}"' not in declared


def _ids(listed: dict) -> list[str]:
    """The ids in `stack list --json`, whatever group they are in."""
    found = []
    for value in listed.values():
        if isinstance(value, list):
            found += [item["id"] for item in value if isinstance(item, dict) and "id" in item]
    return sorted(found)


def test_doctor_does_not_read_the_snapshots_folder(checkout):
    """Given a snapshot in the data folder, when the admin runs `stack
    doctor`, then no finding names `.snapshots`: doctor reads the data
    folder of each stacklet, and `.snapshots` is none."""
    checkout.stack("snapshot", "create", "docs", "--json")

    ran = checkout.stack("doctor", "--json")

    findings = ran.json()["findings"]
    assert not [f for f in findings if ".snapshots" in json.dumps(f)]
    assert ".snapshots" not in ran.err


# ── A stacklet that is half up ───────────────────────────────────────────

@pytest.mark.parametrize("state", ["failing", "starting"])
def test_a_stacklet_with_a_container_up_is_stopped_before_the_clone(checkout, state):
    """Given docs whose web container crashed while its database still runs
    (`failing`), when the admin takes its snapshot, then the stack stops
    docs before the clone and starts it after it: a database cloned while
    its server writes is torn."""
    checkout.docker.running(docs=state)
    checkout.docker.forget()

    ran = checkout.stack("snapshot", "create", "docs", "--json")

    assert ran.code == 0, ran.err
    assert checkout.docker.compose_steps()[:1] == ["stop"]
    assert checkout.docker.compose_steps()[-1] == "up"
    assert checkout.docker.state_of("docs") == "running"
    manifest = json.loads((checkout.data / ".snapshots" / ran.json()["snapshot"]
                           / "MANIFEST.json").read_text())
    assert manifest["stacklets"]["docs"]["up"] is True


def test_a_container_that_did_not_stop_keeps_the_take_from_cloning(checkout):
    """Given docs whose database container stays up after `compose stop`
    exits 0, so docs is still `failing`, when the admin takes its
    snapshot, then the take clones nothing, the command fails and names
    docs, and docs is started again."""
    checkout.docker.follows_compose(False)
    checkout.docker.running(docs="failing")
    checkout.docker.forget()

    ran = checkout.stack("snapshot", "create", "docs", "--json")

    assert ran.code == 1
    assert "docs" in ran.json()["error"]
    assert checkout.snapshot_folders() == []
    assert checkout.docker.compose_steps()[0] == "stop"
    assert checkout.docker.compose_steps()[-1] == "up"


def test_an_unexpected_error_in_the_take_still_starts_the_stacklet(checkout):
    """Given a clone that raises an error the store does not expect, when
    the admin takes the snapshot of the running docs, then the error
    reaches the admin, and docs runs again."""
    import argparse

    from stack import Stack
    from stack.output import CollectorOutput
    from stack.snapshot import cli as snapshot_cli
    from stack.snapshot.store import ThisVolume

    class Exploding(ThisVolume):
        def can_clone(self, folder):
            return True

        def clone(self, source, target):
            raise RuntimeError("the volume went away")

    stck = Stack(root=checkout.root, data=checkout.data, instance_dir=checkout.instance,
                 output=CollectorOutput())
    args = argparse.Namespace(verb="create", stacklets=["docs"], json=True)

    with pytest.raises(RuntimeError, match="went away"):
        snapshot_cli.run(stck, args, volume=Exploding())

    assert checkout.docker.compose_steps()[-1] == "up"
    assert checkout.docker.state_of("docs") == "running"
    assert checkout.snapshot_folders() == []


def test_the_printed_list_shows_the_time_the_stacklets_and_the_release(checkout):
    """Given one snapshot, when the admin runs `stack snapshot list`, then
    its line holds the snapshot's time, its stacklets and its release."""
    checkout.stack("snapshot", "create", "docs", "--json")
    listed = checkout.stack("snapshot", "list", "--json").json()["snapshots"][0]
    from datetime import datetime
    local = datetime.fromisoformat(listed["taken"]).astimezone().strftime("%Y-%m-%d %H:%M")

    line = [ln for ln in checkout.stack("snapshot", "list").out.splitlines() if ln.strip()][0]

    assert line.split() == [listed["name"], *local.split(), "docs", listed["version"]]


def test_an_unreadable_manifest_is_listed_as_unreadable_and_the_others_still(checkout):
    """Given two snapshots, the older one with a cut-off manifest, when the
    admin lists them, then both are listed, the older one as unreadable,
    and the command succeeds: one broken snapshot must not hide the others."""
    first = checkout.stack("snapshot", "create", "docs", "--json").json()["snapshot"]
    second = checkout.stack("snapshot", "create", "docs", "--json").json()["snapshot"]
    (checkout.data / ".snapshots" / first / "MANIFEST.json").write_text('{"kind": "snap')

    ran = checkout.stack("snapshot", "list", "--json")

    assert ran.code == 0, ran.err
    listed = ran.json()["snapshots"]
    assert [s["name"] for s in listed] == [second, first]
    assert "unreadable" not in listed[0]
    assert "MANIFEST.json" in listed[1]["unreadable"]
    assert first in checkout.stack("snapshot", "list").out
