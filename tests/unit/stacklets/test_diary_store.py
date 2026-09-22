"""Prompt fingerprints on the diary caches. A stored artifact is
valid only under the prompt that produced it."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent
                       / "stacklets" / "memory" / "bot"))

import diary_store  # noqa: E402


class TestReadingStorePromptFingerprint:
    def test_a_reading_from_another_prompt_misses(self, tmp_path):
        store = diary_store.ReadingStore(tmp_path / "r.json", "prompt-v1")
        store.put("$ev", {"mode": "monologue"}, "hello")
        store.save()

        same = diary_store.ReadingStore(tmp_path / "r.json", "prompt-v1").load()
        assert same.get("$ev", "hello") is not None

        changed = diary_store.ReadingStore(tmp_path / "r.json", "prompt-v2").load()
        assert changed.get("$ev", "hello") is None


class TestSummaryStorePromptFingerprint:
    def test_a_summary_from_another_prompt_misses(self, tmp_path):
        store = diary_store.SummaryStore(tmp_path / "s.json", "prompt-v1")
        store.put("2026-09", "digest", "Ein Monat.")
        store.save()

        same = diary_store.SummaryStore(tmp_path / "s.json", "prompt-v1").load()
        assert same.get("2026-09", "digest") == "Ein Monat."

        changed = diary_store.SummaryStore(tmp_path / "s.json", "prompt-v2").load()
        assert changed.get("2026-09", "digest") == ""
