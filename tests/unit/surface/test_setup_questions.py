"""A stacklet's first-run questions, asked as forms (ADR-021).

A stacklet's `on_configure` may define `questions(ctx, answers)` and
`apply(ctx, answers)` instead of `run(ctx)`. `stack up` then walks its
forms: answers given up front settle what they can, a terminal is asked
the rest, and without one the run stops with the open form, naming it and
its fields. These tests write a small extension stacklet, a kettle, and
start it the way `./stack up` does (`Stack.up`), in an interpreter shaped
like `./stack`'s, with no terminal unless a test gives it one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from .conftest import make_stack, write_instance

SECRET = "Descale-7731"

KETTLE_FORMS = '''
from stack.forms import Form

WATER = Form(
    id="kettle.water", title="Kettle water",
    text="The kettle boils what it holds.\\n\\n  litres  how much it holds",
    schema={"type": "object",
            "properties": {
                "litres": {"type": "integer", "title": "Litres", "default": 1},
                "code": {"type": "string", "title": "Descale code", "writeOnly": True,
                         "minLength": 4},
            },
            "required": ["litres", "code"]})


def questions(ctx, answers):
    if ctx.cfg("litres"):
        return None
    return WATER.pending(answers)


def apply(ctx, answers):
    if answers["code"].startswith("Refused"):
        raise RuntimeError(f"the descaler refused {answers['code']}")
    ctx.cfg("litres", str(answers["litres"]))
    ctx.secret("DESCALE_CODE", answers["code"])
    ctx.step(f"Kettle holds {answers['litres']} litres")
'''

INSTALL = '''
from pathlib import Path


def run(ctx):
    (Path(ctx.stack.instance_dir) / "installed").write_text("yes")
'''


def _kettle(root: Path, on_configure: str) -> None:
    """An extension stacklet `kettle` with this `on_configure.py`, and an
    `on_install.py` that leaves `installed` in the instance."""
    stacklet = root / "extensions" / "kettle"
    (stacklet / "hooks").mkdir(parents=True)
    (stacklet / "stacklet.toml").write_text('id = "kettle"\nname = "Kettle"\ntype = "host"\n')
    (stacklet / "hooks" / "on_configure.py").write_text(on_configure)
    (stacklet / "hooks" / "on_install.py").write_text(INSTALL)


@pytest.fixture
def no_terminal(monkeypatch):
    """Nobody at a terminal: an agent, a script, a screen's host call."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)


@pytest.fixture
def kettle(tmp_path, as_stack_runs_it, no_terminal):
    """The instance with the kettle in it, never set up."""
    instance = write_instance(tmp_path)
    _kettle(tmp_path, KETTLE_FORMS)
    return instance


def _everything(stack, result: dict, instance: Path) -> str:
    """All a person, a log or a screen could see after a run."""
    out = stack.output
    return "\n".join([json.dumps(result, default=str), *out.steps, *out.warnings, *out.errors,
                      (instance / "stack.toml").read_text()])


# ── Answered up front ────────────────────────────────────────────────────

def test_answers_given_to_up_settle_the_forms_and_are_applied(kettle):
    """Given the kettle never set up, when `stack up kettle` runs with
    every answer, then the answers are applied and it installs."""
    stack = make_stack(kettle)

    result = stack.up("kettle", answers={"litres": "2", "code": SECRET})

    assert result.get("ok"), result
    assert 'litres = "2"' in (kettle / "stack.toml").read_text()
    assert "Kettle holds 2 litres" in stack.output.steps
    assert (kettle / "installed").exists()


def test_a_missing_answer_stops_up_with_the_open_form(kettle):
    """Without a terminal nobody can be asked, so the run stops before
    installing and says which form and which field are open, instead of
    asking for a terminal. The form travels with the result for a screen."""
    stack = make_stack(kettle)

    result = stack.up("kettle", answers={"litres": "2"})

    assert "kettle.water" in result["error"] and "code" in result["error"]
    assert "--answer" in result["error"]
    assert result["form"]["id"] == "kettle.water"
    assert not (kettle / "installed").exists()
    assert "litres" not in (kettle / "stack.toml").read_text()
    assert stack.output.errors == []


