"""Snapshots — the half of a backup that rsync cannot do.

An archive is a pile of files that only ever grows, which rsync handles
perfectly. A database is not that: its files change under you, a copy
taken mid-write is unrestorable, and what you actually want is one
consistent dump per run.

So a snapshot is a `pg_dump` plus whatever small files must travel with
it, packed into one dated tarball. Each run writes a new tarball and
never touches an old one, which turns a mutable database into exactly the
append-only shape the vault already knows how to keep.

Synapse is the first instance. It is also the one that matters most: the
media store holds the family's voice messages, and without the database
those are anonymous blobs with no sender, no room and no date.
"""

from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "stacklets" / "backup" / "cli"))

from _snapshot import (  # noqa: E402
    SnapshotSpec,
    discover_snapshots,
    prune_snapshots,
    snapshot_source,
    take_snapshot,
)


def _spec(tmp_path, **kw) -> SnapshotSpec:
    defaults = dict(
        id="messages/synapse",
        display="Messages",
        name="synapse",
        container="stack-messages-db",
        database="synapse",
        user="synapse",
        include=[],
    )
    defaults.update(kw)
    return SnapshotSpec(**defaults)


def _fake_dump(text: str = "-- pg_dump output\nCREATE TABLE events();\n"):
    """Stand-in for `docker exec ... pg_dump`, so tests need no Postgres."""
    calls: list[dict] = []

    def run(spec: SnapshotSpec) -> bytes:
        calls.append({"container": spec.container, "database": spec.database,
                      "user": spec.user})
        return text.encode()

    run.calls = calls  # type: ignore[attr-defined]
    return run


# ── Taking one ───────────────────────────────────────────────────────────

class TestTakeSnapshot:

    def test_it_writes_one_dated_tarball(self, tmp_path):
        out = tmp_path / "snapshots"
        path = take_snapshot(_spec(tmp_path), out, dump=_fake_dump())

        # Namespaced per stacklet+name so two stacklets cannot collide.
        assert path.parent == out / "messages-synapse"
        assert path.suffixes[-2:] == [".tar", ".gz"]
        assert path.name.startswith("synapse-")
        assert path.exists()

    def test_the_dump_is_inside(self, tmp_path):
        path = take_snapshot(
            _spec(tmp_path), tmp_path / "s",
            dump=_fake_dump("-- the whole database\n"),
        )
        with tarfile.open(path) as tar:
            names = tar.getnames()
            assert "synapse.sql" in names
            body = tar.extractfile("synapse.sql").read().decode()
        assert "the whole database" in body

    def test_named_files_travel_with_the_dump(self, tmp_path):
        """A Synapse dump alone does not restore a homeserver: the signing
        key is its identity and homeserver.yaml holds the secrets that keep
        existing logins valid."""
        cfg = tmp_path / "synapse"
        cfg.mkdir()
        (cfg / "homeserver.yaml").write_text("{}")
        (cfg / "simpson.signing.key").write_text("key")

        path = take_snapshot(
            _spec(tmp_path, include=[str(cfg / "homeserver.yaml"),
                                     str(cfg / "*.signing.key")]),
            tmp_path / "s", dump=_fake_dump(),
        )
        with tarfile.open(path) as tar:
            names = tar.getnames()
        assert "homeserver.yaml" in names
        assert "simpson.signing.key" in names

    def test_a_missing_include_is_skipped_not_fatal(self, tmp_path):
        """A config file an install never created must not cost you the
        database dump, which is the part that cannot be recreated."""
        path = take_snapshot(
            _spec(tmp_path, include=[str(tmp_path / "nope.yaml")]),
            tmp_path / "s", dump=_fake_dump(),
        )
        with tarfile.open(path) as tar:
            assert "synapse.sql" in tar.getnames()

    def test_it_carries_a_manifest_describing_itself(self, tmp_path):
        """Whoever opens this in five years will not have the code that
        wrote it. The tarball has to say what it is and how to put it
        back."""
        path = take_snapshot(_spec(tmp_path), tmp_path / "s",
                             dump=_fake_dump())
        with tarfile.open(path) as tar:
            manifest = json.loads(tar.extractfile("MANIFEST.json").read())

        assert manifest["stacklet"] == "messages"
        assert manifest["database"] == "synapse"
        assert manifest["dump_file"] == "synapse.sql"
        assert manifest["taken_at"].endswith("Z")
        assert "psql" in manifest["restore"]

    def test_each_run_adds_a_tarball_and_keeps_the_old_one(self, tmp_path):
        """The append-only contract, at the snapshot level. A run must
        never overwrite the snapshot that proved restorable yesterday."""
        out = tmp_path / "s"
        first = take_snapshot(_spec(tmp_path), out, dump=_fake_dump("one"))
        second = take_snapshot(_spec(tmp_path), out, dump=_fake_dump("two"))

        assert first != second
        assert first.exists() and second.exists()

    def test_a_failed_dump_leaves_no_tarball(self, tmp_path):
        """A half-written snapshot is worse than none: it would sync to
        the vault, get locked immutable, and look like a backup."""
        def boom(spec):
            raise RuntimeError("postgres is down")

        out = tmp_path / "s"
        with pytest.raises(RuntimeError):
            take_snapshot(_spec(tmp_path), out, dump=boom)

        assert list(out.glob("*.tar.gz")) == []


