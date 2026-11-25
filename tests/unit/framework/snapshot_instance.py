"""The fabricated instance the snapshot tests share: a checkout with two releases, its data, and the commands.

    from tests.unit.framework.snapshot_instance import rollback, updated

    def test_a_rollback_goes_back_to_rc1(updated):      # the fixture `updated`
        ran = rollback(updated)                         # `stack rollback --yes --json`
        assert ran.code == 0, ran.err

`checkout` is a git checkout on `v0.4.0-rc.2` with two stacklets, `docs`
and `photos`, set up and running `v0.4.0-rc.1`; `host_checkout` adds
`speech`, a stacklet without containers. `updated` is the checkout after
`stack restart --with-snapshot` took a snapshot and started rc.2, with the family's
changes since: a letter in docs' database, a new bill in its
`[[backup.archive]]` source, a new album in photos.

Docker is the stand-in `docker_with_projects`, which follows `compose up`
and `compose stop` and answers `image inspect` and `image tag`. `tmutil`
is a stand-in, so no test marks a folder of this Mac for Time Machine.
Every `./stack` command is a process of its own (`Checkout.stack`), as
the admin runs it, in about 0.3 seconds.

The faults (`clone_fails`, `checkout_fails`, `move_fails`, `tag_fails`)
each stop a rollback at one step and return the function that undoes the
fault. The snapshot test modules share their helpers through this module
only, so a change to the instance is made once.
"""

from __future__ import annotations

import json
import os
import pty
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from stack import manifest as manifests


# ── The checkout ──────────────────────────────────────────────────────────
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


RC1_ONLY = "Notes of the docs stacklet in rc.1.\n"


README = "The stack of the test.\n"


QUOTED = '"q2"'


QUOTED_TEXT = "Only rc.1 has the file named with quotes.\n"


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

    def __init__(self, root: Path, monkeypatch, docker, host: bool = False,
                 quoted: bool = False):
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
        # A file both releases have, as the same text.
        (self.root / "README.md").write_text(README)
        # A file that only rc.1 has, as a file a refactor in rc.2 moved.
        (docs / "NOTES.md").write_text(RC1_ONLY)
        if quoted:
            # A name that git's line input reads as a quoted string.
            (self.root / QUOTED).write_text(QUOTED_TEXT)
        self.git("init", "-q", "-b", "main")
        self.release("v0.4.0-rc.1")
        self.rc1 = self.git("rev-parse", "--short", "HEAD")
        (docs / "NOTES.md").unlink()
        if quoted:
            (self.root / QUOTED).unlink()
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


def new_checkout(tmp_path, monkeypatch, docker_with_projects, host: bool,
                 quoted: bool = False) -> Checkout:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tmutil").write_text(f'#!/bin/sh\necho "$@" >> "{bin_dir / "tmutil.log"}"\n')
    (bin_dir / "tmutil").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir), prepend=os.pathsep)
    docker = docker_with_projects(docs="running", photos="running")
    docker.follows_compose()
    return Checkout(tmp_path, monkeypatch, docker, host=host, quoted=quoted)


@pytest.fixture
def checkout(tmp_path, monkeypatch, docker_with_projects) -> Checkout:
    return new_checkout(tmp_path, monkeypatch, docker_with_projects, host=False)


@pytest.fixture
def host_checkout(tmp_path, monkeypatch, docker_with_projects) -> Checkout:
    """The checkout with `speech` too, up on rc.1 without containers."""
    return new_checkout(tmp_path, monkeypatch, docker_with_projects, host=True)


# ── After the update ──────────────────────────────────────────────────────


# The image that the tag of rc.1 names after Watchtower or a pull moved
# it: a newer build of the same tag.
NEWER_IMAGE_ID = "sha256:" + "9" * 64


LETTER = "homer's invoices and the letter from the school"


# The photos' album list in the snapshot, and after the update.
ALBUMS = '["beach"]'


NEW_ALBUMS = '["beach", "springfield fair"]'


PHOTOS_IMAGE = "ghcr.io/immich-app/immich-server:v2.1.0"


PHOTOS_IMAGE_ID = "sha256:" + "5" * 64


@pytest.fixture
def updated(tmp_path, monkeypatch, docker_with_projects) -> Checkout:
    """The checkout after `stack update` to rc.2 and the restart with
    `--with-snapshot`: one
    snapshot, docs on rc.2, and the family's changes since."""
    return _updated(new_checkout(tmp_path, monkeypatch, docker_with_projects, host=False))


@pytest.fixture
def updated_with_a_quoted_name(tmp_path, monkeypatch, docker_with_projects) -> Checkout:
    """`updated`, where rc.1 also has a file named `"q2"`, with the
    quotes, which rc.2 does not have."""
    return _updated(new_checkout(tmp_path, monkeypatch, docker_with_projects, host=False,
                                 quoted=True))


