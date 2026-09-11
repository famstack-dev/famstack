"""Snapshots: capturing state that cannot be rsynced.

An archive source is a directory whose files are written once and never
changed, which rsync can copy incrementally. A database is not: its files
are rewritten continuously, and a copy taken while it is running does not
restore. Such state is dumped instead.

A snapshot is one dump per run, packed with the files that must accompany
it, into a dated tarball. Runs add tarballs and never modify an existing
one, which gives mutable state the same append-only shape the vault
already stores.

The tarballs on the internal disk are a staging area; the copy on the
vault is the backup. `prune_snapshots` can therefore delete local
tarballs freely, because the engine syncs with `--ignore-existing` and
never passes `--delete`.
"""

from __future__ import annotations

import glob
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover — py < 3.11 fallback
    from stack._vendor import tomli as tomllib  # type: ignore

from stack import postgres

from _orchestrator import SourceRecord


# Tarballs retained on the internal disk. The vault retains all of them,
# so this bounds local disk use only.
DEFAULT_KEEP = 7


@dataclass
class SnapshotSpec:
    """One ``[[backup.snapshot]]`` entry, after template rendering."""

    id: str                     # "{stacklet_id}/{name}", e.g. "messages/synapse"
    display: str                # Human-readable, e.g. "Messages"
    name: str                   # "synapse"
    # Capture parameters, keyed by the mechanism that reads them. The
    # namespace lets a stacklet storing state elsewhere declare a
    # snapshot without the contract assuming Postgres.
    postgres: dict = field(default_factory=dict)
    include: List[str] = field(default_factory=list)  # extra files (globs ok)

    @property
    def container(self) -> str:
        return self.postgres.get("container", "")

    @property
    def database(self) -> str:
        return self.postgres.get("database", "")

    @property
    def user(self) -> str:
        return self.postgres.get("user", "")

    @property
    def stacklet_id(self) -> str:
        return self.id.split("/", 1)[0]

    @property
    def subdir(self) -> str:
        """Directory holding this snapshot's tarballs, used both on the
        internal disk and under the vault's `data/`. Qualified by stacklet
        so two stacklets choosing the same `name` do not collide."""
        return f"{self.stacklet_id}-{self.name}"


# ── Discovery ──────────────────────────────────────────────────────────────

def discover_snapshots(
    repo_root: Path, instance_dir: Path, data_dir: Path,
) -> List[SnapshotSpec]:
    """Return the ``[[backup.snapshot]]`` entries of enabled stacklets.

    Follows the same rules as ``discover_archive_sources``: a stacklet
    counts as enabled when `.stack/{id}.setup-done` exists, and
    `{data_dir}` is the only template variable rendered into paths.
    """
    stacklets_dir = repo_root / "stacklets"
    if not stacklets_dir.is_dir():
        return []

    template_vars = {"data_dir": str(data_dir)}
    specs: List[SnapshotSpec] = []

    for manifest_path in sorted(stacklets_dir.glob("*/stacklet.toml")):
        stacklet_id = manifest_path.parent.name
        if not (instance_dir / ".stack" / f"{stacklet_id}.setup-done").exists():
            continue

        try:
            with open(manifest_path, "rb") as f:
                manifest = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            continue

        entries = manifest.get("backup", {}).get("snapshot", [])
        display = manifest.get("name", stacklet_id)

        for entry in entries:
            name = entry.get("name", "default")
            includes = []
            for raw in entry.get("include", []):
                try:
                    includes.append(raw.format(**template_vars))
                except (KeyError, IndexError):
                    # An unrecognised variable is kept verbatim, so the
                    # problem appears later as a file that did not match
                    # rather than as an exception during discovery.
                    includes.append(raw)
            specs.append(SnapshotSpec(
                id=f"{stacklet_id}/{name}",
                display=display,
                name=name,
                postgres=entry.get("postgres", {}) or {},
                include=includes,
            ))

    return specs


# ── Taking one ─────────────────────────────────────────────────────────────

def pg_dump(spec: SnapshotSpec) -> bytes:
    """Default capture for a spec declaring `postgres` parameters."""
    return postgres.dump(spec.container, spec.database, spec.user)


def container_versions(spec: SnapshotSpec) -> dict:
    """Return the images and Postgres version present at snapshot time.

    A dump loads only into a compatible version of the application that
    produced it, so a restore needs to know what that was. Nothing reads
    this yet; it is recorded now because it describes a moment that has
    passed by the time anything wants it.

    Both the tag and the digest are kept. Tags are mutable, so a
    reference like `synapse:latest` resolves to different images over
    time and only the digest identifies the exact one.

    Containers are found through the compose project label rather than a
    list in the manifest, so a stacklet that gains a service does not
    also have to remember to declare it here.
    """
    project = f"stack-{spec.stacklet_id}"
    names = _docker(
        "ps", "--filter", f"label=com.docker.compose.project={project}",
        "--format", "{{.Names}}",
    ).split()

    containers = {}
    for name in names:
        image = _docker("inspect", name, "--format", "{{.Config.Image}}")
        version = _docker(
            "inspect", name, "--format",
            '{{index .Config.Labels "org.opencontainers.image.version"}}',
        )
        digest = _docker(
            "image", "inspect", image, "--format",
            "{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}",
        )
        entry = {"image": image}
        if version and version != "<no value>":
            entry["version"] = version
        if "@" in digest:
            entry["digest"] = digest.split("@", 1)[1]
        containers[name] = entry

    versions: dict = {"containers": containers}
    if spec.container:
        pg = postgres.server_version(spec.container, spec.database, spec.user)
        if pg:
            versions["postgres"] = pg
    return versions


