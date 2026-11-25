"""The manifest: what each stacklet runs, written and read as MANIFEST.json.

A snapshot before an update and, later, a backup keep a manifest, so that
a rollback or a restore can go back to the same code, the same images and
the same migrations. These tests drive `stack.manifest` through its public
functions, as the snapshot and the backup call it.

Docker is outside the unit lane, so the tests give the module a stand-in
for the Docker of this Mac (`ThisMac`): the services of a compose file,
the containers of a stacklet and the images that the Mac holds. Which
image a service runs is tested in `test_images.py`. The migration records
and the manifest files are real.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from stack.images import Container, Image, ImageFacts, Service
from stack.manifest import (
    MANIFEST_FILE,
    Manifest,
    ManifestError,
    StackletRecord,
    read,
    record_stacklet,
    write,
)

REPO_ROOT = Path(__file__).parents[3]

PAPERLESS = "ghcr.io/paperless-ngx/paperless-ngx:2.20.1"
PAPERLESS_ID = "sha256:" + "e" * 64
PAPERLESS_DIGEST = "ghcr.io/paperless-ngx/paperless-ngx@sha256:" + "d" * 64
BOT_RUNNER = "stack-core-stack-core-bot-runner"
BOT_RUNNER_ID = "sha256:" + "c" * 64
PYTHON_DIGEST = "python@sha256:" + "b" * 64


# ── The stand-in for Docker ──────────────────────────────────────────────

@dataclass
class ThisMac:
    """Docker on this Mac as the manifest asks it: the services of each
    compose file, the containers of each stacklet, and the id and registry
    digests of each image it holds."""

    services_by_file: dict[Path, list[Service]] = field(default_factory=dict)
    containers_by_stacklet: dict[str, list[Container]] = field(default_factory=dict)
    images: dict[str, ImageFacts] = field(default_factory=dict)

    def services(self, compose_file: Path) -> list[Service]:
        return self.services_by_file[Path(compose_file)]

    def containers(self, stacklet_id: str) -> list[Container]:
        return self.containers_by_stacklet.get(stacklet_id, [])

    def inspect(self, image: str) -> ImageFacts | None:
        return self.images.get(image)


def a_stacklet(tmp_path: Path, stacklet_id: str, *, applied: dict[str, str] | None = None
               ) -> tuple[Path, Path]:
    """A stacklet's folder with a compose file, and its data folder with
    the migrations that ran."""
    stacklet_dir = tmp_path / "stacklets" / stacklet_id
    stacklet_dir.mkdir(parents=True)
    (stacklet_dir / "docker-compose.yml").write_text(f"name: stack-{stacklet_id}\n")
    data_dir = tmp_path / "data" / stacklet_id
    data_dir.mkdir(parents=True)
    if applied is not None:
        record = {"applied": {number: {"name": name, "at": "2026-11-20T10:00:00+01:00"}
                              for number, name in applied.items()},
                  "failed": {}}
        (data_dir / ".migrations.json").write_text(json.dumps(record))
    return stacklet_dir, data_dir


def paperless_on(stacklet_dir: Path, *, running: bool) -> ThisMac:
    """A Mac that holds the Paperless image of the docs stacklet, with
    its container when the stacklet runs."""
    containers = {"docs": [Container("paperless", PAPERLESS, PAPERLESS_ID)]} if running else {}
    return ThisMac(
        services_by_file={stacklet_dir / "docker-compose.yml": [Service("paperless", PAPERLESS)]},
        containers_by_stacklet=containers,
        images={PAPERLESS: ImageFacts(PAPERLESS_ID, (PAPERLESS_DIGEST,)),
                PAPERLESS_ID: ImageFacts(PAPERLESS_ID, (PAPERLESS_DIGEST,))})


def a_manifest(**stacklets: StackletRecord) -> Manifest:
    return Manifest(kind="snapshot", name="2026-11-24T2300Z", version="0.4.0-rc.1",
                    commit="f5834a67", applies="0.4.0-rc.2", stacklets=stacklets)


PAPERLESS_IMAGE = Image(name=PAPERLESS, id=PAPERLESS_ID, digest=PAPERLESS_DIGEST)

DOCS_RECORD = StackletRecord(up=True, commit="f5834a67", migrations=["0001_move_tags"],
                             images={"paperless": PAPERLESS_IMAGE})


# ── Recording a stacklet ─────────────────────────────────────────────────

def test_a_running_stacklet_records_its_image_digests_migrations_and_up(tmp_path):
    """Given a running stacklet with a pulled image and two applied
    migrations, when the stack records it, then the record holds the
    image its container runs with its registry digest, the migrations in
    number order, the commit it started from and "up"."""
    stacklet_dir, data_dir = a_stacklet(
        tmp_path, "docs", applied={"0002": "kind_for_variants", "0001": "move_tags"})

    record = record_stacklet(stacklet_dir, data_dir, up=True, started_from="f5834a67",
                             docker=paperless_on(stacklet_dir, running=True))

    assert record == StackletRecord(
        up=True, commit="f5834a67",
        migrations=["0001_move_tags", "0002_kind_for_variants"],
        images={"paperless": PAPERLESS_IMAGE})


def test_a_stopped_stacklet_records_down_with_its_migrations_and_the_images_it_would_start(tmp_path):
    """Given a stacklet that is installed but stopped, with no container,
    when the stack records it, then the record says "down", has no commit
    it started from, and still holds its migrations and the images its
    compose file starts. A rollback must restore a stopped stacklet too."""
    stacklet_dir, data_dir = a_stacklet(tmp_path, "docs", applied={"0001": "move_tags"})

    record = record_stacklet(stacklet_dir, data_dir, up=False, started_from=None,
                             docker=paperless_on(stacklet_dir, running=False))

    assert record == StackletRecord(up=False, commit=None, migrations=["0001_move_tags"],
                                    images={"paperless": PAPERLESS_IMAGE})


def test_a_stacklet_without_a_compose_file_or_migrations_records_no_images_and_no_migrations(tmp_path):
    """Given a host stacklet (no compose file) that never ran a migration,
    when the stack records it, then the record has no images and no
    migrations."""
    stacklet_dir = tmp_path / "stacklets" / "ai"
    stacklet_dir.mkdir(parents=True)

    record = record_stacklet(stacklet_dir, tmp_path / "data" / "ai", up=True,
                             started_from="f5834a67", docker=ThisMac())

    assert record == StackletRecord(up=True, commit="f5834a67", migrations=[], images={})


@pytest.mark.parametrize("broken", [
    pytest.param("{\"applied\": {\"0001\": {\"na", id="cut off"),
    pytest.param("[]\n", id="not an object"),
    pytest.param(json.dumps({"applied": {"0001": {"at": "2026-11-20"}}}), id="entry without a name"),
])
def test_a_broken_migration_record_is_an_error_that_names_the_file(tmp_path, broken):
    """Given a stacklet whose migration record is broken, when the stack
    records it, then it gets an error that names the record. A broken
    record never reads as "no migrations", because a rollback would then
    run every migration again on old data."""
    stacklet_dir, data_dir = a_stacklet(tmp_path, "docs")
    (data_dir / ".migrations.json").write_text(broken)

    with pytest.raises(ManifestError) as error:
        record_stacklet(stacklet_dir, data_dir, up=True, started_from="f5834a67",
                        docker=paperless_on(stacklet_dir, running=True))

    assert str(data_dir / ".migrations.json") in str(error.value)


# ── Writing and reading ──────────────────────────────────────────────────

def test_a_written_manifest_reads_back_as_the_same_records(tmp_path):
    """Given a manifest written by the stack, when the stack reads the
    folder again, then it gets the same manifest, field for field."""
    built = StackletRecord(up=False, commit=None, migrations=[],
                           images={"bot": Image(name=BOT_RUNNER, id=BOT_RUNNER_ID, built=True,
                                                base=PYTHON_DIGEST)})
    manifest = a_manifest(docs=DOCS_RECORD, core=built)

    path = write(tmp_path, manifest)

    assert path == tmp_path / MANIFEST_FILE
    assert read(tmp_path) == manifest


def test_a_manifest_with_fields_of_a_later_release_still_reads(tmp_path):
    """Given a manifest with fields this release does not know (at the
    top, in a stacklet and in an image), when the stack reads it, then it
    gets the fields it knows. A later release can add fields, and an older
    one must still roll back to it."""
    write(tmp_path, a_manifest(docs=DOCS_RECORD))
    data = json.loads((tmp_path / MANIFEST_FILE).read_text())
    data["freeze"] = {"start": "2026-11-24T02:00:03Z", "hold": True}
    data["stacklets"]["docs"]["sources"] = [{"kind": "postgres", "path": "docs/postgres.dump"}]
    data["stacklets"]["docs"]["images"]["paperless"]["platform"] = "linux/arm64"
    (tmp_path / MANIFEST_FILE).write_text(json.dumps(data))

    assert read(tmp_path) == a_manifest(docs=DOCS_RECORD)


def test_a_folder_without_a_manifest_reads_as_none(tmp_path):
    """Given a folder with no manifest, when the stack reads it, then it
    gets None, not an error and not an empty manifest."""
    assert read(tmp_path) is None


@pytest.mark.parametrize("broken", [
    pytest.param(lambda text: text[: len(text) // 2], id="cut off"),
    pytest.param(lambda text: "not json at all\n", id="not JSON"),
    pytest.param(lambda text: "[]\n", id="not an object"),
    pytest.param(lambda text: json.dumps({"kind": "snapshot"}), id="fields missing"),
])
def test_a_broken_manifest_is_an_error_that_names_the_file(tmp_path, broken):
    """Given a manifest file that is cut off, not JSON or not a manifest,
    when the stack reads it, then it gets an error that names the file. It
    never gets an empty manifest, because a rollback from an empty one
    would restore nothing and say it worked."""
    write(tmp_path, a_manifest(docs=DOCS_RECORD))
    path = tmp_path / MANIFEST_FILE
    path.write_text(broken(path.read_text()))

    with pytest.raises(ManifestError) as error:
        read(tmp_path)

    assert str(path) in str(error.value)


# ── A write that stops halfway ───────────────────────────────────────────

WRITE_UNTIL_THE_DISK_REFUSES = """
import resource, signal, sys
from pathlib import Path
from stack.images import Image
from stack.manifest import Manifest, StackletRecord, write

