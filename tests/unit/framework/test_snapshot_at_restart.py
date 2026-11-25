"""The snapshot that `stack restart` takes when it applies a release, as the admin runs it.

`stack update` moves the checkout to a release and starts nothing. The
restart after it stops each stacklet that runs older code, takes a
snapshot of its state while it is stopped anyway, and only then starts
it on the new release. These tests run `./stack` as the admin does, as a
process of its own (`python -m stack`), from a fabricated git checkout
with two stacklets, `docs` and `photos`, and two releases: `v0.4.0-rc.1`,
which both run, and `v0.4.0-rc.2`, which changes both compose files. The
checkout of `host_checkout` adds `speech`, a stacklet without containers.

Docker is the stand-in `docker_with_projects`. It follows `compose up` and
`compose stop` as a real daemon does, says that docs' containers started
from rc.1, and that the paperless container runs the image of rc.1 while
the compose file now names rc.2's tag. `tmutil` is a stand-in, so no test
marks a folder of this Mac for Time Machine. The data folder and the
instance are real files in the test's folder.

Each `./stack` command is a process that starts in about 0.3 seconds; a
test runs two to four of them and git a few times, one to two seconds.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from stack import manifest as manifests

REPO = Path(__file__).resolve().parents[3]

# How long one `./stack` command may take here.
STACK_SECONDS = 60

DOCS_TOML = """
id = "docs"
name = "Documents"
category = "test"

[[backup.archive]]
name = "media"
path = "{data_dir}/docs/paperless/media"
"""

COMPOSE = """
name: stack-docs
services:
  paperless:
    image: ghcr.io/paperless-ngx/paperless-ngx:{tag}
    container_name: stack-docs-paperless
"""

PHOTOS_TOML = """
id = "photos"
name = "Photos"
category = "test"
"""

PHOTOS_COMPOSE = """
name: stack-photos
services:
  server:
    image: ghcr.io/immich-app/immich-server:{tag}
    container_name: stack-photos-server
