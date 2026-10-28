"""Snapshots: capturing state that rsync cannot copy.

An archive source is a directory whose files are written once and never
changed. A database is not, so it is dumped instead: one consistent dump
per run, packed with the files that must accompany it, into a dated
tarball that later runs add to but never modify.

Synapse is the first stacklet wired up. Its media store holds the
recordings themselves, while the database holds the sender, room and
date that make each one a message.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_ROOT / "stacklets" / "backup" / "cli"))

from _snapshot import (  # noqa: E402
    NothingToBackUp,
    SnapshotSpec,
    git_refs,
    newest_refs,
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
        postgres={"container": "stack-messages-db",
                  "database": "synapse", "user": "synapse"},
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
        assert path.parent == out / "messages" / "synapse"
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
        """A dump alone does not restore a homeserver. The signing key is
        the server's identity, and homeserver.yaml holds the macaroon
        secret that keeps existing device logins valid."""
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
        """Installs differ in which optional config files exist. A
        pattern matching nothing is skipped, because the dump is the part
        that cannot be reproduced from elsewhere."""
        path = take_snapshot(
            _spec(tmp_path, include=[str(tmp_path / "nope.yaml")]),
            tmp_path / "s", dump=_fake_dump(),
        )
        with tarfile.open(path) as tar:
            assert "synapse.sql" in tar.getnames()

    def test_it_carries_a_manifest_describing_itself(self, tmp_path):
        """The tarball is self-describing, so it can be interpreted
        without this code available."""
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
        """Runs add tarballs and never modify an existing one, so a
        snapshot verified earlier stays as it was verified."""
        out = tmp_path / "s"
        first = take_snapshot(_spec(tmp_path), out, dump=_fake_dump("one"))
        second = take_snapshot(_spec(tmp_path), out, dump=_fake_dump("two"))

        assert first != second
        assert first.exists() and second.exists()

    def test_a_failed_dump_leaves_no_tarball(self, tmp_path):
        """A partial tarball would sync to the vault and be locked
        immutable there, so nothing is written unless the dump
        succeeds."""
        def boom(spec):
            raise RuntimeError("postgres is down")

        out = tmp_path / "s"
        with pytest.raises(RuntimeError):
            take_snapshot(_spec(tmp_path), out, dump=boom)

        assert list(out.glob("*.tar.gz")) == []


# ── Feeding the vault ────────────────────────────────────────────────────

class TestSnapshotSource:
    """Snapshots reach the vault through the ordinary engine. The
    output directory is registered as another append-only source."""

    def test_the_output_directory_becomes_a_source(self, tmp_path):
        src = snapshot_source(_spec(tmp_path), tmp_path / "snapshots")
        assert src.id == "messages/synapse"
        assert src.src_path == tmp_path / "snapshots" / "messages" / "synapse"
        assert src.vault_subdir == "data/messages/synapse"

    def test_it_is_marked_rolling(self, tmp_path):
        """The directory is pruned to a fixed size, so the engine's
        shrink check must not read that as data loss."""
        assert snapshot_source(_spec(tmp_path), tmp_path / "s").rolling is True


# ── Keeping the internal disk honest ─────────────────────────────────────

class TestPrune:
    """Local tarballs are a staging area; the vault copy is the backup.
    The engine syncs with `--ignore-existing` and never `--delete`, so
    pruning here does not affect the vault."""

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
name     = "synapse"
postgres = { container = "stack-messages-db", database = "synapse", user = "synapse" }
include  = ["{data_dir}/messages/synapse/homeserver.yaml"]
'''

    def test_it_finds_an_enabled_stacklets_snapshot(self, tmp_path):
        self._stacklet(tmp_path, "messages", self.MANIFEST)
        self._enable(tmp_path, "messages")

        found = discover_snapshots(tmp_path, tmp_path, Path("/data"))

        assert len(found) == 1
        spec = found[0]
        assert spec.id == "messages/synapse"
        # Namespaced in the manifest, read back through the accessor so
        # callers never touch the raw dict.
        assert spec.container == "stack-messages-db"
        assert spec.database == "synapse"
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
    """A dump loads only into a compatible version of the application
    that wrote it. Paperless is the concrete case: a 3.x database will not
    boot under 2.x, and there is no downgrade path.

    The snapshot therefore records what produced it. This describes a
    moment that has passed by the time a restore wants it, so it cannot
    be reconstructed later.
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
        """Tags are mutable, so a reference like `synapse:latest`
        resolves to different images over time. The digest identifies the
        exact one."""
        path = take_snapshot(_spec(tmp_path), tmp_path / "s",
                             dump=_fake_dump(), versions=self._versions())
        with tarfile.open(path) as tar:
            manifest = json.loads(tar.extractfile("MANIFEST.json").read())

        synapse = manifest["versions"]["containers"]["stack-messages-synapse"]
        assert synapse["digest"].startswith("sha256:")

    def test_unavailable_versions_do_not_cost_the_dump(self, tmp_path):
        """Version lookup can fail for reasons unrelated to the data:
        docker unreachable, a container stopped, an image pruned. The dump
        still proceeds."""
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


