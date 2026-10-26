"""A setup form and its validator, as a hook and a screen use them.

A form is a step's questions as a JSON Schema subset (ADR-021). A hook
builds one and ends its `questions` with `form.pending(answers)`: the form
again while anything is unanswered or wrong, None once it is settled. A
screen reads `to_dict()` and shows `errors` on their fields. These tests
pin what each supported keyword accepts and refuses, and the words of the
refusal, because both the terminal and a screen show them as they are.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from stack.forms import Form, read_typed, validate  # noqa: E402


def form(**fields) -> Form:
    """A form whose fields are `fields`, every one of them required."""
    return Form(id="test.form", title="A test form", text="Why we ask.",
                schema={"type": "object", "properties": fields, "required": list(fields)})


# ── Settled or pending ───────────────────────────────────────────────────

def test_a_form_nobody_answered_is_pending_and_shows_no_errors():
    """The first time a screen shows a form, no field is marked wrong."""
    name = form(name={"type": "string", "title": "Name"})

    pending = name.pending({})

    assert pending is not None
    assert pending.errors == {}
    assert pending.missing({}) == ["name"]


def test_a_form_answered_validly_is_settled():
    assert form(name={"type": "string"}).pending({"name": "smiths"}) is None


def test_each_wrong_answer_gets_its_own_error_and_a_right_one_gets_none():
    """Errors are per field, so a screen marks the two fields that are
    wrong and leaves the right one alone."""
    three = form(name={"type": "string", "minLength": 1},
                 when={"type": "string", "format": "time"},
                 count={"type": "integer"})

    pending = three.pending({"name": "", "when": "25:00", "count": 3})

    assert pending is not None
    assert set(pending.errors) == {"name", "when"}


def test_an_optional_field_left_out_settles_the_form():
    optional = Form(id="t", title="t", schema={
        "type": "object", "properties": {"note": {"type": "string"}}})
    assert optional.pending({}) is None


# ── Each keyword ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("spec, good, bad, says", [
    ({"type": "string"}, "text", 3, "Answer with text."),
    ({"type": "boolean"}, True, "maybe", "Answer yes or no."),
    ({"type": "integer"}, 7, "seven", "Answer with a whole number."),
    ({"type": "integer"}, 7, True, "Answer with a whole number."),
    ({"type": "string", "enum": ["a", "b"]}, "a", "c", "Choose one of: a, b."),
    ({"type": "boolean", "enum": [True]}, True, False, "Choose one of: yes."),
    ({"type": "string", "oneOf": [{"const": "x", "title": "Ex"}, {"const": "y", "title": "Why"}]},
     "y", "z", "Choose one of: x (Ex), y (Why)."),
    ({"type": "string", "minLength": 1}, "a", "", "Must not be empty."),
    ({"type": "string", "minLength": 4}, "abcd", "abc", "Must be at least 4 characters."),
    ({"type": "string", "pattern": "^[a-z]+$"}, "smiths", "Smiths!",
     "Does not have the expected form."),
    ({"type": "string", "pattern": "^[a-z]+$", "x-pattern-error": "Use lowercase letters."},
     "smiths", "Smiths!", "Use lowercase letters."),
    ({"type": "string", "format": "time"}, "02:00", "2am", "Answer with a time like 02:00 or 22:15."),
    ({"type": "string", "format": "time"}, "23:59", "24:00", "Answer with a time like 02:00 or 22:15."),
    ({"type": "string", "format": "uri"}, "https://ai.example.test/v1", "ai.example.test",
     "Answer with a full address, like https://example.com."),
    ({"type": "string", "format": "hostname"}, "home.internal", "-bad-.example",
     "Answer with a host name, like home.internal."),
])
def test_a_keyword_accepts_what_it_allows_and_says_why_it_refuses(spec, good, bad, says):
    one = form(field=spec)

    assert validate(one, {"field": good}) == {}
    assert validate(one, {"field": bad}) == {"field": says}


def test_a_time_of_day_takes_a_one_digit_hour():
    """`3:30` was accepted by the backup hook before it was a form."""
    assert validate(form(t={"type": "string", "format": "time"}), {"t": "3:30"}) == {}


def test_a_pattern_is_searched_as_json_schema_does():
    """JSON Schema patterns are not anchored: `ab` matches `xaby`."""
    assert validate(form(f={"type": "string", "pattern": "ab"}), {"f": "xaby"}) == {}


def test_a_refusal_never_quotes_the_answer():
    """The answer may be a secret, and an error goes to the terminal, a
    screen, and the command's result."""
    secret = "Hunter2 Hunter2!"
    checks = [{"type": "boolean"}, {"type": "integer"}, {"type": "string", "enum": ["a"]},
              {"type": "string", "pattern": "^x$"}, {"type": "string", "format": "time"},
              {"type": "string", "format": "uri"}, {"type": "string", "format": "hostname"},
              {"type": "string", "minLength": 99}]
    for spec in checks:
        errors = validate(form(f={**spec, "writeOnly": True}), {"f": secret})
        assert errors and secret not in errors["f"], spec


# ── Shown, not asked; asked, never shown ─────────────────────────────────

def test_a_read_only_field_is_shown_and_never_asked():
    info = form(url={"type": "string", "readOnly": True, "default": "https://ai.example.test"},
                keep={"type": "boolean"})

    assert info.asked() == ["keep"]
    assert info.pending({"keep": True}) is None
    assert validate(info, {"url": 3, "keep": True}) == {}


def test_a_secret_has_no_default_in_what_a_screen_receives():
    secret = form(passphrase={"type": "string", "writeOnly": True, "default": "hunter2"})

    shown = secret.to_dict()

    assert "default" not in shown["schema"]["properties"]["passphrase"]
    assert secret.secrets() == {"passphrase"}


def test_what_a_screen_receives_is_the_form_and_its_errors():
    docs = Form(id="t.f", title="T", text="Why.", docs="https://famstack.dev/x",
                schema={"type": "object", "properties": {"n": {"type": "string"}}, "required": ["n"]})

    shown = docs.with_errors(n="Wrong.").to_dict()

    assert shown == {"id": "t.f", "title": "T", "text": "Why.",
                     "schema": {"type": "object", "properties": {"n": {"type": "string"}},
                                "required": ["n"]},
                     "errors": {"n": "Wrong."}, "x-docs": "https://famstack.dev/x"}


# ── Answers typed on a command line ──────────────────────────────────────

def test_text_answers_are_read_by_their_fields_types():
    """`--answer keep=yes` arrives as text; the boolean field reads it as
    yes. Text that does not read stays, for the validator to refuse."""
    typed = form(keep={"type": "boolean"}, count={"type": "integer"}, name={"type": "string"},
                 off={"type": "boolean"}, odd={"type": "boolean"})

    answers = read_typed(typed, {"keep": "yes", "count": "12", "name": "007", "off": "false",
                                 "odd": "perhaps", "other": "yes"})

    assert answers == {"keep": True, "count": 12, "name": "007", "off": False,
                       "odd": "perhaps", "other": "yes"}
