"""`stack memory ask` — a question answered from the brain, like `stack web ask`.

The agent answers a vault question in a tool loop: search, read,
check, read again. Each step is a model call with a prefill. This
route is fixed at two model calls: one for the keywords (the `--nl`
rewrite), one for the answer from the top hits. It reports its own
timing so it can be compared with the agent.

Its surface matches `stack web ask`: the answer, then the sources,
and the sources print even when the model could not be reached.

The model lives in the bot-runner container. The host tests stand in
for that hop with a fake `dispatch_capture`; everything on the host
side is the real code. The container side is tested with a stub LLM,
the only external boundary it has.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "cli"))

import ask  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"))


@pytest.fixture
def data_dir(tmp_path):
    """A brain with the diary page that holds the answer, and noise."""
    brain = tmp_path / "memory" / "brain"
    _write(brain / "family/diary/2026/06.md", """
        ---
        title: Juni
        generated: true
        ---

        ## 12. Juni
        Bart hat heute sein Seepferdchen geschafft.
    """)
    _write(brain / "family/notes/garten.md", """
        ---
        title: Garten
        date: 2026-09-17
        ---

        Bart war heute im Garten.
    """)
    return tmp_path


@pytest.fixture
def container(monkeypatch):
    """Fake bot-runner: keywords for `rewrite`, an answer for `answer`.

    Records every hop with the stdin it was given, so a test can check
    what the model would have been shown.
    """
    state = {
        "keywords": "Bart\nSeepferdchen\n",
        "answer": (0, "Am 12. Juni 2026 [1].\n", ""),
        "calls": [],
    }

    def fake_dispatch(command, *argv, timeout=60, input_text=None):
        state["calls"].append({"command": command, "argv": argv, "input": input_text})
        if command == "rewrite":
            return 0, state["keywords"], ""
        if command == "answer":
            return state["answer"]
        return 2, "", f"unknown command {command}"

    monkeypatch.setattr(ask, "dispatch_capture", fake_dispatch)
    return state


def _run(data_dir, *args):
    return ask.run(
        ["Wann hat Bart sein Seepferdchen geschafft?", "--no-refresh", *args],
        None, {"data_dir": str(data_dir)},
    )


class TestAsk:
    def test_prints_the_answer_then_its_sources(self, data_dir, container, capsys):
        _run(data_dir)
        out = capsys.readouterr().out

        assert out.lstrip().startswith("Am 12. Juni 2026 [1].")
        assert "  Sources" in out
        assert "  [1] Juni\n      family/diary/2026/06.md" in out

    def test_json_for_an_agent(self, data_dir, container, capsys):
        _run(data_dir, "--json")
        payload = json.loads(capsys.readouterr().out)

        assert payload["answer"] == "Am 12. Juni 2026 [1]."
        assert payload["sources"][0] == {
            "n": 1, "title": "Juni", "path": "family/diary/2026/06.md"}

    def test_sources_limits_the_hits(self, data_dir, container, capsys):
        _run(data_dir, "--sources", "1", "--json")

        assert len(json.loads(capsys.readouterr().out)["sources"]) == 1

    def test_the_model_is_shown_the_question_and_the_line_that_answers_it(
            self, data_dir, container):
        _run(data_dir)
        hop = next(c for c in container["calls"] if c["command"] == "answer")
        payload = json.loads(hop["input"])

        assert payload["question"] == "Wann hat Bart sein Seepferdchen geschafft?"
        first = payload["evidence"][0]
        assert first["path"] == "family/diary/2026/06.md"
        assert "Seepferdchen" in first["excerpt"]

    def test_reports_where_the_time_went(self, data_dir, container, capsys):
        _run(data_dir)
        err = capsys.readouterr().err

        assert "search" in err and "answer" in err

    def test_nothing_found_means_no_answer_call(self, data_dir, container, capsys):
        with pytest.raises(SystemExit) as exit_:
            ask.run(["Wann war der Einhornausflug?", "--no-refresh"], None,
                    {"data_dir": str(data_dir)})

        assert exit_.value.code == 1
        assert "Search returned nothing" in capsys.readouterr().err
        assert all(c["command"] != "answer" for c in container["calls"])

    def test_without_a_model_the_sources_are_still_shown(
            self, data_dir, container, capsys):
        container["answer"] = (1, "", "stack-core-bot-runner is not running")

        with pytest.raises(SystemExit) as exit_:
            _run(data_dir)
        out = capsys.readouterr().out

        assert exit_.value.code == 1
        assert "Couldn't reach the model (stack-core-bot-runner is not running)" in out
        assert "family/diary/2026/06.md" in out


class TestAskSearchesTheQuestionsOwnWords:
    """The question's words, not a model's rewrite of them.

    A rewrite that adds category words ("Rechnung, Schwimmkurs") to a
    question about a swimming badge lets a receipt beat the diary
    entry: rare words weigh most in the ranking. The question's own
    words find the entry.
    """

    def test_no_rewrite_call_is_made(self, data_dir, container):
        _run(data_dir)

        assert all(c["command"] != "rewrite" for c in container["calls"])

    def test_a_page_with_added_category_words_does_not_win(
            self, data_dir, container):
        _write(data_dir / "memory/brain/family/documents/rechnung-schwimmkurs.md", """
            ---
            title: Rechnung Schwimmkurs Bart
            date: 2026-03-02
            ---

            Rechnung für den Schwimmkurs von Bart, Hallenbad Springfield.
        """)
        container["keywords"] = "Bart\nSeepferdchen\nRechnung\nSchwimmkurs\n"

        _run(data_dir)
        hop = next(c for c in container["calls"] if c["command"] == "answer")

        assert json.loads(hop["input"])["evidence"][0]["path"] == "family/diary/2026/06.md"

    def test_question_words_and_fillers_are_not_searched(
            self, data_dir, container, capsys):
        _run(data_dir)
        err = capsys.readouterr().err

        assert "searched for: Bart, Seepferdchen, geschafft;" in err


# ── container side ──────────────────────────────────────────────────────

class _StubLLM:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    async def complete(self, namespace, prompt, json_mode=False, **kwargs):
        self.prompts.append(prompt)
        return self.reply


def _answer_command():
    """Load `answer.py` by path: several stacklets have a `cli` package,
    and whichever a test imported first would shadow this one."""
    import importlib.util
    path = _REPO_ROOT / "stacklets" / "memory" / "bot" / "cli" / "answer.py"
    spec = importlib.util.spec_from_file_location("memory_bot_cli_answer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestAnswerCommand:
    PAYLOAD = {
        "question": "Wann hat Bart sein Seepferdchen geschafft?",
        "evidence": [
            {"path": "family/diary/2026/06.md", "title": "Juni", "date": "",
             "excerpt": "Bart hat heute sein Seepferdchen geschafft.", "summary": ""},
            {"path": "family/notes/garten.md", "title": "Garten", "date": "2026-09-17",
             "excerpt": "Bart war heute im Garten.", "summary": ""},
        ],
    }

    def test_prints_what_the_model_answered(self, monkeypatch, capsys):
        llm = _StubLLM("Am 12. Juni 2026 [1].")
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(self.PAYLOAD)))

        code = asyncio.run(_answer_command().run(llm, []))

        assert code == 0
        assert capsys.readouterr().out.strip() == "Am 12. Juni 2026 [1]."

    def test_the_prompt_numbers_the_evidence_for_citations(self, monkeypatch):
        llm = _StubLLM("x")
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(self.PAYLOAD)))

        asyncio.run(_answer_command().run(llm, []))
        prompt = llm.prompts[0]

        assert self.PAYLOAD["question"] in prompt
        assert "[1]" in prompt and "Seepferdchen geschafft" in prompt
        assert "[2]" in prompt and "family/notes/garten.md" in prompt

    def test_an_empty_answer_is_a_failure(self, monkeypatch):
        llm = _StubLLM("  ")
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(self.PAYLOAD)))

        assert asyncio.run(_answer_command().run(llm, [])) == 1
