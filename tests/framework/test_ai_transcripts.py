"""Unit tests for `stack.ai.transcripts`.

The store persists whisper output. Passes process stored records and
write an audit list. These tests pin the store round-trip, the force
path, pass isolation, and staleness detection.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))

from stack.ai.client import LLMUnavailableError  # noqa: E402
from stack.ai.transcripts import (  # noqa: E402
    TranscriptPass,
    TranscriptStore,
    gate_pass,
    polish_pass,
    run_passes,
    stale_passes,
)


class _StubLLM:
    """Returns a fixed string, or raises. Records calls."""

    def __init__(self, result: str = "", error: Exception | None = None):
        self.result, self.error = result, error
        self.calls: list[dict] = []

    async def complete(self, role, prompt, **kw):
        self.calls.append({"role": role, **kw})
        if self.error:
            raise self.error
        return self.result


class TestStore:
    async def test_round_trip_and_single_production(self, tmp_path):
        store = TranscriptStore(tmp_path)
        produced = 0

        async def produce():
            nonlocal produced
            produced += 1
            return {"raw": "hi", "text": "hi"}

        first = await store.run("$ev1", produce)
        second = await store.run("$ev1", produce)
        assert first["text"] == second["text"] == "hi"
        assert produced == 1
        assert store.read("$ev1")["raw"] == "hi"

    async def test_force_reproduces_and_overwrites(self, tmp_path):
        store = TranscriptStore(tmp_path)
        results = iter([{"text": "old"}, {"text": "new"}])

        async def produce():
            return next(results)

        await store.run("$ev1", produce)
        forced = await store.run("$ev1", produce, force=True)
        assert forced["text"] == "new"
        assert store.read("$ev1")["text"] == "new"


class TestRunPasses:
    async def test_writes_the_pass_list(self):
        async def upper(record):
            return record["text"].upper(), "applied", {"model": "m1"}

        record = await run_passes(
            {"raw": "hi", "text": "hi"},
            [TranscriptPass("upper", 2, upper)])

        assert record["text"] == "HI"
        assert record["passes"] == [
            {"name": "upper", "version": 2, "outcome": "applied",
             "model": "m1"}]
        assert record["raw"] == "hi"

    async def test_a_failing_pass_keeps_the_text_and_the_chain(self):
        async def broken(record):
            raise LLMUnavailableError("down")

        async def upper(record):
            return record["text"].upper(), "applied", {}

        record = await run_passes(
            {"raw": "hi", "text": "hi"},
            [TranscriptPass("broken", 1, broken),
             TranscriptPass("upper", 1, upper)])

        assert record["text"] == "HI"
        outcomes = {p["name"]: p["outcome"] for p in record["passes"]}
        assert outcomes["broken"].startswith("failed")
        assert outcomes["upper"] == "applied"

    async def test_a_rerun_replaces_the_old_entry(self):
        async def noop(record):
            return record["text"], "clean", {}

        record = {"raw": "hi", "text": "hi",
                  "passes": [{"name": "gate", "version": 1,
                              "outcome": "clean"}]}
        record = await run_passes(record, [TranscriptPass("gate", 2, noop)])
        assert record["passes"] == [
            {"name": "gate", "version": 2, "outcome": "clean"}]


class TestStalePasses:
    def test_version_change_marks_stale(self):
        async def noop(record):
            return record["text"], "clean", {}

        p1, p2 = (TranscriptPass("gate", 2, noop),
                  TranscriptPass("polish", 1, noop))
        record = {"passes": [{"name": "gate", "version": 1},
                             {"name": "polish", "version": 1}]}
        assert stale_passes(record, [p1, p2]) == [p1]

    def test_a_record_without_a_list_is_fully_stale(self):
        async def noop(record):
            return "", "clean", {}

        p = TranscriptPass("gate", 1, noop)
        assert stale_passes({}, [p]) == [p]


class TestGatePass:
    async def test_blocks_a_loop_and_empties_the_text(self):
        looping = "and then we went " * 40
        text, outcome, _ = await gate_pass().apply(
            {"raw": looping, "text": looping, "quality": {}})
        assert text == ""
        assert outcome.startswith("blocked")

    async def test_clean_text_passes_through(self):
        text, outcome, _ = await gate_pass().apply(
            {"raw": "hallo bart", "text": "hallo bart", "quality": {}})
        assert (text, outcome) == ("hallo bart", "clean")


class TestPolishPass:
    async def test_skips_empty_text_without_a_model_call(self):
        llm = _StubLLM()
        text, outcome, _ = await polish_pass(llm).apply(
            {"raw": "garbage", "text": ""})
        assert (text, outcome) == ("", "skipped: no text")
        assert llm.calls == []

    async def test_applies_polish_and_records_the_model(self):
        llm = _StubLLM(result="Hallo Bart.")
        text, outcome, extra = await polish_pass(llm).apply(
            {"raw": "hallo bart", "text": "hallo bart"})
        assert text == "Hallo Bart."
        assert outcome == "applied"
        # No model is configured in the test env; extra stays empty.
        assert extra == {}