def _docker(*args: str) -> str:
    """Run one docker command and return its stripped stdout.

    Any failure yields an empty string. Callers use this for metadata
    only, where an absent value is preferable to an aborted snapshot.
    """
    try:
        proc = subprocess.run(["docker", *args], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.decode(errors="replace").strip()


def take_snapshot(
    spec: SnapshotSpec,
    out_root: Path,
    *,
    dump: Optional[Callable[[SnapshotSpec], bytes]] = None,
    versions: Optional[Callable[[SnapshotSpec], dict]] = None,
    now: Optional[time.struct_time] = None,
) -> Path:
    """Write one dated tarball for `spec` and return its path.

    The tarball is assembled under a temporary name and moved into place
    only once complete. A partially written file left in the output
    directory would be picked up by the next sync and locked immutable on
    the vault, where it could not be replaced.
    """
    dump = dump or pg_dump
    versions = versions or container_versions
    stamp = time.strftime("%Y%m%dT%H%M%SZ", now or time.gmtime())

    # Taken first, so a failure propagates before any file exists.
    sql = dump(spec)

    out_dir = out_root / spec.subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    # The timestamp has second resolution, so two runs within the same
    # second would otherwise produce the same name. A suffix keeps them
    # distinct rather than letting the second overwrite the first.
    target = out_dir / f"{spec.name}-{stamp}.tar.gz"
    attempt = 2
    while target.exists():
        target = out_dir / f"{spec.name}-{stamp}-{attempt}.tar.gz"
        attempt += 1

    dump_name = f"{spec.database or spec.name}.sql"
    included: List[str] = []

    # Recorded as an empty mapping rather than omitted, which
    # distinguishes a snapshot whose versions could not be read from one
    # written before versions were recorded at all.
    try:
        recorded = versions(spec)
    except Exception as e:
        logger_warn(f"could not record versions for {spec.id}: {e}")
        recorded = {}

    fd, tmp_path = tempfile.mkstemp(dir=out_dir, suffix=".tar.gz.partial")
    os.close(fd)
    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            _add_bytes(tar, dump_name, sql)
            for pattern in spec.include:
                # Patterns that match nothing are skipped. An install may
                # legitimately lack a file another one has, and that is
                # not a reason to discard the dump.
                for path in sorted(glob.glob(pattern)):
                    p = Path(path)
                    if p.is_file():
                        tar.add(p, arcname=p.name)
                        included.append(p.name)
            _add_bytes(tar, "MANIFEST.json", json.dumps(
                _manifest(spec, dump_name, stamp, included, recorded),
                indent=2,
            ).encode())
        os.replace(tmp_path, target)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    return target


def _add_bytes(tar: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mtime = int(time.time())
    tar.addfile(info, io.BytesIO(payload))


def logger_warn(msg: str) -> None:
    print(f"    warning: {msg}", file=sys.stderr)


def _manifest(
    spec: SnapshotSpec, dump_name: str, stamp: str, included: List[str],
    versions: dict,
) -> dict:
    """Describe the tarball for a reader who does not have this code.

    `restore` holds a literal command rather than a reference to
    documentation, so the tarball remains self-describing if it is opened
    somewhere the project is not available.
    """
    return {
        "famstack_snapshot": 1,
        "stacklet": spec.stacklet_id,
        "name": spec.name,
        "database": spec.database,
        "dump_file": dump_name,
        "included_files": included,
        "versions": versions,
        "taken_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.strptime(stamp, "%Y%m%dT%H%M%SZ"),
        ),
        "restore": (
            f"createdb -U {spec.user} {spec.database} && "
            f"psql -U {spec.user} -d {spec.database} -f {dump_name}"
        ),
        "note": (
            "Restore the database first, then the media files from the "
            "matching archive. Media without a database row is harmless; "
            "a database row without its media is a broken message."
        ),
    }


# ── Feeding the vault ──────────────────────────────────────────────────────

def snapshot_source(spec: SnapshotSpec, out_root: Path) -> SourceRecord:
    """Return the engine source carrying this snapshot's tarballs.

    Marked `rolling`, because `prune_snapshots` keeps the directory at a
    fixed size. Without that flag the engine's shrink check would read
    routine pruning as data loss.
    """
    return SourceRecord(
        id=spec.id,
        display=spec.display,
        src_path=out_root / spec.subdir,
        vault_subdir=f"data/{spec.subdir}",
        rolling=True,
    )


# ── Keeping the internal disk honest ───────────────────────────────────────

def prune_snapshots(directory: Path, keep: int = DEFAULT_KEEP) -> List[Path]:
    """Delete all but the newest `keep` tarballs; return those removed.

    Only the internal disk is affected. The engine syncs with
    `--ignore-existing` and never `--delete`, so tarballs already on the
    vault are unaffected by anything removed here.

    Ordering by name is ordering by time, because the timestamp is
    fixed-width and UTC.
    """
    if not directory.is_dir():
        return []
    tarballs = sorted(directory.glob("*.tar.gz"))
    doomed = tarballs[:-keep] if keep > 0 else tarballs
    for path in doomed:
        path.unlink(missing_ok=True)
    return doomed