class TestTheShippedManifest:
    """Parses the real `stacklets/messages/stacklet.toml` rather than a
    fixture.

    The other discovery tests write their own manifest, so they verify the
    parser against an example the test also wrote. They stay green if the
    declaration that actually ships is malformed or renames a key, which
    is a failure only a live snapshot would otherwise reveal.
    """

    def _enabled(self, tmp_path: Path, stacklet_id: str) -> Path:
        (tmp_path / ".stack").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".stack" / f"{stacklet_id}.setup-done").touch()
        return tmp_path

    def test_the_messages_snapshot_declaration_parses(self, tmp_path):
        specs = discover_snapshots(
            _ROOT, self._enabled(tmp_path, "messages"), Path("/data"),
        )

        assert [s.id for s in specs] == ["messages/synapse"]
        spec = specs[0]
        assert spec.container == "stack-messages-db"
        assert spec.database == "synapse"
        assert spec.user == "synapse"

    def test_it_carries_the_files_a_homeserver_cannot_restore_without(
        self, tmp_path,
    ):
        spec = discover_snapshots(
            _ROOT, self._enabled(tmp_path, "messages"), Path("/data"),
        )[0]

        assert any(p.endswith("homeserver.yaml") for p in spec.include)
        assert any(p.endswith("*.signing.key") for p in spec.include)
        # `{data_dir}` is rendered, not passed through to the glob.
        assert all(p.startswith("/data/") for p in spec.include)


# ── The family memory: a git repository ─────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Marge Simpson",
         "-c", "user.email=marge@simpson.test", *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _clone_with_unpushed_commit(tmp_path: Path) -> Path:
    """A clone laid out as the curator keeps it.

    Forgejo's repository starts with a seed commit, and the clone made
    from it keeps `origin` at that commit. The curator pushes through its
    own remote, `curator`. One commit was written while Forgejo was down
    and is on no remote.
    """
    forgejo = tmp_path / "forgejo-memory.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(forgejo)],
                   check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "README.md").write_text("# Memory\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-q", "-m", "seed")
    _git(seed, "push", "-q", str(forgejo), "main")

    clone = tmp_path / "data" / "memory" / "vault"
    clone.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(forgejo), str(clone)],
                   check=True, capture_output=True)
    _git(clone, "remote", "add", "curator", str(forgejo))
    (clone / "lisa.md").write_text("# Lisa\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-q", "-m", "add lisa")
    _git(clone, "push", "-q", "curator", "main")
    (clone / "diary.md").write_text("Saxophone recital on Friday.\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-q", "-m", "diary: recital")
    return clone


def _git_spec(repo: Path) -> SnapshotSpec:
    return SnapshotSpec(id="memory/vault", display="Memory", name="vault",
                        git={"repo": str(repo)})


def _no_versions(spec):
    return {}


def _manifest_of(tarball: Path) -> dict:
    with tarfile.open(tarball) as tar:
        return json.loads(tar.extractfile("MANIFEST.json").read())