"""

SPEECH_TOML = """
id = "speech"
name = "Speech"
category = "test"
version = "{version}"
"""

OLD_IMAGE = "ghcr.io/paperless-ngx/paperless-ngx:2.20.1"
OLD_IMAGE_ID = "sha256:" + "4" * 64
OLD_DIGEST = "ghcr.io/paperless-ngx/paperless-ngx@sha256:" + "a" * 64


@dataclass
class Ran:
    code: int
    out: str
    err: str


class Checkout:
    """A git checkout on rc.2, with docs set up and running rc.1.

    `stack(...)` runs `./stack` against it, as the admin does.
    """

    def __init__(self, root: Path, monkeypatch, docker, host: bool = False):
        self.root = root / "checkout"
        self.instance = root / "instance"
        self.data = root / "data"
        self.docker = docker
        docs = self.root / "stacklets" / "docs"
        docs.mkdir(parents=True)
        (docs / "stacklet.toml").write_text(DOCS_TOML)
        (docs / "docker-compose.yml").write_text(COMPOSE.format(tag="2.20.1"))
        photos = self.root / "stacklets" / "photos"
        photos.mkdir()
        (photos / "stacklet.toml").write_text(PHOTOS_TOML)
        (photos / "docker-compose.yml").write_text(PHOTOS_COMPOSE.format(tag="v2.1.0"))
        speech = self.root / "stacklets" / "speech"
        if host:
            speech.mkdir()
            (speech / "stacklet.toml").write_text(SPEECH_TOML.format(version="1"))
        (self.root / ".gitignore").write_text(".env\n")
        self.git("init", "-q", "-b", "main")
        self.release("v0.4.0-rc.1")
        self.rc1 = self.git("rev-parse", "--short", "HEAD")
        (docs / "docker-compose.yml").write_text(COMPOSE.format(tag="2.20.2"))
        (photos / "docker-compose.yml").write_text(PHOTOS_COMPOSE.format(tag="v2.2.0"))
        if host:
            (speech / "stacklet.toml").write_text(SPEECH_TOML.format(version="2"))
        self.release("v0.4.0-rc.2")
        self.rc2 = self.git("rev-parse", "--short", "HEAD")

        (self.instance / ".stack").mkdir(parents=True)
        for sid in ("docs", "photos", *(["speech"] if host else [])):
            (self.instance / ".stack" / f"{sid}.setup-done").touch()
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
        (self.data / "photos" / "library").mkdir(parents=True)
        (self.data / "photos" / "library" / "beach.jpg").write_text("JPEG")
        if host:
            # A stacklet without containers is up while its stamp holds the
            # commit it started from, as `stack up` writes it.
            (self.data / "speech").mkdir()
            (self.data / "speech" / "voice.onnx").write_text("weights")
            (self.instance / ".stack" / "speech.up").write_text(self.rc1)
        monkeypatch.setenv("STACK_DIR", str(self.instance))

        docker.commits(docs=self.rc1, photos=self.rc1)
        docker.runs_image("docs", "paperless", OLD_IMAGE, OLD_IMAGE_ID, OLD_DIGEST)
        docker.runs_image("photos", "server", "ghcr.io/immich-app/immich-server:v2.1.0",
                          "sha256:" + "5" * 64, "ghcr.io/immich-app/immich-server@sha256:" + "b" * 64)

    def git(self, *args: str) -> str:
        """git in the checkout, without the hooks or the identity of this Mac."""
        done = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Homer",
             "-c", "user.email=homer@example.com", "-c", "tag.gpgSign=false",
             "-c", "commit.gpgSign=false", *args],
            cwd=self.root, capture_output=True, text=True, check=True)
        return done.stdout.strip()

    def release(self, tag: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"chore: release {tag}")
        self.git("tag", "-a", tag, "-m", tag)

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

    def manifest(self, name: str) -> dict:
        return json.loads((self.data / ".snapshots" / name / "MANIFEST.json").read_text())

    def older_snapshot(self, name: str) -> None:
        """A whole snapshot taken before, named for its time."""
        folder = self.data / ".snapshots" / name
        (folder / "docs").mkdir(parents=True)
        manifests.write(folder, manifests.Manifest(kind="snapshot", name=name,
                                                   version="0.3.0", commit="0ld0ld0"))


def _checkout(tmp_path, monkeypatch, docker_with_projects, host: bool) -> Checkout:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tmutil").write_text(f'#!/bin/sh\necho "$@" >> "{bin_dir / "tmutil.log"}"\n')
    (bin_dir / "tmutil").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir), prepend=os.pathsep)
    docker = docker_with_projects(docs="running", photos="running")
    docker.follows_compose()
    return Checkout(tmp_path, monkeypatch, docker, host=host)


@pytest.fixture
def checkout(tmp_path, monkeypatch, docker_with_projects) -> Checkout:
    return _checkout(tmp_path, monkeypatch, docker_with_projects, host=False)


@pytest.fixture
def host_checkout(tmp_path, monkeypatch, docker_with_projects) -> Checkout:
    """The checkout with `speech` too, up on rc.1 without containers."""
    return _checkout(tmp_path, monkeypatch, docker_with_projects, host=True)


def the_new_snapshot(checkout: Checkout, before: list[str]) -> str:
    """The one snapshot folder that is new since `before`."""
    new = sorted(set(checkout.snapshot_folders()) - set(before))
    assert len(new) == 1, checkout.snapshot_folders()
    return new[0]


# ── The restart that applies a release ───────────────────────────────────

def test_the_restart_stops_docs_takes_its_snapshot_then_starts_it(checkout):
    """Given that `stack update` moved the checkout to rc.2 and docs runs
    rc.1, when the admin runs `stack restart`, then the stack stops docs,
    takes the snapshot of its state, and starts docs on rc.2."""
    checkout.docker.forget()

    ran = checkout.stack("restart")

    assert ran.code == 0, ran.err
    [name] = checkout.snapshot_folders()
    folder = checkout.data / ".snapshots" / name
    assert (folder / "docs" / "postgres" / "base.dat").read_text() == "homer's invoices"
    assert not (folder / "docs" / "paperless" / "media").exists()
    steps = checkout.docker.compose_steps()
    assert steps.count("stop") == 2 and steps.count("up") == 2
    assert steps.index("up") > len(steps) - 1 - steps[::-1].index("stop")
    assert checkout.docker.state_of("docs") == "running"
    assert name in ran.out


def test_the_snapshot_names_the_commit_before_the_release_after_and_the_running_image(checkout):
    """Given that snapshot, when the admin reads its manifest, then it
    names the commit docs ran before (rc.1), the release the restart
    applied (rc.2), and the image the container ran, not the tag that the
    new compose file names. A rollback starts exactly that image again."""
    ran = checkout.stack("restart")

    assert ran.code == 0, ran.err
    manifest = checkout.manifest(checkout.snapshot_folders()[0])
    assert manifest["kind"] == "snapshot"
    assert manifest["commit"] == checkout.rc1
    assert manifest["version"] == "0.4.0-rc.1"
    assert manifest["applies"] == "0.4.0-rc.2"
    docs = manifest["stacklets"]["docs"]
    assert docs["commit"] == checkout.rc1
    assert docs["up"] is True
    assert docs["images"]["paperless"] == {"name": OLD_IMAGE, "id": OLD_IMAGE_ID,
                                           "digest": OLD_DIGEST}


def test_the_update_plan_says_the_restart_takes_a_snapshot_first(checkout):
    """Given a checkout on rc.1 with docs running it, when the admin asks
    `stack update` for its plan, then the plan names the snapshot the
    restart takes first, where it goes, and how to skip it."""
    checkout.git("checkout", "-q", "v0.4.0-rc.1")

    ran = checkout.stack("update", "v0.4.0-rc.2", "--dry-run")

    assert ran.code == 0, ran.err
    assert ".snapshots" in ran.out
    assert "stack restart --no-snapshot" in ran.out
    assert checkout.snapshot_folders() == []


# ── No release, no snapshot ──────────────────────────────────────────────

def test_a_restart_on_the_current_code_takes_no_snapshot(checkout):
    """Given docs that runs the code of the checkout, when the admin runs
    `stack restart docs`, then docs restarts and no snapshot is taken:
    nothing new starts, so there is nothing to go back from."""
    checkout.docker.commits(docs=checkout.rc2, photos=checkout.rc2)

    ran = checkout.stack("restart", "docs")

    assert ran.code == 0, ran.err
    assert checkout.snapshot_folders() == []
    assert checkout.docker.state_of("docs") == "running"


def test_the_resting_restart_changes_nothing_in_the_snapshots(checkout):
    """Given no update (docs runs the current code) and an older snapshot,
    when the admin runs `stack restart`, then nothing in `.snapshots/`
    changes."""
    checkout.docker.commits(docs=checkout.rc2, photos=checkout.rc2)
    checkout.older_snapshot("2026-11-01T0900Z")
    before = sorted(p.relative_to(checkout.data) for p in (checkout.data / ".snapshots").rglob("*"))
    checkout.docker.forget()

    ran = checkout.stack("restart")

    assert ran.code == 0, ran.err
    assert sorted(p.relative_to(checkout.data)
                  for p in (checkout.data / ".snapshots").rglob("*")) == before
    assert checkout.docker.compose_steps() == []


def test_no_snapshot_restarts_without_one(checkout):
    """Given the update to rc.2, when the admin runs `stack restart
    --no-snapshot`, then docs starts on rc.2 and no snapshot is taken."""
    ran = checkout.stack("restart", "--no-snapshot")

    assert ran.code == 0, ran.err
    assert checkout.snapshot_folders() == []
    assert checkout.docker.state_of("docs") == "running"


# ── The newest 3 stay ────────────────────────────────────────────────────

def test_the_restart_keeps_the_newest_three_and_names_the_one_it_removed(checkout):
    """Given three older snapshots, when the restart takes a fourth, then
    the newest three stay, the new one among them, and the restart names
    the snapshot it removed."""
    for name in ("2026-11-01T0900Z", "2026-11-02T0900Z", "2026-11-03T0900Z"):
        checkout.older_snapshot(name)
    before = checkout.snapshot_folders()

    ran = checkout.stack("restart")

    assert ran.code == 0, ran.err
    new = the_new_snapshot(checkout, before)
    assert checkout.snapshot_folders() == sorted(["2026-11-02T0900Z", "2026-11-03T0900Z", new])
    assert "2026-11-01T0900Z" in ran.out


def test_an_unreadable_snapshot_is_never_removed_and_never_counted(checkout):
    """Given three older snapshots and one whose manifest is unreadable,
    when the restart takes a new one, then the unreadable one stays, and
    the newest three readable ones stay: a broken snapshot is not one of
    the three ways back."""
    for name in ("2026-11-01T0900Z", "2026-11-02T0900Z", "2026-11-03T0900Z"):
        checkout.older_snapshot(name)
    broken = checkout.data / ".snapshots" / "2026-11-04T0900Z"
    broken.mkdir()
    (broken / "MANIFEST.json").write_text("{not json")
    before = checkout.snapshot_folders()

    ran = checkout.stack("restart")

    assert ran.code == 0, ran.err
    new = the_new_snapshot(checkout, before)
    assert checkout.snapshot_folders() == sorted(
        ["2026-11-02T0900Z", "2026-11-03T0900Z", "2026-11-04T0900Z", new])


# ── A snapshot that fails ────────────────────────────────────────────────

# The four ways `stack restart` names what it restarts. Each one records
# before its stop and starts nothing on the new release without a snapshot.
RESTARTS = [("restart",), ("restart", "docs"), ("restart", "docs", "photos"), ("restart", "all")]


@pytest.mark.parametrize("command", RESTARTS, ids=" ".join)
def test_every_restart_records_docs_while_it_still_runs(checkout, command):
    """Given the update to rc.2, when the admin restarts docs in any of
    the four ways, then the snapshot's record says that docs was up: it
    was made before the stop, so it names what ran."""
    ran = checkout.stack(*command)

    assert ran.code == 0, ran.err
    [name] = checkout.snapshot_folders()
    assert checkout.manifest(name)["stacklets"]["docs"]["up"] is True


@pytest.mark.parametrize("command", RESTARTS, ids=" ".join)
def test_a_failed_snapshot_keeps_docs_from_starting_on_the_new_release(checkout, command):
    """Given a snapshot that fails (`.snapshots` cannot be made), when the
    admin restarts docs in any of the four ways, then docs is stopped and
    not started on rc.2, and the stack names the cause and the two ways
    on."""
    (checkout.data / ".snapshots").write_text("not a folder")
    checkout.docker.forget()

    ran = checkout.stack(*command)

    assert ran.code == 1
    assert set(checkout.docker.compose_steps()) == {"stop"}
    assert checkout.docker.state_of("docs") == "stopped"
    assert ".snapshots" in ran.err
    assert "stack restart docs" in ran.err
    assert "--no-snapshot" in ran.err


def test_a_record_that_fails_stops_nothing(checkout):
    """Given a migration record of docs that cannot be read, when the
    admin runs `stack restart`, then nothing stops: docs still runs rc.1,
    and the stack names the record and the two ways on."""
    (checkout.data / "docs" / ".migrations.json").write_text("{not json")
    checkout.docker.forget()

    ran = checkout.stack("restart")

    assert ran.code == 1
    assert checkout.docker.compose_steps() == []
    assert checkout.docker.state_of("docs") == "running"
    assert ".migrations.json" in ran.err
    assert "--no-snapshot" in ran.err


def test_after_a_failed_snapshot_docs_does_not_start_with_stack_up(checkout):
    """Given docs stopped by a restart whose snapshot failed, when the
    admin runs `stack up docs`, then docs stays stopped and the stack
    names the restart that takes the snapshot: `up` takes none."""
    (checkout.data / ".snapshots").write_text("not a folder")
    assert checkout.stack("restart").code == 1
    (checkout.data / ".snapshots").unlink()
    checkout.docker.forget()

    ran = checkout.stack("up", "docs")

    assert ran.code != 0
    assert "up" not in checkout.docker.compose_steps()
    assert checkout.docker.state_of("docs") == "stopped"
    assert "stack restart docs" in ran.out + ran.err


def test_after_a_failed_snapshot_the_restart_of_docs_takes_it(checkout):
    """Given docs stopped by a restart whose snapshot failed, when the
    admin fixes the cause and runs `stack restart docs`, as the stack
    said, then the snapshot is taken and docs starts on rc.2."""
    (checkout.data / ".snapshots").write_text("not a folder")
    assert checkout.stack("restart").code == 1
    (checkout.data / ".snapshots").unlink()

    ran = checkout.stack("restart", "docs")

    assert ran.code == 0, ran.err
    [name] = checkout.snapshot_folders()
    assert checkout.manifest(name)["stacklets"]["docs"]["commit"] == checkout.rc1
    assert checkout.docker.state_of("docs") == "running"


def test_after_a_failed_snapshot_no_snapshot_starts_docs(checkout):
    """Given docs stopped by a restart whose snapshot failed, when the
    admin runs `stack restart docs --no-snapshot`, then docs starts on
    rc.2 without a snapshot."""
    (checkout.data / ".snapshots").write_text("not a folder")
    assert checkout.stack("restart").code == 1

    ran = checkout.stack("restart", "docs", "--no-snapshot")

    assert ran.code == 0, ran.err
    assert checkout.docker.state_of("docs") == "running"
    assert (checkout.data / ".snapshots").is_file()


def test_the_restart_of_all_takes_the_snapshot_of_a_stacklet_on_an_older_release(checkout):
    """Given that `stack update` moved the checkout and docs runs rc.1,
    when the admin runs `stack restart all`, then docs gets its snapshot
    before it starts on rc.2, as with the bare restart."""
    ran = checkout.stack("restart", "all")

    assert ran.code == 0, ran.err
    [name] = checkout.snapshot_folders()
    assert checkout.manifest(name)["stacklets"]["docs"]["commit"] == checkout.rc1
    assert checkout.docker.state_of("docs") == "running"


# ── A stacklet without containers ────────────────────────────────────────

def test_a_stacklet_without_containers_waits_for_its_snapshot_too(host_checkout):
    """Given speech, a stacklet without containers that runs rc.1, and a
    restart whose snapshot failed, when the admin runs `stack up speech`,
    then speech does not start; when the admin then runs `stack restart
    speech`, the snapshot names rc.1 and that speech was up. Its stop
    deleted the stamp that held its commit, so only the kept record
    knows."""
    checkout = host_checkout
    (checkout.data / ".snapshots").write_text("not a folder")
    assert checkout.stack("restart").code == 1
    assert not (checkout.instance / ".stack" / "speech.up").exists()
    (checkout.data / ".snapshots").unlink()

    refused = checkout.stack("up", "speech")
    ran = checkout.stack("restart", "speech")

    assert refused.code != 0
    assert ran.code == 0, ran.err
    [name] = checkout.snapshot_folders()
    speech = checkout.manifest(name)["stacklets"]["speech"]
    assert speech["commit"] == checkout.rc1 and speech["up"] is True
    assert (checkout.instance / ".stack" / "speech.up").exists()


# ── An unknown commit ────────────────────────────────────────────────────

def test_a_commit_git_does_not_know_is_named_and_takes_no_snapshot(checkout):
    """Given docs that runs a commit this checkout does not have (a
    shallow clone, a deleted branch), when the admin runs `stack restart
    docs`, then docs restarts without a snapshot, and the stack names docs
    as a stacklet whose commit is unknown instead of saying nothing."""
    checkout.docker.commits(docs="0123456789abcdef", photos=checkout.rc2)

    ran = checkout.stack("restart", "docs")

    assert ran.code == 0, ran.err
    assert checkout.snapshot_folders() == []
    assert "No snapshot of docs" in ran.err


# ── Pruning ──────────────────────────────────────────────────────────────

def test_prune_keeps_the_snapshot_just_taken_when_the_clock_made_it_oldest(checkout):
    """Given a new snapshot whose name sorts below three older ones (the
    clock went back), when the restart prunes, then the new one stays
    with the two newest others: the snapshot just taken is the way back
    from this restart."""
    from stack.snapshot import release

    for name in ("2026-11-01T0900Z", "2026-11-02T0900Z", "2026-11-03T0900Z", "2026-11-04T0900Z"):
        checkout.older_snapshot(name)

    pruned = release.prune(checkout.data, "2026-11-01T0900Z")

    assert pruned.removed == ["2026-11-02T0900Z"]
    assert checkout.snapshot_folders() == ["2026-11-01T0900Z", "2026-11-03T0900Z", "2026-11-04T0900Z"]


# ── The kept record ──────────────────────────────────────────────────────

def pending_record(checkout: Checkout, sid: str) -> Path:
    return checkout.instance / ".stack" / "snapshot-pending" / sid / "MANIFEST.json"


def test_a_record_that_cannot_be_kept_stops_nothing(checkout):
    """Given a `.stack/` where the record of the restart cannot be kept,
    when the admin runs `stack restart`, then nothing stops: a stacklet
    stopped without a kept record could start later with no snapshot."""
    locked = checkout.instance / ".stack" / "snapshot-pending"
    locked.mkdir()
    locked.chmod(0o500)
    checkout.docker.forget()
    try:
        ran = checkout.stack("restart")
    finally:
        locked.chmod(0o700)

    assert ran.code == 1
    assert checkout.docker.compose_steps() == []
    assert checkout.docker.state_of("docs") == "running"
    assert "snapshot-pending" in ran.err
    assert "Traceback" not in ran.err


@pytest.mark.parametrize("command", RESTARTS, ids=" ".join)
def test_a_stacklet_that_did_not_stop_keeps_no_record(checkout, command):
    """Given a restart whose stop fails, when the admin restarts docs in
    any of the four ways, then nothing starts, no record waits for docs,
    and the stack says that docs still runs: it never left the release it
    ran."""
    checkout.docker.fail("stop", "container is marked for removal")
    checkout.docker.forget()

    ran = checkout.stack(*command)

    assert ran.code == 1
    assert "up" not in checkout.docker.compose_steps()
    assert checkout.docker.state_of("docs") == "running"
    assert not pending_record(checkout, "docs").exists()
    assert "still runs" in ran.err


def test_an_unreadable_record_names_its_file_and_the_two_ways_on(checkout):
    """Given docs stopped by a failed snapshot and a kept record that was
    cut off, when the admin runs `stack restart docs`, then docs does not
    start, and the stack names the file and the two ways on."""
    (checkout.data / ".snapshots").write_text("not a folder")
    assert checkout.stack("restart").code == 1
    (checkout.data / ".snapshots").unlink()
    pending_record(checkout, "docs").write_text("{cut off")
    checkout.docker.forget()

    ran = checkout.stack("restart", "docs")

    assert ran.code == 1
    assert checkout.docker.compose_steps() == []
    assert str(pending_record(checkout, "docs")) in ran.err
    assert "stack restart docs\n" in ran.err
    assert "stack restart docs --no-snapshot" in ran.err
    assert "still runs" not in ran.err


def test_destroy_ends_the_wait_and_a_new_install_starts(checkout):
    """Given docs stopped by a failed snapshot, when the admin destroys
    docs and then runs `stack up docs`, then docs starts as a new install,
    no record waits, and no snapshot claims that the new install ran rc.1."""
    (checkout.data / ".snapshots").write_text("not a folder")
    assert checkout.stack("restart").code == 1
    (checkout.data / ".snapshots").unlink()

    destroyed = checkout.stack("destroy", "docs", "--yes")
    ran = checkout.stack("up", "docs")

    assert destroyed.code == 0, destroyed.err
    assert ran.code == 0, ran.err
    assert not pending_record(checkout, "docs").exists()
    assert checkout.snapshot_folders() == []
    assert checkout.docker.state_of("docs") == "running"
