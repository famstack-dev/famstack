"""`stack configure <id>`, and the answers `stack up` and it take.

`stack configure` asks a stacklet's setup questions on their own, without
installing anything (ADR-021). In a terminal it asks form by form; without
one, `--answer key=value` and `--answers <file>` (or `-` for stdin) answer
them, and whatever is still open fails, naming the form and its fields.
`--json` never asks: it prints the next form, or `{"ready": true}` once the
answers are applied. `stack up <id>` takes the same flags on a first run.

These tests run `./stack` in this process, through `stack.cli.main`, the
function `./stack` calls, against a fabricated instance with the kettle
stacklet of `test_setup_questions.py`, and no terminal.
"""

from __future__ import annotations

import json

import pytest

from .test_setup_questions import KETTLE_FORMS, SECRET, _kettle


@pytest.fixture
def kettle(stack_cli, tmp_path):
    _kettle(tmp_path, KETTLE_FORMS)
    return stack_cli


# ── As data ──────────────────────────────────────────────────────────────

def test_configure_json_without_answers_prints_the_first_form(kettle):
    ran = kettle("configure", "kettle", "--json")

    assert ran.code == 0
    assert ran.answer["form"]["id"] == "kettle.water"
    assert ran.answer["form"]["errors"] == {}
    assert set(ran.answer) == {"form"}


def test_configure_json_with_every_answer_applies_them_and_is_ready(kettle, tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"code": SECRET}))

    ran = kettle("configure", "kettle", "--json", "--answer", "litres=2", "--answers", str(answers))

    assert ran.code == 0, ran.err
    assert ran.answer == {"ready": True}
    assert 'litres = "2"' in kettle.toml()


def test_answers_on_stdin_keep_a_secret_out_of_the_arguments(kettle):
    """`--answer code=...` would show the secret to every process on the
    Mac through `ps`; `--answers -` reads it from stdin."""
    ran = kettle("configure", "kettle", "--json", "--answers", "-",
                 stdin=json.dumps({"litres": 2, "code": SECRET}))

    assert ran.answer == {"ready": True}
    assert SECRET not in ran.out + ran.err + kettle.toml()


def test_a_wrong_answer_prints_the_form_again_with_its_error(kettle):
    ran = kettle("configure", "kettle", "--json", "--answer", "litres=lots")

    assert ran.code == 0
    assert ran.answer["form"]["errors"] == {"litres": "Answer with a whole number."}


def test_a_configured_stacklet_is_ready_at_once(kettle):
    kettle("configure", "kettle", "--json", "--answer", "litres=2", "--answer", f"code={SECRET}")

    ran = kettle("configure", "kettle", "--json", "--answer", "litres=9")

    assert ran.answer == {"ready": True}
    assert 'litres = "2"' in kettle.toml()


def test_a_malformed_answer_is_refused_before_anything_runs(kettle):
    ran = kettle("configure", "kettle", "--json", "--answer", "litres")

    assert ran.code == 1
    assert "key=value" in ran.answer["error"]


def test_a_hook_that_asks_for_itself_is_not_run_for_json(stack_cli, tmp_path):
    """`--json` is for a caller that cannot answer a terminal prompt."""
    _kettle(tmp_path, 'def run(ctx):\n    input("Litres? ")\n')

    ran = stack_cli("configure", "kettle", "--json")

    assert ran.code == 1
    assert "stack up kettle" in ran.answer["error"]


# ── For a person ─────────────────────────────────────────────────────────

def test_configure_without_a_terminal_names_the_open_form_and_its_field(kettle):
    ran = kettle("configure", "kettle", "--answer", "litres=2")

    assert ran.code == 1
    assert "kettle.water" in ran.err and "code" in ran.err
    assert "litres" not in kettle.toml()


def test_configure_on_a_configured_stacklet_says_what_is_set_and_changes_nothing(kettle):
    kettle("configure", "kettle", "--json", "--answer", "litres=2", "--answer", f"code={SECRET}")
    before = kettle.toml()

    ran = kettle("configure", "kettle")

    assert ran.code == 0
    assert "litres" in ran.out and "2" in ran.out
    assert kettle.toml() == before


# ── stack up ─────────────────────────────────────────────────────────────

def test_up_without_the_answer_names_the_form_and_installs_nothing(kettle):
    """Without a terminal, `stack up` stops before installing and says
    what to answer, instead of asking for a terminal."""
    ran = kettle("up", "kettle")

    assert ran.code == 1
    assert "kettle.water" in ran.err and "--answer" in ran.err
    assert not (kettle.instance / "installed").exists()


def test_up_hands_its_answers_to_the_forms(kettle):
    """The answer reached the form: it was judged, and refused."""
    ran = kettle("up", "kettle", "--answer", "litres=lots", "--answer", f"code={SECRET}")

    assert ran.code == 1
    assert "Answer with a whole number." in ran.err
    assert SECRET not in ran.out + ran.err