# ── Feeding the vault ────────────────────────────────────────────────────

class TestSnapshotSource:
    """Snapshots reach the vault through the same engine as everything
    else: the output directory is just another append-only source."""

    def test_the_output_directory_becomes_a_source(self, tmp_path):
        src = snapshot_source(_spec(tmp_path), tmp_path / "snapshots")
        assert src.id == "messages/synapse"
        assert src.src_path == tmp_path / "snapshots" / "messages-synapse"
        assert src.vault_subdir == "data/messages-synapse"

    def test_it_is_marked_rolling(self, tmp_path):
        """Pruned to a fixed window on purpose, so the engine must not read
        its shrinking as the data loss its guard looks for."""
        assert snapshot_source(_spec(tmp_path), tmp_path / "s").rolling is True


# ── Keeping the internal disk honest ─────────────────────────────────────

class TestPrune:
    """Local snapshots are a staging area, not the backup. The vault copy
    is the backup, and the engine syncs with `--ignore-existing` and no
    `--delete`, so pruning here can never reach it."""

    def _fill(self, d: Path, n: int) -> list[Path]:
        d.mkdir(parents=True, exist_ok=True)
        made = []
        for i in range(n):
            p = d / f"synapse-2026091{i}T000000Z.tar.gz"
            p.write_bytes(b"x")
            made.append(p)
        return made

    def test_it_keeps_the_newest_and_drops_the_rest(self, tmp_path):
        made = self._fill(tmp_path / "s", 5)
        prune_snapshots(tmp_path / "s", keep=2)
        left = sorted(p.name for p in (tmp_path / "s").glob("*.tar.gz"))
        assert left == sorted(p.name for p in made[-2:])

    def test_it_does_nothing_when_under_the_limit(self, tmp_path):
        self._fill(tmp_path / "s", 2)
        prune_snapshots(tmp_path / "s", keep=7)
        assert len(list((tmp_path / "s").glob("*.tar.gz"))) == 2

    def test_it_ignores_anything_that_is_not_a_snapshot(self, tmp_path):
        d = tmp_path / "s"
        self._fill(d, 3)
        (d / "README.txt").write_text("do not delete me")
        prune_snapshots(d, keep=1)
        assert (d / "README.txt").exists()


# ── Discovery ────────────────────────────────────────────────────────────