class TestGitSnapshot:
    """The memory's cards, diary and lists live in the curator's clone
    and in Forgejo, both on the Mac's own disk. A git snapshot puts the
    clone's whole history on the backup disk as a bundle."""

    def test_the_bundle_clones_with_the_unpushed_commit(self, tmp_path):
        clone = _clone_with_unpushed_commit(tmp_path)
        path = take_snapshot(_git_spec(clone), tmp_path / "s",
                             versions=_no_versions)

        assert path.parent == tmp_path / "s" / "memory" / "vault"
        unpacked = tmp_path / "unpacked"
        with tarfile.open(path) as tar:
            tar.extractall(unpacked, filter="data")
        restored = tmp_path / "restored"
        subprocess.run(["git", "clone", "-q", str(unpacked / "vault.bundle"),
                        str(restored)], check=True, capture_output=True)

        log = _git(restored, "log", "--format=%s")
        assert log.splitlines() == ["diary: recital", "add lisa", "seed"]
        assert (restored / "diary.md").read_text().startswith("Saxophone")

    def test_the_manifest_gives_a_git_restore_and_the_refs(self, tmp_path):
        clone = _clone_with_unpushed_commit(tmp_path)
        manifest = _manifest_of(take_snapshot(
            _git_spec(clone), tmp_path / "s", versions=_no_versions))

        assert manifest["kind"] == "git"
        assert manifest["dump_file"] == "vault.bundle"
        assert manifest["restore"] == "git clone vault.bundle vault"
        assert "psql" not in manifest["restore"]
        # What each remote had: the curator pushed "add lisa", the commit
        # on main after it waits, and origin stays at the seed.
        refs = manifest["refs"]
        assert refs["HEAD"] == refs["refs/heads/main"]
        assert refs["refs/remotes/curator/main"] == \
            _git(clone, "rev-parse", "main~1").strip()
        assert _git(clone, "log", "--format=%s", "--branches",
                    "--not", "--remotes").splitlines() == ["diary: recital"]
        assert "refs/remotes/origin/main" in refs

    def test_unchanged_refs_write_no_new_tarball(self, tmp_path):
        clone = _clone_with_unpushed_commit(tmp_path)
        out = tmp_path / "s"
        first = take_snapshot(_git_spec(clone), out, versions=_no_versions)

        assert take_snapshot(_git_spec(clone), out,
                             versions=_no_versions) is None
        assert list((out / "memory" / "vault").glob("*.tar.gz")) == [first]

    def test_a_new_commit_writes_a_new_tarball(self, tmp_path):
        clone = _clone_with_unpushed_commit(tmp_path)
        out = tmp_path / "s"
        take_snapshot(_git_spec(clone), out, versions=_no_versions)
        (clone / "bart.md").write_text("# Bart\n")
        _git(clone, "add", ".")
        _git(clone, "commit", "-q", "-m", "add bart")

        assert take_snapshot(_git_spec(clone), out,
                             versions=_no_versions) is not None
        assert len(list((out / "memory" / "vault").glob("*.tar.gz"))) == 2

    def test_a_missing_clone_fails_and_writes_nothing(self, tmp_path):
        out = tmp_path / "s"
        with pytest.raises(RuntimeError, match="not found"):
            take_snapshot(_git_spec(tmp_path / "gone"), out,
                          versions=_no_versions)
        assert not (out / "memory" / "vault").exists()

    def test_a_directory_that_is_not_a_repository_fails(self, tmp_path):
        """git would otherwise walk up to an enclosing repository and
        back up the wrong history."""
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        plain = tmp_path / "data" / "memory" / "vault"
        plain.mkdir(parents=True)
        with pytest.raises(RuntimeError, match="for-each-ref failed"):
            git_refs(_git_spec(plain))

    def test_the_clone_is_not_written(self, tmp_path):
        """No fetch, no gc, no lock: the curator is the clone's one
        writer."""
        clone = _clone_with_unpushed_commit(tmp_path)
        before = {p: p.stat().st_mtime_ns
                  for p in (clone / ".git").rglob("*") if p.is_file()}
        take_snapshot(_git_spec(clone), tmp_path / "s", versions=_no_versions)
        after = {p: p.stat().st_mtime_ns
                 for p in (clone / ".git").rglob("*") if p.is_file()}
        assert after == before


