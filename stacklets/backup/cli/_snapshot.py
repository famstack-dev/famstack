"""Snapshots — the half of a backup rsync cannot do.

An archive is a pile of files that only ever grows. Photos, scanned
documents, voice recordings: written once, never edited, never deleted.
rsync with ``--ignore-existing`` handles that perfectly, and the vault
locks each new file immutable.

A database is the opposite. Its files change under you continuously, a
copy taken mid-write is unrestorable, and no amount of care with rsync
fixes that. What you want instead is one consistent dump per run.

A snapshot is that dump plus whatever small files must travel with it,
packed into a single dated tarball. Each run writes a new tarball and
never touches an old one, which turns a mutable database into exactly the
append-only shape the vault already knows how to keep. The tarballs then
reach the vault through the ordinary engine, as just another source.

Two consequences worth knowing:

*Local snapshots are a staging area, not the backup.* The vault copy is
the backup. The engine syncs with ``--ignore-existing`` and never
``--delete``, so :func:`prune_snapshots` can keep the internal disk small
without any risk of reaching the vault.

*Snapshots hold secrets.* A Synapse snapshot carries `homeserver.yaml`
(database password, macaroon key) and the signing key, because a dump
without them restores a homeserver nobody can log into. That is the right
trade, and it is a reason the vault disk is a physical object you hold.
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

from _orchestrator import SourceRecord


# How many tarballs to keep on the internal disk. Enough that a bad
# snapshot can be stepped over without reaching for the vault, small
# enough that a database an order of magnitude larger still fits.
DEFAULT_KEEP = 7


@dataclass
class SnapshotSpec:
    """One ``[[backup.snapshot]]`` entry, after template rendering."""

    id: str                     # "{stacklet_id}/{name}", e.g. "messages/synapse"
    display: str                # Human-readable, e.g. "Messages"
    name: str                   # "synapse"
    container: str              # Docker container running Postgres
    database: str               # Database to dump
    user: str                   # Postgres role to dump as
    include: List[str] = field(default_factory=list)  # extra files (globs ok)

    @property
    def stacklet_id(self) -> str:
        return self.id.split("/", 1)[0]

    @property
    def subdir(self) -> str:
        """Directory name for this snapshot's tarballs, on disk and in the
        vault. Namespaced so two stacklets can't collide."""
        return f"{self.stacklet_id}-{self.name}"


# ── Discovery ──────────────────────────────────────────────────────────────

def discover_snapshots(
    repo_root: Path, instance_dir: Path, data_dir: Path,
) -> List[SnapshotSpec]:
    """Walk every ``stacklets/*/stacklet.toml`` for ``[[backup.snapshot]]``.

    Mirrors ``discover_archive_sources``: a stacklet contributes only if
    it is enabled (``.stack/{id}.setup-done``), and ``{data_dir}`` is the
    one template variable rendered into paths.
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
                    # Unknown template var: keep the literal so the failure
                    # surfaces as a missing file rather than a crash.
                    includes.append(raw)
            specs.append(SnapshotSpec(
                id=f"{stacklet_id}/{name}",
                display=display,
                name=name,
                container=entry.get("container", ""),
                database=entry.get("database", ""),
                user=entry.get("user", ""),
                include=includes,
            ))

    return specs


# ── Taking one ─────────────────────────────────────────────────────────────

def pg_dump(spec: SnapshotSpec) -> bytes:
    """Dump `spec`'s database through the container running it.

    `pg_dump` takes its own consistent view via MVCC, so this runs
    against a live homeserver with nothing stopped and no downtime.
    """
    proc = subprocess.run(
        ["docker", "exec", spec.container,
         "pg_dump", "-U", spec.user, "-d", spec.database],
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip()[:400]
        raise RuntimeError(
            f"pg_dump failed for {spec.database} in {spec.container}: {detail}"
        )
    return proc.stdout


def container_versions(spec: SnapshotSpec) -> dict:
    """What was running when this snapshot was taken.

    A dump only restores into something compatible with what produced it,
    and the failure is not subtle: a Paperless 3.x database will not boot
    under 2.x, and there is no downgrade. Recording the versions is the
    one part of a snapshot that cannot be added later, so it happens even
    though nothing reads it yet.

    The image *digest* is the load-bearing field. Tags lie over time —
    the homeserver runs `matrixdotorg/synapse:latest`, which names a
    different image every month and nothing identifiable in five years.
    The digest still names this exact image whenever someone comes back
    to it.

    Containers are found by the compose project label rather than a
    manifest list, so a stacklet does not have to enumerate its own
    services and cannot forget one when it adds another.
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
        pg = _docker(
            "exec", spec.container,
            "psql", "-U", spec.user, "-d", spec.database,
            "-tAc", "show server_version;",
        )
        if pg:
            versions["postgres"] = pg
    return versions


def _docker(*args: str) -> str:
    """One docker call, stripped. Empty string when it fails: version
    metadata is nice to have, never worth losing a dump over."""
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

    Built in a temporary file and moved into place only once complete, so
    a dump that fails halfway leaves nothing behind. A half-written
    tarball would otherwise sync to the vault, get locked immutable, and
    sit there looking like a backup.
    """
    dump = dump or pg_dump
    versions = versions or container_versions
    stamp = time.strftime("%Y%m%dT%H%M%SZ", now or time.gmtime())

    # Raises on failure, before anything is created.
    sql = dump(spec)

    out_dir = out_root / spec.subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    # Second resolution reads well in a directory listing, but two runs
    # inside one second would otherwise land on the same name and the
    # second would overwrite the first. Overwriting is the one thing a
    # snapshot must never do, so a collision takes a counter instead.
    target = out_dir / f"{spec.name}-{stamp}.tar.gz"
    attempt = 2
    while target.exists():
        target = out_dir / f"{spec.name}-{stamp}-{attempt}.tar.gz"
        attempt += 1

    dump_name = f"{spec.database or spec.name}.sql"
    included: List[str] = []

    # Never worth losing the dump over: docker unreachable, a container
    # stopped, an image pruned. Recorded as empty rather than omitted, so
    # a reader can tell "we could not look" from "this predates version
    # recording at all".
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
                # A config file this install never created must not cost
                # the dump, which is the part that cannot be recreated.
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
    """What this tarball is, for whoever opens it without the code.

    A backup nobody can interpret is not a backup. The restore line is
    deliberately a literal command rather than a pointer to documentation
    that may not exist by then.
    """
    return {
        "famstack_snapshot": 1,
        "stacklet": spec.stacklet_id,
        "name": spec.name,
        "database": spec.database,
        "dump_file": dump_name,
        "included_files": included,
        # What produced this dump. A restore has to refuse an incompatible
        # target rather than discover it the hard way.
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
    """The engine source that carries this snapshot's tarballs to the vault.

    Marked ``rolling``: this directory is pruned to a fixed window on
    purpose, so its shrinking is normal operation rather than the data
    loss the engine's guard looks for.
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
    """Delete all but the newest `keep` tarballs; return what was removed.

    Safe by construction: the vault copy is the backup, and the engine
    syncs with ``--ignore-existing`` and never ``--delete``, so nothing
    here can reach it. Names sort chronologically because the timestamp
    is fixed-width and UTC.
    """
    if not directory.is_dir():
        return []
    tarballs = sorted(directory.glob("*.tar.gz"))
    doomed = tarballs[:-keep] if keep > 0 else tarballs
    for path in doomed:
        path.unlink(missing_ok=True)
    return doomed