def test_a_wrong_answer_comes_back_as_the_form_with_its_error(kettle):
    stack = make_stack(kettle)

    result = stack.up("kettle", answers={"litres": "lots", "code": SECRET})

    assert result["form"]["errors"] == {"litres": "Answer with a whole number."}
    assert "Answer with a whole number." in result["error"]


def test_a_configured_stacklet_is_not_changed_by_answers(tmp_path, as_stack_runs_it, no_terminal):
    """Given the setting already in stack.toml (an installer wrote it),
    when the first `stack up` runs with an answer for it, then nothing is
    asked and nothing is applied."""
    instance = write_instance(tmp_path, extra='[kettle]\nlitres = "1"\n')
    _kettle(tmp_path, KETTLE_FORMS)
    stack = make_stack(instance)

    result = stack.up("kettle", answers={"litres": "9", "code": SECRET})

    assert result.get("ok"), result
    assert 'litres = "1"' in (instance / "stack.toml").read_text()
    assert not any("Kettle holds" in step for step in stack.output.steps)


# ── Secrets ──────────────────────────────────────────────────────────────

def test_a_secret_answer_reaches_apply_and_nothing_else(kettle):
    stack = make_stack(kettle)

    result = stack.up("kettle", answers={"litres": "2", "code": SECRET})

    assert stack.secrets.get("kettle", "DESCALE_CODE") == SECRET
    assert SECRET not in _everything(stack, result, kettle)


def test_a_secret_is_blanked_from_an_error_that_names_it(kettle):
    """A hook's error is printed and kept in the result; a secret the
    hook let into its message is not."""
    stack = make_stack(kettle)

    result = stack.up("kettle", answers={"litres": "2", "code": "Refused-9431"})

    assert result["error"] == "Configuration failed"
    assert any("the descaler refused" in e for e in stack.output.errors)
    assert "Refused-9431" not in _everything(stack, result, kettle)


def test_a_secret_answered_wrong_is_not_repeated_in_the_refusal(kettle):
    stack = make_stack(kettle)

    result = stack.up("kettle", answers={"litres": "2", "code": "x9z"})

    assert result["form"]["errors"] == {"code": "Must be at least 4 characters."}
    assert "x9z" not in _everything(stack, result, kettle)


# ── A hook written before forms ──────────────────────────────────────────

def test_a_hook_with_only_run_runs_as_before(tmp_path, as_stack_runs_it, no_terminal):
    """Every extension today has `run(ctx)`. It is called with the hook
    context, answers or not, and a failure reads as it always did."""
    instance = write_instance(tmp_path)
    _kettle(tmp_path, 'def run(ctx):\n    ctx.cfg("litres", "3")\n    ctx.step("asked myself")\n')
    stack = make_stack(instance)

    result = stack.up("kettle", answers={"litres": "9"})

    assert result.get("ok"), result
    assert 'litres = "3"' in (instance / "stack.toml").read_text()
    assert "asked myself" in stack.output.steps


def test_a_hook_with_only_run_fails_as_before(tmp_path, as_stack_runs_it, no_terminal):
    instance = write_instance(tmp_path)
    _kettle(tmp_path, 'def run(ctx):\n    raise RuntimeError("no water")\n')
    stack = make_stack(instance)

    result = stack.up("kettle")

    assert result["error"] == "Configuration failed"
    assert stack.output.errors == ["Hook on_configure.py error: no water", "Configuration failed"]
    assert not (instance / "installed").exists()


# ── In a terminal ────────────────────────────────────────────────────────

def test_in_a_terminal_up_asks_each_field_and_explains_a_refusal(
        kettle, monkeypatch, capsys):
    """Given a terminal and no answers, when `stack up kettle` runs, then
    it shows the form's words, asks each field, says why an answer is
    refused and asks again; the secret is read without echo."""
    import getpass
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    typed = iter(["lots", "2"])
    hidden = iter(["abc", SECRET])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(typed))
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: next(hidden))
    stack = make_stack(kettle)

    result = stack.up("kettle")

    shown = capsys.readouterr().out
    assert result.get("ok"), result
    assert "Kettle water" in shown and "The kettle boils what it holds." in shown
    assert "  litres  how much it holds" in shown
    assert "Answer with a whole number." in shown
    assert "Must be at least 4 characters." in shown
    assert SECRET not in shown
    assert 'litres = "2"' in (kettle / "stack.toml").read_text()
