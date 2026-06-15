"""Unit tests for the backup stacklet's stack.toml target-config helper.

The helper does a narrow, comment-preserving block replacement in
stack.toml. These tests cover the create / replace / coexistence
cases plus a round-trip with the reader.
"""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "backup"))

from _config import read_target, write_target


# ── read_target ─────────────────────────────────────────────────────────────

class TestReadTarget:
    def test_returns_none_when_file_missing(self, tmp_path):
        assert read_target(tmp_path / "absent.toml", "vault") is None

    def test_returns_none_when_no_backup_section(self, tmp_path):
        p = tmp_path / "stack.toml"
        p.write_text('[core]\ndata_dir = "/x"\n')
        assert read_target(p, "vault") is None

    def test_returns_none_when_no_such_target(self, tmp_path):
        p = tmp_path / "stack.toml"
        p.write_text(
            '[backup.targets.offsite]\n'
            'engine = "restic"\n'
        )
        assert read_target(p, "vault") is None

    def test_returns_config_dict_when_target_present(self, tmp_path):
        p = tmp_path / "stack.toml"
        p.write_text(
            '[backup.targets.vault]\n'
            'engine = "external-disk"\n'
            'disk = "backup-vault"\n'
            'schedule = "0 2 * * *"\n'
        )
        cfg = read_target(p, "vault")
        assert cfg == {
            "engine": "external-disk",
            "disk": "backup-vault",
            "schedule": "0 2 * * *",
        }

    def test_returns_none_on_malformed_toml(self, tmp_path):
        # A broken stack.toml shouldn't blow up the orchestrator —
        # treat it like "no config" and let the caller decide.
        p = tmp_path / "stack.toml"
        p.write_text("this is not [valid] toml = ===")
        assert read_target(p, "vault") is None


# ── write_target — create cases ─────────────────────────────────────────────

class TestWriteTargetCreate:
    def test_creates_file_when_missing(self, tmp_path):
        p = tmp_path / "stack.toml"
        write_target(p, "vault", {"engine": "external-disk", "disk": "backup-vault"})
        assert p.exists()
        assert read_target(p, "vault") == {
            "engine": "external-disk",
            "disk": "backup-vault",
        }

    def test_appends_block_to_existing_file(self, tmp_path):
        p = tmp_path / "stack.toml"
        p.write_text(
            '[core]\n'
            'data_dir = "/x"\n'
            'timezone = "UTC"\n'
        )
        write_target(p, "vault", {"engine": "external-disk", "disk": "backup-vault"})

        text = p.read_text()
        # The [core] block is untouched.
        assert '[core]' in text
        assert 'data_dir = "/x"' in text
        # The new block is present.
        assert '[backup.targets.vault]' in text
        assert read_target(p, "vault")["disk"] == "backup-vault"

    def test_preserves_comments_in_unrelated_sections(self, tmp_path):
        # The header comment, the inline comment, and the standalone
        # comment between sections must all survive a write that
        # doesn't touch those sections.
        p = tmp_path / "stack.toml"
        original = (
            '# famstack stack.toml — household config\n'
            '\n'
            '[core]\n'
            'data_dir = "/x"   # absolute path\n'
            '\n'
            '# AI section below\n'
            '[ai]\n'
            'default = "model"\n'
        )
        p.write_text(original)
        write_target(p, "vault", {"engine": "external-disk"})

        text = p.read_text()
        assert '# famstack stack.toml — household config' in text
        assert 'data_dir = "/x"   # absolute path' in text
        assert '# AI section below' in text


# ── write_target — replace cases ────────────────────────────────────────────

class TestWriteTargetReplace:
    def test_replaces_existing_block(self, tmp_path):
        p = tmp_path / "stack.toml"
        p.write_text(
            '[backup.targets.vault]\n'
            'engine = "external-disk"\n'
            'disk = "old-name"\n'
            'schedule = "0 0 * * *"\n'
        )
        write_target(p, "vault", {
            "engine": "external-disk",
            "disk": "new-name",
            "schedule": "0 2 * * *",
        })
        assert read_target(p, "vault") == {
            "engine": "external-disk",
            "disk": "new-name",
            "schedule": "0 2 * * *",
        }

    def test_drops_keys_not_in_new_config(self, tmp_path):
        # Replacing means *replacing* — not merging. If the new config
        # has fewer keys, the old extras are gone.
        p = tmp_path / "stack.toml"
        p.write_text(
            '[backup.targets.vault]\n'
            'engine = "external-disk"\n'
            'disk = "d"\n'
            'legacy_field = "should disappear"\n'
        )
        write_target(p, "vault", {"engine": "external-disk", "disk": "d"})
        cfg = read_target(p, "vault")
        assert "legacy_field" not in cfg
        assert cfg["disk"] == "d"

    def test_does_not_touch_sibling_target(self, tmp_path):
        # Two targets configured — writing to one must leave the
        # other intact, comments and all.
        p = tmp_path / "stack.toml"
        p.write_text(
            '[backup.targets.vault]\n'
            'engine = "external-disk"\n'
            'disk = "v"\n'
            '\n'
            '# Offsite is the future restic target\n'
            '[backup.targets.offsite]\n'
            'engine = "restic"\n'
            'repository = "s3:..."\n'
        )
        write_target(p, "vault", {"engine": "external-disk", "disk": "new-vault"})

        text = p.read_text()
        assert "# Offsite is the future restic target" in text
        assert read_target(p, "offsite") == {
            "engine": "restic",
            "repository": "s3:...",
        }
        assert read_target(p, "vault")["disk"] == "new-vault"

    def test_does_not_touch_unrelated_section_after(self, tmp_path):
        # The block-matching regex must stop at the next [section]
        # header — otherwise we'd consume part of [updates].
        p = tmp_path / "stack.toml"
        p.write_text(
            '[backup.targets.vault]\n'
            'engine = "external-disk"\n'
            '\n'
            '[updates]\n'
            'schedule = "0 0 3 * * *"\n'
        )
        write_target(p, "vault", {"engine": "external-disk", "disk": "d"})
        text = p.read_text()
        assert "[updates]" in text
        assert 'schedule = "0 0 3 * * *"' in text


# ── write_target — value escaping ───────────────────────────────────────────

class TestWriteTargetEscaping:
    def test_round_trip_preserves_values(self, tmp_path):
        p = tmp_path / "stack.toml"
        cfg = {
            "engine": "external-disk",
            "disk": "backup-vault",
            "schedule": "0 2 * * *",
        }
        write_target(p, "vault", cfg)
        assert read_target(p, "vault") == cfg

    def test_double_quote_in_value_escapes_correctly(self, tmp_path):
        # No current field plausibly contains a quote, but the helper
        # is the wrong place to silently mangle one if it ever does.
        p = tmp_path / "stack.toml"
        write_target(p, "vault", {"engine": "ext", "disk": 'has"quote'})
        assert read_target(p, "vault")["disk"] == 'has"quote'

    def test_backslash_in_value_escapes_correctly(self, tmp_path):
        p = tmp_path / "stack.toml"
        write_target(p, "vault", {"engine": "ext", "disk": r"has\back"})
        assert read_target(p, "vault")["disk"] == r"has\back"


# ── Atomicity ──────────────────────────────────────────────────────────────

class TestWriteTargetAtomicity:
    def test_no_temp_file_left_behind_after_success(self, tmp_path):
        p = tmp_path / "stack.toml"
        write_target(p, "vault", {"engine": "external-disk"})
        # Only stack.toml should exist — no .tmp leftovers.
        entries = sorted(x.name for x in tmp_path.iterdir())
        assert entries == ["stack.toml"]
