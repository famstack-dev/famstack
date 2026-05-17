"""Tests for the archivist's capture-tag cache.

A small frequency/recency-tracked dictionary persisted to the bot's
data dir. Loaded at startup, updated after each `publish_capture`,
top-N rendered into the capture prompt so the LLM reuses existing
vocabulary instead of inventing variants ("LLMs" not "llm" not
"Large Language Models").

These tests pin the cache logic in isolation — no Matrix, no
Forgejo, no LLM. The cache is plain JSON on disk.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BOT_DIR = Path(__file__).resolve().parent.parent.parent / "stacklets" / "docs" / "bot"
sys.path.insert(0, str(_BOT_DIR))

from capture_tags import CaptureTagCache  # noqa: E402


@pytest.fixture
def cache(tmp_path):
    return CaptureTagCache(tmp_path / "capture-tags.json")


# ── Load / save round-trip ───────────────────────────────────────────────

class TestPersistence:
    def test_empty_when_file_missing(self, cache):
        cache.load()
        assert cache.top(10) == []

    def test_record_then_load_round_trip(self, cache, tmp_path):
        cache.load()
        cache.record(["LLMs", "Apple Silicon"], when="2026-05-17")
        cache.save()

        fresh = CaptureTagCache(tmp_path / "capture-tags.json")
        fresh.load()
        assert set(fresh.top(10)) == {"LLMs", "Apple Silicon"}

    def test_corrupt_file_starts_empty(self, cache, tmp_path):
        (tmp_path / "capture-tags.json").write_text("not json")
        cache.load()
        assert cache.top(10) == []


# ── Counting + ranking ───────────────────────────────────────────────────

class TestRanking:
    """`top(n)` returns the most-frequent tags. Frequency wins — recency
    is recorded but doesn't affect ordering yet (dream-cycle rebuild can
    add decay later if tag drift becomes an issue)."""

    def test_top_by_frequency(self, cache):
        cache.load()
        cache.record(["LLMs"], when="2026-05-15")
        cache.record(["LLMs"], when="2026-05-16")
        cache.record(["LLMs"], when="2026-05-17")
        cache.record(["AI"], when="2026-05-17")
        cache.record(["Privacy"], when="2026-05-15")

        top = cache.top(2)
        # LLMs (3) before AI (1) / Privacy (1) — those two are tied;
        # we don't pin their order against each other.
        assert top[0] == "LLMs"

    def test_top_respects_limit(self, cache):
        cache.load()
        for i in range(10):
            cache.record([f"tag{i}"], when="2026-05-17")
        assert len(cache.top(5)) == 5

    def test_dedup_within_single_record(self, cache):
        # The classifier sometimes returns duplicates ("LLMs" twice in
        # the same response). Recording should count each tag once
        # per capture, not once per LLM emission.
        cache.load()
        cache.record(["LLMs", "LLMs", "AI"], when="2026-05-17")
        cache.record(["LLMs"], when="2026-05-17")
        # Now LLMs has 2 (two captures), AI has 1 — not LLMs=3.
        ranked = cache._sorted_tags()
        scores = dict(ranked)
        assert scores["LLMs"] == 2
        assert scores["AI"] == 1


# ── Robustness ───────────────────────────────────────────────────────────

class TestRobustness:
    def test_ignores_blank_and_non_string_tags(self, cache):
        cache.load()
        cache.record(["LLMs", "", "  ", None, 42, "AI"], when="2026-05-17")
        assert set(cache.top(10)) == {"LLMs", "AI"}

    def test_strips_whitespace_on_tags(self, cache):
        cache.load()
        cache.record(["  LLMs  ", "AI"], when="2026-05-17")
        assert "LLMs" in cache.top(10)

    def test_case_preserved(self, cache):
        # We keep the casing the classifier produced. The dream-cycle
        # canonicalizes ("llm" → "LLMs") later; the cache is a faithful
        # record of what's been written, not a normalizer.
        cache.load()
        cache.record(["LLMs"], when="2026-05-17")
        cache.record(["llms"], when="2026-05-17")
        assert set(cache.top(10)) == {"LLMs", "llms"}

    def test_save_creates_parent_dir(self, tmp_path):
        nested = tmp_path / "sub" / "deep" / "capture-tags.json"
        cache = CaptureTagCache(nested)
        cache.load()
        cache.record(["LLMs"], when="2026-05-17")
        cache.save()
        assert nested.exists()
        data = json.loads(nested.read_text())
        assert "LLMs" in data["tags"]
