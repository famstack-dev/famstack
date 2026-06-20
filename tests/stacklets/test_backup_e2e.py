"""End-to-end tests for the backup pipeline against a real APFS volume.

Uses ``hdiutil`` to create a sparse APFS disk image and attach it.
From the kernel's perspective it's indistinguishable from a USB drive:
``chflags uchg`` enforces, ``diskutil`` recognizes it, ``rsync`` writes
real bytes. No external storage needed.

What these tests exercise that the unit suite mocks:

* ``stat -f %T`` against a real filesystem
* ``hdiutil attach`` / ``diskutil`` against a real volume
* ``/usr/bin/rsync`` writing real files
* Kernel-enforced ``chflags uchg`` immutability
* The engine subprocess as a black box (stdin/argv/env in, JSON out)
* The orchestrator → engine → vault flow via ``./stack backup sync``

What they don't:

* Cron sandbox — would touch the user's actual crontab
* TCC / Full Disk Access — opaque to the bundle being granted

macOS-only: ``hdiutil`` and BSD ``chflags`` are Mac-specific.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="backup E2E tests require macOS (hdiutil + chflags + APFS)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_SCRIPT = REPO_ROOT / "stacklets" / "backup" / "engines" / "external-disk" / "sync.py"
STACK_BIN = REPO_ROOT / "stack"


# ── APFS disk image fixture ───────────────────────────────────────────────

@pytest.fixture
def vault_image(tmp_path):
    """Create and attach a 100MB APFS sparse image. Yields ``(name, mount)``.

    The volume name is unique per test invocation (pid + ms timestamp)
    so concurrent runs and crash-leftovers don't collide. Detach is
    best-effort with retry, falling back to ``-force`` if uchg-locked
    files keep the volume busy.
    """
    dmg = tmp_path / "vault.dmg"
    name = f"famstack-test-vault-{os.getpid()}-{int(time.time() * 1000)}"

    subprocess.run(
        ["hdiutil", "create", "-size", "100m", "-fs", "APFS",
         "-volname", name, str(dmg)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["hdiutil", "attach", str(dmg)],
        check=True, capture_output=True,
    )
    mount = Path(f"/Volumes/{name}")
    assert mount.is_dir(), f"hdiutil attach didn't mount {mount}"

    try:
        yield name, mount
    finally:
        _detach_with_retry(mount)


def _detach_with_retry(mount: Path) -> None:
    """Detach a mounted dmg, retrying past transient busy errors.

    Falls back to ``-force`` after three attempts. Worst case: a stale
    ``/Volumes/<name>`` until reboot — annoying but not destructive.
    """
    for _ in range(3):
        result = subprocess.run(
            ["hdiutil", "detach", str(mount)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return
        time.sleep(0.3)
    subprocess.run(
        ["hdiutil", "detach", str(mount), "-force"],
        capture_output=True,
    )


# ── Other fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def backup_data_dir(tmp_path):
    """Post-install state for the engine: directory exists, canary
    planted with the expected content. The engine no longer creates
    the canary — that's on_install's job — so tests have to mirror it."""
    d = tmp_path / "backup-data"
    d.mkdir()
    (d / "canary").write_text("famstack-backup-canary-do-not-delete\n")
    return d


@pytest.fixture
def fake_sources(tmp_path):
    """Two source directories with real files. Matches the layout the
    real photos/docs manifests expect when ``{data_dir}`` is rendered.

    The photos manifest declares ``path = "{data_dir}/photos/library/library"``,
    so for the orchestrator's template rendering to land here,
    ``data_dir`` must be the parent of the photos+docs tree — i.e.
    ``tmp_path / "data"``, not ``tmp_path``.
    """
    data_dir = tmp_path / "data"
    photos = data_dir / "photos" / "library" / "library"
    docs = data_dir / "docs" / "paperless" / "media"
    photos.mkdir(parents=True)
    docs.mkdir(parents=True)
    for i in range(15):
        (photos / f"photo-{i:03d}.jpg").write_bytes(b"x" * 256)
    for i in range(12):
        (docs / f"doc-{i:03d}.pdf").write_bytes(b"y" * 256)
    return {"photos": photos, "docs": docs, "data_dir": data_dir}


# ── Helpers ────────────────────────────────────────────────────────────────

def _sources_env(fake_sources: dict, *, photos_min: int = 10, docs_min: int = 5) -> str:
    """Build the ``$SOURCES`` env string the engine expects."""
    return "\n".join([
        f"photos/library|Photos|{fake_sources['photos']}|data/photos-library|{photos_min}",
        f"docs/media|Documents|{fake_sources['docs']}|data/docs-media|{docs_min}",
    ])


def _run_engine(backup_data_dir: Path, vault_name: str, sources_env: str,
                *, args=None):
    env = os.environ.copy()
    env["BACKUP_DATA_DIR"] = str(backup_data_dir)
    env["VAULT_DISK"] = vault_name
    env["SOURCES"] = sources_env
    cmd = [sys.executable, str(ENGINE_SCRIPT)] + (args or [])
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)


def _read_result(backup_data_dir: Path) -> dict:
    """Read the latest run from history.jsonl (last good JSON line)."""
    history = backup_data_dir / "logs" / "history.jsonl"
    latest = None
    for line in history.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            latest = json.loads(line)
        except json.JSONDecodeError:
            continue
    assert latest is not None, f"history.jsonl had no parseable lines: {history}"
    return latest


def _has_uchg(path: Path) -> bool:
    """True if the BSD uchg flag is set on ``path``. ``ls -lO`` shows
    the flag inline; we look for ``uchg`` as a word in the output."""
    result = subprocess.run(["ls", "-ldO", str(path)],
                            capture_output=True, text=True)
    return "uchg" in result.stdout.split()


# ── Engine E2E ─────────────────────────────────────────────────────────────

class TestEngineSyncE2E:
    def test_first_sync_writes_and_locks_files(
        self, vault_image, backup_data_dir, fake_sources
    ):
        name, mount = vault_image
        result = _run_engine(backup_data_dir, name,
                             _sources_env(fake_sources), args=["--no-eject"])

        assert result.returncode == 0, (
            f"engine failed (exit {result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        vault_photos = mount / "data" / "photos-library"
        vault_docs = mount / "data" / "docs-media"
        assert vault_photos.is_dir()
        assert vault_docs.is_dir()

        photos = list(vault_photos.glob("*.jpg"))
        assert len(photos) == 15
        for p in photos:
            assert _has_uchg(p), f"{p.name} is not uchg-locked"

        data = _read_result(backup_data_dir)
        assert data["success"] is True
        assert data["dry_run"] is False
        photo_result = next(s for s in data["sources"] if s["id"] == "photos/library")
        assert photo_result["new_files"] == 15
        assert photo_result["total_files"] == 15

    def test_immutable_files_resist_modification(
        self, vault_image, backup_data_dir, fake_sources
    ):
        """Sanity-check the kernel actually refuses to modify uchg files.

        If this test fails, something's wrong with the filesystem's
        BSD flag enforcement — the whole append-only contract relies
        on the kernel honoring ``uchg``.
        """
        name, mount = vault_image
        _run_engine(backup_data_dir, name, _sources_env(fake_sources),
                    args=["--no-eject"])

        locked = next((mount / "data" / "photos-library").glob("*.jpg"))
        with pytest.raises(PermissionError):
            locked.write_bytes(b"tampered")

    def test_second_sync_is_noop(
        self, vault_image, backup_data_dir, fake_sources
    ):
        """``--ignore-existing`` means a re-run with the same sources
        touches no files on the vault. No new uchg applications either."""
        name, mount = vault_image
        sources = _sources_env(fake_sources)
        _run_engine(backup_data_dir, name, sources, args=["--no-eject"])

        photos = mount / "data" / "photos-library"
        before = {p.name: p.stat().st_mtime for p in photos.iterdir()}

        result = _run_engine(backup_data_dir, name, sources, args=["--no-eject"])
        assert result.returncode == 0

        after = {p.name: p.stat().st_mtime for p in photos.iterdir()}
        assert before == after

        data = _read_result(backup_data_dir)
        assert all(s["new_files"] == 0 for s in data["sources"])

    def test_new_source_files_picked_up_on_next_run(
        self, vault_image, backup_data_dir, fake_sources
    ):
        """Add a file to the source between runs. The next sync writes
        and locks just the new one."""
        name, mount = vault_image
        sources = _sources_env(fake_sources)
        _run_engine(backup_data_dir, name, sources, args=["--no-eject"])

        (fake_sources["photos"] / "extra.jpg").write_bytes(b"z" * 256)

        result = _run_engine(backup_data_dir, name, sources, args=["--no-eject"])
        assert result.returncode == 0

        new_on_vault = mount / "data" / "photos-library" / "extra.jpg"
        assert new_on_vault.is_file()
        assert _has_uchg(new_on_vault)

        data = _read_result(backup_data_dir)
        photo_result = next(s for s in data["sources"] if s["id"] == "photos/library")
        assert photo_result["new_files"] == 1
        assert photo_result["total_files"] == 16

    def test_canary_deletion_between_runs_aborts(
        self, vault_image, backup_data_dir, fake_sources
    ):
        """Silent-rearm defense at the engine boundary. An attacker who
        knows about the tripwire mustn't be able to delete it between
        runs and slip past with a freshly-created replacement."""
        name, _ = vault_image
        sources = _sources_env(fake_sources)
        _run_engine(backup_data_dir, name, sources, args=["--no-eject"])

        canary = backup_data_dir / "canary"
        history = backup_data_dir / "logs" / "history.jsonl"
        assert canary.exists() and history.exists()

        # Attacker deletes the canary. history.jsonl stays as the
        # witness that a real previous run happened.
        canary.unlink()

        result = _run_engine(backup_data_dir, name, sources, args=["--no-eject"])
        assert result.returncode != 0

        data = _read_result(backup_data_dir)
        assert data["success"] is False
        reason = (data["failure_reason"] or "").lower()
        assert "canary" in reason and "missing" in reason

    def test_canary_tamper_aborts_sync(
        self, vault_image, backup_data_dir, fake_sources
    ):
        """The canary file is the precise ransomware tripwire. If its
        contents change between runs, the sync must abort before
        touching the vault."""
        name, mount = vault_image
        sources = _sources_env(fake_sources)
        _run_engine(backup_data_dir, name, sources, args=["--no-eject"])

        # Tamper. The previous run created the canary with the known
        # string; we overwrite it with garbage.
        (backup_data_dir / "canary").write_text("tampered\n")

        result = _run_engine(backup_data_dir, name, sources, args=["--no-eject"])
        assert result.returncode != 0

        data = _read_result(backup_data_dir)
        assert data["success"] is False
        assert "canary" in (data["failure_reason"] or "").lower()

    def test_refuses_when_source_under_minimum(
        self, vault_image, backup_data_dir, fake_sources
    ):
        """Preflight is the coarse ransomware guard — refuses to sync
        a source that's been wiped to fewer files than the declared
        minimum. Critically, no vault writes happen."""
        name, mount = vault_image
        # photos has 15 files; bump min to 100 so preflight fails
        sources = _sources_env(fake_sources, photos_min=100)

        result = _run_engine(backup_data_dir, name, sources, args=["--no-eject"])
        assert result.returncode != 0

        # The vault stays clean — preflight failure means we never
        # mounted (or in this case never wrote, since the disk was
        # already mounted by the test fixture).
        assert not (mount / "data" / "photos-library").exists()

        data = _read_result(backup_data_dir)
        assert data["success"] is False
        assert "preflight" in (data["failure_reason"] or "").lower()

    def test_dry_run_writes_no_files(
        self, vault_image, backup_data_dir, fake_sources
    ):
        """``--dry-run`` rsyncs in preview mode and skips locking. Vault
        contents unchanged after."""
        name, mount = vault_image
        result = _run_engine(backup_data_dir, name,
                             _sources_env(fake_sources), args=["--dry-run"])
        assert result.returncode == 0

        # Vault has nothing — no real writes happened
        data_dir = mount / "data"
        if data_dir.exists():
            assert not list(data_dir.iterdir())

        data = _read_result(backup_data_dir)
        assert data["dry_run"] is True
        assert data["success"] is True

    def test_refuses_non_apfs_filesystem(
        self, tmp_path, backup_data_dir, fake_sources
    ):
        """The probe is what stops us silently degrading to a non-WORM
        copy on a filesystem that doesn't honor BSD flags. Verify it
        actually fires against a real FAT32 volume."""
        dmg = tmp_path / "fat-vault.dmg"
        name = f"famstack-test-fat-{os.getpid()}-{int(time.time() * 1000)}"

        try:
            subprocess.run(
                ["hdiutil", "create", "-size", "10m", "-fs", "MS-DOS FAT32",
                 "-volname", name, str(dmg)],
                check=True, capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            pytest.skip(f"Couldn't create FAT32 test image: {e.stderr.decode()}")

        subprocess.run(["hdiutil", "attach", str(dmg)],
                       check=True, capture_output=True)
        mount = Path(f"/Volumes/{name}")
        try:
            result = _run_engine(backup_data_dir, name,
                                 _sources_env(fake_sources), args=["--no-eject"])
            assert result.returncode != 0

            data = _read_result(backup_data_dir)
            assert data["success"] is False
            # The error message should mention the filesystem or
            # immutability — we don't pin to exact wording, just the
            # category of failure.
            reason = (data["failure_reason"] or "").lower()
            assert "filesystem" in reason or "immutability" in reason
        finally:
            _detach_with_retry(mount)


# ── Orchestrator E2E ───────────────────────────────────────────────────────

class TestOrchestratorE2E:
    """End-to-end through the orchestrator: ``./stack backup sync`` with
    ``STACK_DIR`` pointed at a fake instance. Exercises the full chain
    from CLI dispatch through source discovery through engine subprocess
    to vault writes."""

    @pytest.fixture
    def test_instance(self, tmp_path, vault_image, fake_sources):
        name, _ = vault_image
        instance = tmp_path / "instance"
        instance.mkdir()

        # Symlink stacklets/ to the real repo — we test against the
        # real photos/docs manifests so source discovery exercises
        # the actual schema.
        (instance / "stacklets").symlink_to(REPO_ROOT / "stacklets")

        # data_dir is wherever fake_sources put the source tree.
        # photos/library/library/* and docs/paperless/media/* live
        # under tmp_path/data, so data_dir is tmp_path.
        data_dir = fake_sources["data_dir"]

        (instance / "stack.toml").write_text(
            f'[core]\n'
            f'data_dir = "{data_dir}"\n'
            f'\n'
            f'[backup.targets.{name}]\n'
            f'engine = "external-disk"\n'
            f'disk = "{name}"\n'
            f'schedule = "0 2 * * *"\n'
        )
        (instance / "users.toml").write_text("")

        # Simulate the post-install state. Real on_install plants the
        # canary alongside the .stack/backup.setup-done marker; the
        # silent-rearm check uses setup-done as its witness so missing
        # canary + present setup-done = tampering. The test fixture
        # has to mirror both, or the very first orchestrator run looks
        # like an attack.
        backup_state_dir = data_dir / "backup"
        backup_state_dir.mkdir(parents=True, exist_ok=True)
        (backup_state_dir / "canary").write_text(
            "famstack-backup-canary-do-not-delete\n"
        )

        # Setup-done markers gate source discovery — only enabled
        # stacklets contribute.
        stack_dir = instance / ".stack"
        stack_dir.mkdir()
        (stack_dir / "photos.setup-done").write_text("")
        (stack_dir / "docs.setup-done").write_text("")
        (stack_dir / "backup.setup-done").write_text("")
        (stack_dir / "secrets.toml").write_text("")

        return instance

    def test_stack_backup_sync_runs_engine_end_to_end(
        self, vault_image, test_instance
    ):
        """Invoke ``./stack backup sync`` via subprocess with
        ``STACK_DIR`` pointed at the fake instance. Verify:

        * the subprocess returns 0
        * files appear on the vault in the expected locations
        * each file has the uchg flag set

        Matrix notification is skipped automatically: the test
        instance's secrets file is empty, so the orchestrator's
        ``_post_notification`` returns early with "stacker-bot
        password not in secrets" and we continue.
        """
        name, mount = vault_image

        env = os.environ.copy()
        env["STACK_DIR"] = str(test_instance)

        result = subprocess.run(
            [str(STACK_BIN), "backup", "sync", "--no-eject"],
            env=env, capture_output=True, text=True, timeout=120,
        )

        assert result.returncode == 0, (
            f"./stack backup sync failed (exit {result.returncode}):\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

        vault_photos = mount / "data" / "photos-library"
        vault_docs = mount / "data" / "docs-media"
        assert vault_photos.is_dir(), \
            f"orchestrator didn't write photos to vault.\nstdout:\n{result.stdout}"
        assert vault_docs.is_dir()

        photos = list(vault_photos.glob("*.jpg"))
        docs = list(vault_docs.glob("*.pdf"))
        assert len(photos) == 15
        assert len(docs) == 12

        # Spot-check the uchg flag on a few files
        assert _has_uchg(photos[0])
        assert _has_uchg(docs[0])