# The file size limit stops the write partway, as a full disk does.
# SIGXFSZ is ignored so that the write raises instead of killing the process.
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (200, 200))
images = {f"service-{n}": Image(name=f"example/image-{n}:1", digest="example@sha256:" + "a" * 64)
          for n in range(50)}
write(Path(sys.argv[1]), Manifest(kind="snapshot", name="later", version="0.5.0", commit="0123abcd",
                                  stacklets={"docs": StackletRecord(True, None, [], images)}))
"""


def write_that_stops_halfway(folder: Path) -> None:
    """Run `write` in its own process with a file size limit, so the
    write really stops partway. The process must fail."""
    result = subprocess.run(
        [sys.executable, "-c", WRITE_UNTIL_THE_DISK_REFUSES, str(folder)],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "lib")})
    assert result.returncode != 0, "the write was meant to stop halfway but finished"


def test_a_write_that_stops_halfway_leaves_the_old_manifest(tmp_path):
    """Given a manifest in a folder, when a new write stops halfway, then
    the stack reads the old manifest, never half of the new one."""
    old = a_manifest(docs=DOCS_RECORD)
    write(tmp_path, old)

    write_that_stops_halfway(tmp_path)

    assert read(tmp_path) == old


def test_a_first_write_that_stops_halfway_leaves_no_manifest(tmp_path):
    """Given a folder without a manifest, when the first write stops
    halfway, then the stack finds no manifest, not a broken one."""
    write_that_stops_halfway(tmp_path)

    assert read(tmp_path) is None
