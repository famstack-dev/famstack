"""`stack memory search --nl` — asking the vault a question in words.

Without `--nl` the query is a Python regex, which is the right default
for a surface agents call in a loop and the wrong shape for "what do
we still need to buy for the camping trip". That sentence as a regex
asks for those exact words, adjacent, and matches nothing. `--nl`
sends it to a model first and searches for the words that come back.

The model lives in the bot-runner container, so these tests stand in
for that hop rather than starting a container: `_resolve_query` is
driven with a replacement `dispatch_capture` that returns what the
container would have printed. Everything on this side of the hop is
the real thing, including the regex assembly, because that is where
the behaviour under test lives.

The CLI cases below run the real `stack memory search` as a
subprocess against a fixture vault. No bot-runner is up in a test
environment, so they exercise the degradation path for free, which is
the one that has to hold: a family asking a question on a host with
no AI configured still gets whatever literally matches.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "cli"))

import search  # noqa: E402


# ── Fixture vault ───────────────────────────────────────────────────────

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"))


@pytest.fixture
def vault(tmp_path):
    """Two camping notes, phrased the way a family writes them.

    Neither body contains the sentence anyone would ask, which is the
    entire problem: recall has to go through the words that are on
    disk ("Zelt", "Schlafsack"), not the words in the question.
    """
    v = tmp_path / "vault"
    _write(v / "family" / "camping" / "packliste.md", """
        ---
        title: Packliste Campingausflug
        date: 2026-08-02
        persons:
          - Bart
        ---

        # Packliste Campingausflug

        Noch zu besorgen: Schlafsack für Bart, Ersatzstab für das Zelt.
    """)
    _write(v / "family" / "camping" / "reservation.md", """
        ---
        title: Stellplatz reserviert
        date: 2026-08-01
        persons:
          - Marge
        ---

        # Stellplatz reserviert

        Zeltplatz am See gebucht, zwei Nächte.
    """)
    return v


@pytest.fixture
def container_says(monkeypatch):
    """Stand in for the bot-runner hop with a canned container reply.

    Returns a setter taking `(returncode, stdout, reason)` exactly as
    `dispatch_capture` returns it, plus a `calls` list so a test can
    assert the hop did *not* happen, which is half of what these
    tests are about.
    """
    calls: list[tuple] = []
    reply = {"rc": 0, "out": "", "reason": ""}

    def fake_dispatch(command, *argv, timeout=60):
        calls.append((command, argv))
        return reply["rc"], reply["out"], reply["reason"]

    monkeypatch.setattr(search, "dispatch_capture", fake_dispatch)

    def _set(rc, out="", reason=""):
        reply["rc"], reply["out"], reply["reason"] = rc, out, reason
        return calls

    _set.calls = calls  # type: ignore[attr-defined]
    return _set


# ── When the model is asked, and when it is not ─────────────────────────

class TestWhenTheModelIsAsked:
    """`--nl` is opt-in, and even then not every query is worth a call."""

    def test_a_question_becomes_its_keywords(self, container_says):
        # The hop returns the words that are actually on disk; the
        # search runs on those, OR-alternated, not on the sentence.
        container_says(0, "Zelt\nSchlafsack\n")

        query, keywords = search._resolve_query(
            "what do we still need to buy for the camping trip",
        )

        assert keywords == ["Zelt", "Schlafsack"]
        assert query == "Zelt|Schlafsack"

    def test_a_single_word_never_leaves_the_host(self, container_says):
        # `search camping --nl` would otherwise spend a container round
        # trip and a model call to learn that the keyword for "camping"
        # is "camping". Agents pass --nl on everything, so this is the
        # common case, not an edge one.
        calls = container_says(0, "should-not-be-asked\n")

        query, keywords = search._resolve_query("camping")

        assert query == "camping"
        assert keywords == []
        assert calls == []

    def test_a_regex_query_is_never_rewritten(self, vault, capsys):
        # Without --nl nothing is asked at all: `run` does not even
        # reach the resolver. This is the default path, and it must
        # stay free of the container and the model.
        search.run(["Zelt|Schlafsack", "--vault", str(vault), "--no-refresh"],
                   None, None)

        out = capsys.readouterr().out
        assert "Packliste Campingausflug" in out
        assert "Searched for" not in out


# ── Degrade, never fail ─────────────────────────────────────────────────

class TestDegradation:
    """Every way the hop can fail ends in a literal search."""

    def test_no_bot_runner_falls_back_to_the_literal_query(self, container_says, capsys):
        # Container down, no docker, model timed out: one branch,
        # because the caller wants results, not a report on our
        # infrastructure.
        container_says(1, "")

        query, keywords = search._resolve_query("camping trip packing list")

        assert query == "camping trip packing list"
        assert keywords == []
        assert "searching the query literally" in capsys.readouterr().err

    def test_an_outdated_container_degrades_in_one_line(self, container_says, capsys):
        # Version skew is the realistic version of this: the host has
        # new code, the bot-runner has not been restarted, and its
        # entry point answers an unknown command with its whole usage
        # text. One line of it reaches the person, as context on our
        # own note, never as a wall over their search results.
        container_says(2, "", "Unknown command: rewrite")

        search._resolve_query("what do we still need")

        err = capsys.readouterr().err
        assert err.splitlines() == [
            "[memory] no rewrite available (Unknown command: rewrite), "
            "searching the query literally",
        ]

    def test_a_model_with_nothing_to_say_falls_back(self, container_says):
        # Exit 0 and no keywords is the container telling us the model
        # answered off-shape. Same outcome as a dead container: search
        # what the caller typed.
        container_says(0, "\n  \n")

        assert search._resolve_query("what did we buy") == ("what did we buy", [])

    def test_falling_back_still_finds_what_literally_matches(self, vault, container_says, capsys):
        # The point of degrading rather than failing: with no AI
        # anywhere in sight, the words the caller typed are still
        # searched, and a phrase that is on disk still comes back.
        container_says(1, "")

        search.run(["Zeltplatz am See", "--nl",
                    "--vault", str(vault), "--no-refresh"], None, None)

        out = capsys.readouterr().out
        assert "Stellplatz reserviert" in out
        assert "Searched for" not in out  # nothing was rewritten


# ── What the family is told ─────────────────────────────────────────────

class TestSearchedForLine:
    """A bad rewrite has to be visible, not silent."""

    def test_printed_when_a_rewrite_happened(self, vault, container_says, capsys):
        container_says(0, "Zelt\nSchlafsack\n")

        search.run(["what do we need for camping", "--nl",
                    "--vault", str(vault), "--no-refresh"], None, None)

        out = capsys.readouterr().out
        assert out.startswith("Searched for: Zelt, Schlafsack\n")
        assert "Packliste Campingausflug" in out

    def test_printed_even_when_nothing_matched(self, vault, container_says, capsys):
        # This is the case it exists for. Without the line, a rewrite
        # that picked the wrong words is indistinguishable from an
        # empty vault, which is what hid the original bug for months.
        container_says(0, "Bootsführerschein\n")

        with pytest.raises(SystemExit) as exit_code:
            search.run(["do we have a boat licence", "--nl",
                        "--vault", str(vault), "--no-refresh"], None, None)

        assert exit_code.value.code == 1
        assert "Searched for: Bootsführerschein" in capsys.readouterr().out

    def test_suppressed_under_paths(self, vault, container_says, capsys):
        # `--paths` feeds xargs. One extra line would send a
        # non-existent file into whatever runs next.
        container_says(0, "Schlafsack\n")

        search.run(["what about the sleeping bag", "--nl", "--paths",
                    "--vault", str(vault), "--no-refresh"], None, None)

        out = capsys.readouterr().out
        assert "Searched for" not in out
        assert out.strip() == "family/camping/packliste.md"

    def test_suppressed_under_count(self, vault, container_says, capsys):
        # `--count` promises an integer and nothing else. Both notes
        # match here (`Zelt` is inside `Zeltplatz`), which is the
        # alternation doing its job.
        container_says(0, "Zelt\nSchlafsack\n")

        search.run(["what do we need", "--nl", "--count",
                    "--vault", str(vault), "--no-refresh"], None, None)

        assert capsys.readouterr().out.strip() == "2"


# ── Exit codes, across every path ───────────────────────────────────────

class TestExitCodes:
    """The contract wrappers read. `memory_tool` treats 1 as an answer.

    Exercised through the real CLI, because the exit code is what the
    process returns, not what a function returns. No bot-runner is
    running here, so `--nl` takes its fallback path, which is the one
    a host without AI configured takes in production.
    """

    def test_hit_exits_zero(self, stack_cli, vault):
        rc, _, _ = stack_cli("memory", "search", "Zelt",
                             "--vault", str(vault), "--no-refresh")
        assert rc == 0

    def test_miss_exits_one(self, stack_cli, vault):
        rc, _, _ = stack_cli("memory", "search", "Regenschirm",
                             "--vault", str(vault), "--no-refresh")
        assert rc == 1

    def test_nl_miss_still_exits_one(self, stack_cli, vault):
        # Not 3, and not 0. A question nobody can answer is "no
        # results", the same as a keyword nobody can answer. The agent
        # reads anything above 1 as "the search is broken" and retries.
        rc, _, _ = stack_cli("memory", "search", "where is the boat licence",
                             "--nl", "--vault", str(vault), "--no-refresh")
        assert rc == 1

    def test_nl_hit_exits_zero_through_the_fallback(self, stack_cli, vault):
        # Two words, so the rewrite is attempted and unavailable, and
        # the literal search underneath still matches.
        rc, out, _ = stack_cli("memory", "search", "Schlafsack für Bart",
                               "--nl", "--vault", str(vault), "--no-refresh")
        assert rc == 0
        assert "Packliste" in out

    def test_bad_arguments_exit_two(self, stack_cli, vault):
        rc, _, _ = stack_cli("memory", "search", "Zelt", "--paths", "--count",
                             "--vault", str(vault), "--no-refresh")
        assert rc == 2

    def test_missing_vault_exits_three(self, stack_cli, tmp_path):
        rc, _, _ = stack_cli("memory", "search", "Zelt",
                             "--vault", str(tmp_path / "nope"), "--no-refresh")
        assert rc == 3