class TestDiscovery:
    """Same shape as archive discovery: walk enabled stacklets, read their
    manifests, render `{data_dir}`."""

    def _stacklet(self, root: Path, sid: str, body: str) -> None:
        d = root / "stacklets" / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "stacklet.toml").write_text(body)

    def _enable(self, instance: Path, sid: str) -> None:
        (instance / ".stack").mkdir(parents=True, exist_ok=True)
        (instance / ".stack" / f"{sid}.setup-done").touch()

    MANIFEST = '''
id = "messages"
name = "Messages"

[[backup.snapshot]]
name      = "synapse"
container = "stack-messages-db"
database  = "synapse"
user      = "synapse"
include   = ["{data_dir}/messages/synapse/homeserver.yaml"]
'''

    def test_it_finds_an_enabled_stacklets_snapshot(self, tmp_path):
        self._stacklet(tmp_path, "messages", self.MANIFEST)
        self._enable(tmp_path, "messages")

        found = discover_snapshots(tmp_path, tmp_path, Path("/data"))

        assert len(found) == 1
        spec = found[0]
        assert spec.id == "messages/synapse"
        assert spec.container == "stack-messages-db"
        assert spec.include == ["/data/messages/synapse/homeserver.yaml"]

    def test_a_disabled_stacklet_contributes_nothing(self, tmp_path):
        self._stacklet(tmp_path, "messages", self.MANIFEST)
        # no setup-done marker
        assert discover_snapshots(tmp_path, tmp_path, Path("/data")) == []

    def test_a_stacklet_without_snapshots_is_skipped(self, tmp_path):
        self._stacklet(tmp_path, "photos", 'id = "photos"\nname = "Photos"\n')
        self._enable(tmp_path, "photos")
        assert discover_snapshots(tmp_path, tmp_path, Path("/data")) == []


class TestRecordedVersions:
    """A dump is only restorable into something compatible with what wrote
    it. Paperless makes the point sharply: a 3.x database will not boot
    under 2.x, and there is no downgrade.

    So a snapshot records what produced it. This is the one piece of a
    snapshot that cannot be added later: whatever a restore tool eventually
    does, it can only be as good as the metadata captured at the time, and
    a tarball taken today without it stays ambiguous forever.
    """

    def _versions(self, payload=None, error=None):
        def probe(spec):
            if error is not None:
                raise error
            return payload if payload is not None else {
                "containers": {
                    "stack-messages-synapse": {
                        "image": "matrixdotorg/synapse:latest",
                        "version": "1.160.0",
                        "digest": "sha256:1231c84d",
                    },
                },
                "postgres": "16.15",
            }
        return probe

    def test_the_manifest_records_what_produced_it(self, tmp_path):
        path = take_snapshot(_spec(tmp_path), tmp_path / "s",
                             dump=_fake_dump(), versions=self._versions())
        with tarfile.open(path) as tar:
            manifest = json.loads(tar.extractfile("MANIFEST.json").read())

        synapse = manifest["versions"]["containers"]["stack-messages-synapse"]
        assert synapse["version"] == "1.160.0"
        assert manifest["versions"]["postgres"] == "16.15"

    def test_the_digest_is_kept_because_a_tag_is_not_a_version(self, tmp_path):
        """The running image is tagged `latest`, which means something
        different every year and nothing at all in five. The digest is the
        only identifier that still names this exact image later."""
        path = take_snapshot(_spec(tmp_path), tmp_path / "s",
                             dump=_fake_dump(), versions=self._versions())
        with tarfile.open(path) as tar:
            manifest = json.loads(tar.extractfile("MANIFEST.json").read())

        synapse = manifest["versions"]["containers"]["stack-messages-synapse"]
        assert synapse["digest"].startswith("sha256:")

    def test_unavailable_versions_do_not_cost_the_dump(self, tmp_path):
        """Docker unreachable, a container not running, an image pruned.
        None of that is worth losing the database over."""
        path = take_snapshot(
            _spec(tmp_path), tmp_path / "s", dump=_fake_dump(),
            versions=self._versions(error=RuntimeError("docker is not running")),
        )
        with tarfile.open(path) as tar:
            manifest = json.loads(tar.extractfile("MANIFEST.json").read())
            assert "synapse.sql" in tar.getnames()

        # Recorded as unknown rather than omitted, so a reader can tell
        # "we could not look" from "this predates version recording".
        assert manifest["versions"] == {}
