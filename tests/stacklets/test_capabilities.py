"""Unit tests for the per-model capability cache."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "docs" / "bot"))

from capabilities import ModelCapabilities


# ── Tri-state semantics ─────────────────────────────────────────────────

class TestSupportsVision:
    def test_unknown_returns_none(self, tmp_path):
        caps = ModelCapabilities(path=tmp_path / "caps.json")
        assert caps.supports_vision("never-seen-model") is None

    def test_recorded_true_returns_true(self, tmp_path):
        caps = ModelCapabilities(path=tmp_path / "caps.json")
        caps.record_vision("vl-model", True)
        assert caps.supports_vision("vl-model") is True

    def test_recorded_false_returns_false(self, tmp_path):
        # The "false" case matters as much as "true" — we cache it so we
        # don't re-probe text-only models on every classify call.
        caps = ModelCapabilities(path=tmp_path / "caps.json")
        caps.record_vision("text-only", False)
        assert caps.supports_vision("text-only") is False


# ── Persistence ─────────────────────────────────────────────────────────

class TestPersistence:
    def test_record_persists_to_disk(self, tmp_path):
        path = tmp_path / "caps.json"
        ModelCapabilities(path=path).record_vision("m1", True)
        # Fresh instance loads the same data — verifies the write hit disk.
        assert ModelCapabilities(path=path).supports_vision("m1") is True

    def test_in_memory_only_when_path_none(self, tmp_path):
        caps = ModelCapabilities(path=None)
        caps.record_vision("m1", True)
        assert caps.supports_vision("m1") is True
        # Nothing leaked to disk — no file to find.
        assert not list(tmp_path.iterdir())

    def test_corrupted_file_starts_empty(self, tmp_path):
        # If an admin or a crashed write leaves invalid JSON, the cache
        # should silently start over rather than crash the bot startup.
        path = tmp_path / "caps.json"
        path.write_text("{not valid json")
        caps = ModelCapabilities(path=path)
        assert caps.supports_vision("anything") is None
        # Recording into a fresh cache works and overwrites the bad file.
        caps.record_vision("m1", True)
        assert ModelCapabilities(path=path).supports_vision("m1") is True

    def test_record_writes_probed_at_timestamp(self, tmp_path):
        # The probed_at timestamp lets a future eviction policy ("re-probe
        # capabilities older than N days") work without a separate index.
        import json
        path = tmp_path / "caps.json"
        ModelCapabilities(path=path).record_vision("m1", True)
        data = json.loads(path.read_text())
        assert "probed_at" in data["m1"]
        # ISO 8601 zulu — verify shape, not exact value.
        assert data["m1"]["probed_at"].endswith("Z")
