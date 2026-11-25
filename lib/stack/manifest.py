"""The manifest: the record of what each stacklet runs, kept as MANIFEST.json.

A snapshot before an update keeps one manifest in its folder. A later
backup keeps one too. The manifest records the commit and the version
that the stacklets ran. It holds one stacklet record for each stacklet.
A stacklet record says whether the stacklet is up and which commit it
started from. It lists the migrations that ran on the stacklet's data
and the image of each service. A rollback or a restore reads the
manifest to go back to the same code, the same images and the same
migrations.

    from stack import manifest
    from stack.images import DockerImages

    record = manifest.record_stacklet(stacklet_dir, data_dir / "docs",
                                      up=True, started_from="f5834a67",
                                      docker=DockerImages())
    manifest.write(folder, manifest.Manifest(
        kind="snapshot", name="2026-11-24T2300Z", version="0.4.0-rc.1",
        commit="f5834a67", applies="0.4.0-rc.2", stacklets={"docs": record}))
    manifest.read(folder)          # the same Manifest, or None without one

The file, for one stacklet with a pulled image and one with an image
built on this Mac:

    {
      "kind": "snapshot", "name": "2026-11-24T2300Z",
      "version": "0.4.0-rc.1", "commit": "f5834a67", "applies": "0.4.0-rc.2",
      "stacklets": {
        "docs": {"up": true, "commit": "f5834a67",
                 "migrations": ["0001_move_tags"],
                 "images": {"stack-docs-redis": {
                     "name": "docker.io/valkey/valkey:8.1-alpine",
                     "id": "sha256:8a1d…",
                     "digest": "valkey/valkey@sha256:9971…"}}},
        "core": {"up": false, "migrations": [],
                 "images": {"stack-core-bot-runner": {
                     "name": "stack-core-stack-core-bot-runner",
                     "id": "sha256:c457…", "built": true,
                     "base": "python@sha256:a6e3…"}}}
      }
    }

Why it works this way:

- **The images come from `stack.images`.** That module says which image
  each service runs, and why a container counts before the compose file.
  This module keeps the records, the file's shape, the write and the read.
- **The applied migrations come from `stack.migrations.Record`,** the one
  reader of `.migrations.json`. A broken record fails the manifest. It
  never reads as "no migrations".
- **A write is all or nothing.** The file is written beside the old one
  and renamed over it. A write that stops halfway leaves the old manifest
  or none.
- **A broken file is an error, never an empty manifest.** A rollback from
  an empty manifest would restore nothing and report success. Fields this
  release does not know are ignored, because a later release can add them.

The caller says whether a stacklet is up and which commit it started from
(`Stack.is_running`, `Stack.started_commits`). The caller also gives the
commit and the version they ran (`stack.updater.Checkout`): the checkout's
for `stack snapshot create`, the release before the update for the
restart's snapshot (`stack.snapshot.release`). This module takes no
snapshot and has no command. The snapshot store calls it. A release
writes the file and a later release reads it, so the file's shape is a
contract between releases.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .images import Image, ImageError, ImageSource, images_of
from .migrations import RECORD_FILE, Record

__all__ = [
    "MANIFEST_FILE",
    "Manifest",
    "ManifestError",
    "StackletRecord",
    "read",
    "record_stacklet",
    "write",
]

MANIFEST_FILE = "MANIFEST.json"

# The name of the file during a write. It starts with a dot, so a reader of
# the folder sees it as a leftover, and never as a manifest.
_PARTIAL_FILE = "." + MANIFEST_FILE + ".partial"


# ── The records ──────────────────────────────────────────────────────────

class ManifestError(Exception):
    """A manifest that cannot be read or recorded. The message names the
    file.

        ManifestError(".snapshots/2026-11-24T2300Z/MANIFEST.json: not a readable manifest: …")
    """


@dataclass(frozen=True)
class StackletRecord:
    """What one stacklet runs.

    `up` says whether it ran when the record was taken. `commit` is the
    commit it started from, or None when it is down or the start left no
    commit. `migrations` lists the applied migrations as `NNNN_<name>`, in
    number order. `images` maps each compose service to its image
    (`stack.images.Image`).

        StackletRecord(up=True, commit="f5834a67",
                       migrations=["0001_move_tags"],
                       images={"stack-docs-db": Image(name="postgres:16-alpine",
                                                      id="sha256:31b7…",
                                                      digest="postgres@sha256:1d5b…")})
    """

    up: bool
    commit: str | None
    migrations: list[str]
    images: dict[str, Image]


@dataclass(frozen=True)
class Manifest:
    """The record of a snapshot or a backup.

    `kind` is "snapshot" or "backup". `name` is the folder's name.
    `version` and `commit` name the release and the commit that the
    stacklets ran when the record was taken. After `stack update` that is
    the release before it, not the checkout's. A snapshot's `applies` is
    the release that the restart after it applies. `stacklets` maps each
    stacklet's id to its record.

        Manifest(kind="snapshot", name="2026-11-24T2300Z",
                 version="0.4.0-rc.1", commit="f5834a67",
                 applies="0.4.0-rc.2", stacklets={"docs": record})
    """

    kind: str
    name: str
    version: str
    commit: str
    stacklets: dict[str, StackletRecord] = field(default_factory=dict)
    applies: str | None = None


# ── Recording, writing, reading ──────────────────────────────────────────

def record_stacklet(stacklet_dir: Path, data_dir: Path, *, up: bool,
                    started_from: str | None, docker: ImageSource) -> StackletRecord:
    """The record of the stacklet in `stacklet_dir`, whose data folder is
    `data_dir` (`<data_dir>/<id>/`).

    Reads the applied migrations from the data folder's migration record.
    Asks `docker` for the image of each service (`stack.images.images_of`).
    `up` and `started_from` come from the caller, which knows the
    stacklet's state. Raises `ManifestError` when the migration record or
    the compose file cannot be read. Changes nothing.

        record_stacklet(Path("stacklets/docs"), Path("~/famstack-data/docs"),
                        up=False, started_from=None, docker=DockerImages())
        # StackletRecord(up=False, commit=None, migrations=["0001_move_tags"],
        #                images={"stack-docs-db": Image(…), …})
    """
    try:
        images = images_of(Path(stacklet_dir), docker)
    except ImageError as error:
        raise ManifestError(str(error)) from error
    return StackletRecord(up=up, commit=started_from,
                          migrations=_applied_migrations(Path(data_dir)), images=images)


def write(folder: Path, manifest: Manifest) -> Path:
    """Write `manifest` as `<folder>/MANIFEST.json` and return its path.

    Creates the folder when it is missing. The new file replaces an older
    manifest in one step. A write that stops halfway (a full disk, a
    killed process) leaves the older manifest or none. A write that fails
    raises, and leaves no partial file behind.

        write(Path(".snapshots/2026-11-24T2300Z"), manifest)
        # Path(".snapshots/2026-11-24T2300Z/MANIFEST.json")
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / MANIFEST_FILE
    partial = folder / _PARTIAL_FILE
    text = json.dumps(_manifest_to_json(manifest), indent=2) + "\n"
    try:
        with open(partial, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(partial, path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    _sync_folder(folder)
    return path


def read(folder: Path) -> Manifest | None:
    """The manifest in `folder`, or None when the folder has none.

    Raises `ManifestError` when the file is cut off, is not JSON or misses
    a field. The error names the file. Fields that this release does not
    know are ignored.

        read(Path(".snapshots/2026-11-24T2300Z"))   # Manifest(kind="snapshot", …)
        read(Path(".snapshots/empty"))              # None
    """
    path = Path(folder) / MANIFEST_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return _manifest_from_json(json.loads(text))
    except (ValueError, KeyError, TypeError) as error:
        raise ManifestError(f"{path}: not a readable manifest: {error}") from error


# ── Migrations ───────────────────────────────────────────────────────────

def _applied_migrations(data_dir: Path) -> list[str]:
    """The applied migrations of `data_dir`'s migration record, as
    `NNNN_<name>` in number order. A data folder without a record has
    none. A broken record raises `ManifestError` with the record's path."""
    path = data_dir / RECORD_FILE
    try:
        applied = Record.load(path).applied
        return [f"{number}_{applied[number]['name']}" for number in sorted(applied)]
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ManifestError(f"{path}: not a readable migration record: {error!r}") from error


# ── The file's shape ─────────────────────────────────────────────────────

def _manifest_to_json(manifest: Manifest) -> dict:
    data: dict[str, object] = {"kind": manifest.kind, "name": manifest.name,
                               "version": manifest.version, "commit": manifest.commit}
    if manifest.applies is not None:
        data["applies"] = manifest.applies
    data["stacklets"] = {sid: _record_to_json(r) for sid, r in manifest.stacklets.items()}
    return data


def _record_to_json(record: StackletRecord) -> dict:
    data: dict = {"up": record.up}
    if record.commit is not None:
        data["commit"] = record.commit
    data["migrations"] = list(record.migrations)
    data["images"] = {service: _image_to_json(image) for service, image in record.images.items()}
    return data


def _image_to_json(image: Image) -> dict:
    """The image's fields that say something: no None, and `built` only
    when it is true."""
    return {key: value for key, value in vars(image).items()
            if value is not None and value is not False}


def _manifest_from_json(data: dict) -> Manifest:
    """A Manifest from the file's JSON. Raises KeyError for a missing
    field and TypeError for a field of the wrong type."""
    stacklets = _expect(data["stacklets"], dict)
    return Manifest(
        kind=_expect(data["kind"], str), name=_expect(data["name"], str),
        version=_expect(data["version"], str), commit=_expect(data["commit"], str),
        applies=_optional(data.get("applies"), str),
        stacklets={sid: _record_from_json(r) for sid, r in stacklets.items()})


def _record_from_json(data: dict) -> StackletRecord:
    images = _expect(data["images"], dict)
    return StackletRecord(
        up=_expect(data["up"], bool), commit=_optional(data.get("commit"), str),
        migrations=[_expect(m, str) for m in _expect(data["migrations"], list)],
        images={service: _image_from_json(image) for service, image in images.items()})


def _image_from_json(data: dict) -> Image:
    return Image(name=_expect(data["name"], str),
                 id=_optional(data.get("id"), str),
                 digest=_optional(data.get("digest"), str),
                 built=_expect(data.get("built", False), bool),
                 base=_optional(data.get("base"), str))


def _expect(value, kind: type):
    if not isinstance(value, kind):
        raise TypeError(f"expected {kind.__name__}, found {type(value).__name__}")
    return value


def _optional(value, kind: type):
    return None if value is None else _expect(value, kind)


def _sync_folder(folder: Path) -> None:
    """Flush the folder's entry for the new file. The rename then
    survives a power cut, not only a stopped process."""
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