def _updated(checkout: Checkout) -> Checkout:
    """Run `stack restart --with-snapshot` on `checkout` and make the family's
    changes since."""
    (checkout.data / "photos" / "albums.json").write_text(ALBUMS)
    # Each tag names the image that runs, as after the pull that started it.
    checkout.docker.tags(**{OLD_IMAGE: OLD_IMAGE_ID, PHOTOS_IMAGE: PHOTOS_IMAGE_ID})
    ran = checkout.stack("restart", "--with-snapshot")
    assert ran.code == 0, ran.err
    (checkout.data / "docs" / "postgres" / "base.dat").write_text(LETTER)
    (checkout.data / "docs" / "paperless" / "media" / "new-bill.pdf").write_text("%PDF new")
    (checkout.data / "photos" / "albums.json").write_text(NEW_ALBUMS)
    checkout.docker.forget()
    return checkout


def rollback(checkout: Checkout, *args: str) -> Ran:
    """`stack rollback --yes --json <args>`, as a script runs it."""
    return checkout.stack("rollback", "--yes", "--json", *args)


def in_terminal(checkout: Checkout, *args: str, answer: str) -> Ran:
    """`./stack <args>` from a terminal, with `answer` typed at its question."""
    typing, terminal = pty.openpty()
    try:
        os.write(typing, f"{answer}\n".encode())
        done = subprocess.run([sys.executable, "-m", "stack", *args], cwd=checkout.root,
                              stdin=terminal, capture_output=True, text=True,
                              env={**os.environ, "PYTHONPATH": str(REPO / "lib")},
                              timeout=STACK_SECONDS)
    finally:
        os.close(typing)
        os.close(terminal)
    return Ran(done.returncode, done.stdout, done.stderr)


def head(checkout: Checkout) -> str:
    return checkout.git("rev-parse", "--short", "HEAD")


def replaced_folders(checkout: Checkout) -> list[str]:
    folder = checkout.data / ".snapshots" / "replaced"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def everything_in(folder) -> list[str]:
    """Each path below `folder`, with the content of each file."""
    return sorted(f"{p.relative_to(folder)}={p.read_text() if p.is_file() else ''}"
                  for p in folder.rglob("*"))


def tags_set(checkout: Checkout) -> list[list[str]]:
    """Each `docker image tag` the stand-in got."""
    return [argv for argv in checkout.docker.commands() if argv[:2] == ["image", "tag"]]


def replaced_folder(checkout: Checkout):
    [time] = replaced_folders(checkout)
    return checkout.data / ".snapshots" / "replaced" / time


def rolled_back(checkout: Checkout, extra: set[str]) -> None:
    """The end state of every rollback: one replaced folder with each
    stacklet's state whole, the snapshot's state in the data folders, the
    new scan where it was, the snapshot's commit, both stacklets running."""
    replaced = replaced_folder(checkout)
    assert {p.name for p in replaced.iterdir()} == {"ROLLBACK.json", "docs", "photos", *extra}
    assert (replaced / "docs" / "postgres" / "base.dat").read_text() == LETTER
    assert (replaced / "photos" / "albums.json").read_text() == NEW_ALBUMS
    assert (replaced / "photos" / "library" / "beach.jpg").read_text() == "JPEG"
    assert (checkout.data / "docs" / "postgres" / "base.dat").read_text() == "homer's invoices"
    assert (checkout.data / "photos" / "albums.json").read_text() == ALBUMS
    assert (checkout.data / "docs" / "paperless" / "media" / "new-bill.pdf").exists()
    assert head(checkout) == checkout.rc1
    assert checkout.docker.state_of("docs") == "running"
    assert checkout.docker.state_of("photos") == "running"
    assert "finished" in json.loads((replaced / "ROLLBACK.json").read_text())["done"]


# ── Faults that stop a rollback ───────────────────────────────────────────
def checkout_fails(c: Checkout):
    lock = c.root / ".git" / "index.lock"
    lock.write_text("")
    return lock.unlink


def move_fails(c: Checkout):
    (c.data / "photos").chmod(0o555)
    return lambda: (c.data / "photos").chmod(0o755)


def clone_fails(c: Checkout):
    [name] = c.snapshot_folders()
    unreadable = c.data / ".snapshots" / name / "photos" / "library" / "beach.jpg"
    unreadable.chmod(0)
    return lambda: unreadable.chmod(0o644)


def tag_fails(c: Checkout):
    c.docker.tags(**{OLD_IMAGE: NEWER_IMAGE_ID})
    c.docker.fail("tag", "Error response from daemon: no space left on device")
    return lambda: c.docker.fail("tag", "")


def stopped_at_the_clone_of_docs(checkout: Checkout) -> None:
    """Run a rollback that stops at the restore of docs, then fix it."""
    [name] = checkout.snapshot_folders()
    unreadable = checkout.data / ".snapshots" / name / "docs" / "postgres" / "base.dat"
    unreadable.chmod(0)
    try:
        assert rollback(checkout).code == 1
    finally:
        unreadable.chmod(0o644)
    checkout.docker.forget()
