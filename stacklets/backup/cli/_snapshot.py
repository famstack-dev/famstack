"""Snapshots: capturing state that cannot be rsynced.

An archive source is a directory whose files are written once and never
changed, which rsync can copy incrementally. A database is not: its files
are rewritten continuously, and a copy taken while it is running does not
restore. Such state is dumped instead.

A snapshot is one dump per run, packed with the files that must accompany
it, into a dated tarball. Runs add tarballs and never modify an existing
one, which gives mutable state the same append-only shape the vault
already stores.

Two kinds of state are captured. A `postgres` snapshot dumps a database
inside its container. A `git` snapshot bundles a repository on the host
(`git bundle create --all`), which carries every branch, including
commits the repository's remote does not have yet. A repository changes
far less often than a database, so a git snapshot whose refs match the
newest local tarball is skipped rather than written again.

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
import re
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

from _orchestrator import SourceRecord, vault_subdir


# Tarballs retained on the internal disk. The vault retains all of them,
# so this bounds local disk use only.
DEFAULT_KEEP = 7

# `<name>-<UTC stamp>[-<n>].tar.gz`, as `take_snapshot` names a tarball.
# The suffix counts snapshots taken within the same second.
_TARBALL_NAME = re.compile(r"-(\d{8}T\d{6}Z)(?:-(\d+))?\.tar\.gz$")


class NothingToBackUp(Exception):
    """The state a snapshot would capture does not exist yet.

    A repository with no commit is the case today: a memory whose seed has
    not run. The run reports it and goes on, as the engine does with an
    archive that has never held a file.
    """


@dataclass
class SnapshotSpec:
    """One ``[[backup.snapshot]]`` entry, after template rendering."""

    # "{stacklet_id}/{name}", e.g. "messages/synapse". Doubles as the
    # directory holding this snapshot's tarballs, both in the staging
    # area and under the vault's `data/`.
    id: str
    display: str                # Human-readable, e.g. "Messages"
    name: str                   # "synapse"
    # Capture parameters, keyed by the mechanism that reads them. The
    # namespace lets a stacklet storing state elsewhere declare a
    # snapshot without the contract assuming Postgres. An entry sets
    # exactly one of them.
    postgres: dict = field(default_factory=dict)
    git: dict = field(default_factory=dict)
    include: List[str] = field(default_factory=list)  # extra files (globs ok)

    @property
    def kind(self) -> str:
        return "git" if self.git else "postgres"

    @property
    def repo(self) -> str:
        return self.git.get("repo", "")

    @property
    def subject(self) -> str:
        """What this snapshot captures, in words, for progress lines."""
        if self.kind == "git":
            return f"the repository at {self.repo}"
        return f"the database {self.database}"

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
            git = dict(entry.get("git", {}) or {})
            if "repo" in git:
                git["repo"] = _render(git["repo"], template_vars)
            specs.append(SnapshotSpec(
                id=f"{stacklet_id}/{name}",
                display=display,
                name=name,
                postgres=entry.get("postgres", {}) or {},
                git=git,
                include=[_render(raw, template_vars)
                         for raw in entry.get("include", [])],
            ))

    return specs


def _render(raw: str, template_vars: dict) -> str:
    """Render `{data_dir}` into one manifest path.

    An unrecognised variable is kept verbatim, so the problem appears
    later as a path that does not exist rather than as an exception
    during discovery.
    """
    try:
        return raw.format(**template_vars)
    except (KeyError, IndexError):
        return raw


# ── Taking one ─────────────────────────────────────────────────────────────

def pg_dump(spec: SnapshotSpec) -> bytes:
    """Default capture for a spec declaring `postgres` parameters."""
    return postgres.dump(spec.container, spec.database, spec.user)


def git_bundle(spec: SnapshotSpec) -> bytes:
    """Default capture for a spec declaring `git` parameters.

    `--all` takes every ref and HEAD, so `git clone` of the bundle checks
    out the branch the repository was on, and the remote-tracking refs
    show what each remote had at the time. Commits on a branch that no
    remote-tracking ref reaches had not been pushed anywhere
    (`git log --branches --not --remotes`). The bundle holds refs and
    objects only, never the repository's config, so remote URLs and the
    credentials in them stay behind. Bundling only reads the repository:
    it writes no ref, takes no lock and starts no gc.
    """
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "repo.bundle"
        _git(spec.repo, "bundle", "create", "--quiet", str(bundle), "--all")
        return bundle.read_bytes()


def git_refs(spec: SnapshotSpec) -> dict:
    """Return every ref of the repository and HEAD, name to commit.

    Two snapshots with the same refs hold the same history, so this is
    what decides whether a new tarball is needed. Raises RuntimeError
    when the path is not a git repository, and NothingToBackUp when it
    has no branch yet.
    """
    listing = _git(spec.repo, "for-each-ref",
                   "--format=%(objectname) %(refname)")
    refs = {}
    for line in listing.splitlines():
        commit, _, name = line.partition(" ")
        refs[name] = commit
    if not any(name.startswith("refs/heads/") for name in refs):
        raise NothingToBackUp(f"the repository at {spec.repo} has no commit yet")
    refs["HEAD"] = _git(spec.repo, "rev-parse", "--verify", "HEAD").strip()
    return refs


def _git(repo: str, *args: str) -> str:
    """Run one read-only git command in `repo` and return its stdout.

    Git must find exactly the repository at `repo`, or the wrong history
    is backed up. So the path must be absolute (`Path("")` is the working
    directory), inherited `GIT_*` variables such as `GIT_DIR` are dropped,
    and `GIT_CEILING_DIRECTORIES` stops git from walking up to an
    enclosing repository. `GIT_OPTIONAL_LOCKS=0` stops git from
    refreshing the index on the side, so nothing in the repository is
    written.
    """
    if not repo or not os.path.isabs(repo):
        raise RuntimeError(f"repository path must be absolute: {repo!r}")
    path = Path(repo)
    if not path.is_dir():
        raise RuntimeError(f"repository not found: {repo}")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CEILING_DIRECTORIES=str(path.parent), GIT_OPTIONAL_LOCKS="0")
    proc = subprocess.run(["git", "-C", str(path), *args],
                          capture_output=True, text=True, env=env, timeout=600)
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()
        raise RuntimeError(
            f"git {args[0]} failed in {repo}: "
            f"{detail[-1] if detail else f'exit {proc.returncode}'}"
        )
    return proc.stdout


def newest_refs(out_dir: Path) -> Optional[dict]:
    """Return the refs recorded in the newest tarball in `out_dir`.

    None when there is no tarball, or when its manifest records no refs
    or cannot be read; either way the next snapshot is written. Any
    failure to read counts: a truncated tarball raises EOFError or a zlib
    error, and letting that through would fail every later run while the
    bad file is never pruned.
    """
    tarballs = _by_age(out_dir.glob("*.tar.gz")) if out_dir.is_dir() else []
    if not tarballs:
        return None
    try:
        with tarfile.open(tarballs[-1]) as tar:
            member = tar.extractfile("MANIFEST.json")
            manifest = json.loads(member.read()) if member else {}
        return manifest.get("refs")
    except Exception:
        return None


def _by_age(tarballs) -> List[Path]:
    """Order tarballs oldest first: by stamp, then by same-second suffix.

    Plain name order puts `x-<stamp>-2.tar.gz` before `x-<stamp>.tar.gz`,
    because "-" sorts before ".". A name that does not parse sorts first,
    so it is pruned before any real snapshot.
    """
    def key(path: Path):
        m = _TARBALL_NAME.search(path.name)
        return (m.group(1), int(m.group(2) or 1)) if m else ("", 0)
    return sorted(tarballs, key=key)


def git_version(spec: SnapshotSpec) -> dict:
    """Return the version of the git that made the bundle.

    A bundle restores with plain git, not with any container image, so
    git's version is what a restore needs to know.
    """
    try:
        proc = subprocess.run(["git", "--version"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    return {"git": proc.stdout.strip()} if proc.returncode == 0 else {}


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
    refs: Optional[Callable[[SnapshotSpec], dict]] = None,
    versions: Optional[Callable[[SnapshotSpec], dict]] = None,
    now: Optional[time.struct_time] = None,
) -> Optional[Path]:
    """Write one dated tarball for `spec` and return its path.

    Returns None for a git snapshot whose refs match the newest tarball
    in the output directory: that history is already captured, and the
    tarball holding it stays until it is pruned. Raises NothingToBackUp
    for a repository with no commit yet.

    The tarball is assembled under a temporary name and moved into place
    only once complete. A partially written file left in the output
    directory would be picked up by the next sync and locked immutable on
    the vault, where it could not be replaced.
    """
    is_git = spec.kind == "git"
    dump = dump or (git_bundle if is_git else pg_dump)
    versions = versions or (git_version if is_git else container_versions)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", now or time.gmtime())
    out_dir = out_root / spec.id

    # Read before the bundle. A commit landing in between is then in the
    # bundle but not in the recorded refs, so the next run writes again;
    # read after, it would be recorded but missing from the bundle, and
    # every later run would skip it.
    recorded_refs = None
    if is_git:
        recorded_refs = (refs or git_refs)(spec)
        if recorded_refs == newest_refs(out_dir):
            return None

    # Taken first, so a failure propagates before any file exists.
    payload = dump(spec)

    out_dir.mkdir(parents=True, exist_ok=True)
    # The timestamp has second resolution, so two runs within the same
    # second would otherwise produce the same name. A suffix keeps them
    # distinct rather than letting the second overwrite the first.
    target = out_dir / f"{spec.name}-{stamp}.tar.gz"
    attempt = 2
    while target.exists():
        target = out_dir / f"{spec.name}-{stamp}-{attempt}.tar.gz"
        attempt += 1

    dump_name = (f"{spec.name}.bundle" if is_git
                 else f"{spec.database or spec.name}.sql")
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
            _add_bytes(tar, dump_name, payload)
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
                _manifest(spec, dump_name, stamp, included, recorded,
                          recorded_refs),
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
    versions: dict, refs: Optional[dict],
) -> dict:
    """Describe the tarball for a reader who does not have this code.

    `restore` holds a literal command rather than a reference to
    documentation, so the tarball remains self-describing if it is opened
    somewhere the project is not available.
    """
    common = {
        "famstack_snapshot": 1,
        "stacklet": spec.stacklet_id,
        "name": spec.name,
        "kind": spec.kind,
        "dump_file": dump_name,
        "included_files": included,
        "versions": versions,
        "taken_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.strptime(stamp, "%Y%m%dT%H%M%SZ"),
        ),
    }
    if spec.kind == "git":
        return {
            **common,
            "repository": spec.repo,
            "refs": refs,
            "restore": f"git clone {dump_name} {spec.name}",
            "note": (
                "The bundle holds every branch of the repository. `refs` "
                "shows what each remote had when the snapshot was taken. "
                "The clone's `origin` is the bundle file: point it at the "
                "real remote before pushing."
            ),
        }
    return {
        **common,
        "database": spec.database,
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
        src_path=out_root / spec.id,
        vault_subdir=vault_subdir(spec.id),
        rolling=True,
    )


# ── Keeping the internal disk honest ───────────────────────────────────────

def prune_snapshots(directory: Path, keep: int = DEFAULT_KEEP) -> List[Path]:
    """Delete all but the newest `keep` tarballs; return those removed.

    Only the internal disk is affected. The engine syncs with
    `--ignore-existing` and never `--delete`, so tarballs already on the
    vault are unaffected by anything removed here.

    Tarballs are ordered by their UTC stamp, then by the suffix that
    counts snapshots taken within one second.
    """
    if not directory.is_dir():
        return []
    tarballs = _by_age(directory.glob("*.tar.gz"))
    doomed = tarballs[:-keep] if keep > 0 else tarballs
    for path in doomed:
        path.unlink(missing_ok=True)
    return doomed