class TestGitSnapshotEdges:

    def test_an_empty_repository_has_nothing_to_back_up(self, tmp_path):
        """A memory whose seed has not run has no commit. That is a new
        install, not a failure."""
        empty = tmp_path / "data" / "memory" / "vault"
        empty.parent.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(empty)], check=True)
        out = tmp_path / "s"
        with pytest.raises(NothingToBackUp):
            take_snapshot(_git_spec(empty), out, versions=_no_versions)
        assert not (out / "memory" / "vault").exists()

    def test_a_truncated_newest_tarball_means_a_new_one(self, tmp_path):
        """Reading the previous manifest must never fail the run: the bad
        file would stay newest and break every night after."""
        clone = _clone_with_unpushed_commit(tmp_path)
        out = tmp_path / "s"
        first = take_snapshot(_git_spec(clone), out, versions=_no_versions)
        first.write_bytes(first.read_bytes()[:200])

        assert newest_refs(first.parent) is None
        assert take_snapshot(_git_spec(clone), out,
                             versions=_no_versions) is not None

    def test_a_relative_repo_path_is_refused(self, tmp_path, monkeypatch):
        """`Path("")` is the working directory, and git would search
        upward from there for some other repository."""
        monkeypatch.chdir(_ROOT)
        for repo in ("", "data/memory/vault"):
            spec = SnapshotSpec(id="memory/vault", display="Memory",
                                name="vault", git={"repo": repo})
            with pytest.raises(RuntimeError, match="absolute"):
                git_refs(spec)

    def test_an_inherited_git_dir_does_not_redirect_git(
        self, tmp_path, monkeypatch,
    ):
        clone = _clone_with_unpushed_commit(tmp_path)
        head = _git(clone, "rev-parse", "HEAD").strip()
        monkeypatch.setenv("GIT_DIR", str(_ROOT / ".git"))
        assert git_refs(_git_spec(clone))["HEAD"] == head

    def test_the_newest_is_read_past_a_same_second_suffix(self, tmp_path):
        """`x-<stamp>-2.tar.gz` sorts before `x-<stamp>.tar.gz` by name,
        but it is the later of the two."""
        clone = _clone_with_unpushed_commit(tmp_path)
        out = tmp_path / "s"
        stamp = time.gmtime(0)
        take_snapshot(_git_spec(clone), out, versions=_no_versions, now=stamp)
        (clone / "bart.md").write_text("# Bart\n")
        _git(clone, "add", ".")
        _git(clone, "commit", "-q", "-m", "add bart")
        second = take_snapshot(_git_spec(clone), out, versions=_no_versions,
                               now=stamp)

        assert second.name.endswith("-2.tar.gz")
        assert take_snapshot(_git_spec(clone), out, versions=_no_versions,
                             now=stamp) is None
        prune_snapshots(second.parent, keep=1)
        assert list(second.parent.glob("*.tar.gz")) == [second]

    def test_it_records_the_git_that_made_the_bundle(self, tmp_path):
        """A bundle restores with plain git, so git's version is what a
        restore needs, not the memory containers' images."""
        clone = _clone_with_unpushed_commit(tmp_path)
        manifest = _manifest_of(take_snapshot(_git_spec(clone), tmp_path / "s"))
        assert manifest["versions"]["git"].startswith("git version ")
        assert "containers" not in manifest["versions"]

    def test_no_remote_url_reaches_the_bundle(self, tmp_path):
        """Remote URLs carry credentials. A bundle holds refs and objects,
        never the repository's config."""
        clone = _clone_with_unpushed_commit(tmp_path)
        _git(clone, "remote", "set-url", "curator",
             "http://memory-bot:s3cret@stack-code:3000/family/memory.git")
        path = take_snapshot(_git_spec(clone), tmp_path / "s",
                             versions=_no_versions)
        with tarfile.open(path) as tar:
            blob = b"".join(tar.extractfile(m).read() for m in tar.getmembers())
        assert b"s3cret" not in blob
