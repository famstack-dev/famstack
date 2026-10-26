"""Ask a setup form in the terminal.

The terminal's renderer for `stack.forms` (ADR-021), built from the
primitives in `stack.prompt`. `stack up` and `stack configure` hand it the
form a stacklet's `questions` returned, when there is a terminal to ask
in (`stack.configure.asker`). It shows the form's title and text, asks
each open field and checks every answer with the same validator a screen
uses, asking again with the reason until it passes. A form that came back
with errors (a disk that is not mounted) shows only the errors and asks
those fields again.

A secret (`writeOnly`) is read with `getpass` and never printed. End of
input at a question stops the command as a cancel, not as a failure.
"""

from __future__ import annotations

import getpass
import textwrap

from .forms import Form, check, read_value
from .hooks import Cancelled
from .prompt import ORANGE, RESET, ask, choose, confirm, dim, kv, nl, out, section, warn

# The width text is wrapped to, inside the two-space margin, the width the
# hooks wrapped their explanations to by hand.
_WIDTH = 64

_NO_ANSWER = "No answer was given, so nothing was set up."


def ask_form(form: Form, answers: dict) -> dict:
    """Ask `form` in the terminal; the answers given for its fields.

    Asks the fields that are unanswered or carry an error; when none do,
    every field, so a form that came back unchanged is never skipped.
    """
    todo = [name for name in form.asked() if name in form.errors or name not in answers]
    if form.errors:
        nl()
    else:
        section(form.title)
        _say(form.text)
        _show_read_only(form)
    given = {}
    for name in todo or form.asked():
        error = form.errors.get(name)
        if error:
            warn(error)
        given[name] = _ask_field(form.fields[name])
    return given


# ── The form's words ─────────────────────────────────────────────────────

def _say(text: str) -> None:
    """The form's text: paragraphs split by a blank line, each line
    wrapped, and a line that starts with spaces kept as it is (an example)."""
    if not text:
        return
    for paragraph in text.split("\n\n"):
        for line in paragraph.splitlines():
            if line.startswith(" "):
                out(line)
            else:
                for part in textwrap.wrap(line, _WIDTH) or [""]:
                    out(part)
        nl()


def _show_read_only(form: Form) -> None:
    shown = [(spec.get("title", name), spec.get("default", ""))
             for name, spec in form.fields.items() if spec.get("readOnly")]
    for title, value in shown:
        kv(title, value)
    if shown:
        nl()


# ── One field ────────────────────────────────────────────────────────────

def _ask_field(spec: dict):
    """Ask one field until its answer passes, and confirm it when the
    field says so (`x-confirm`)."""
    title = spec.get("title", "")
    if spec.get("description"):
        for line in textwrap.wrap(spec["description"], _WIDTH):
            dim(line)
        nl()
    while True:
        value = _read(spec, title)
        if _confirmed(spec, value):
            return value


def _read(spec: dict, title: str):
    """One answer, by the field's kind: yes or no, a choice, a secret, text."""
    default = spec.get("default")
    if spec.get("type") == "boolean" and "enum" not in spec:
        return confirm(title, default=bool(default))
    if "enum" in spec or "oneOf" in spec:
        return _choose(spec, title)
    if spec.get("writeOnly"):
        return _secret(spec, title)
    text = ask(title, default="" if default is None else str(default),
               validate=lambda typed: check(spec, read_value(spec, typed)))
    if text is None:
        raise Cancelled(_NO_ANSWER)
    return read_value(spec, text)


def _choose(spec: dict, title: str):
    choices = [(c.get("const"), c.get("title", str(c.get("const")))) for c in spec.get("oneOf", [])] \
        or [(v, str(v)) for v in spec["enum"]]
    picked = choose(title, [label for _, label in choices])
    if not isinstance(picked, int):
        raise Cancelled(_NO_ANSWER)
    return choices[picked][0]


def _secret(spec: dict, title: str) -> str:
    """A secret, read without echo, asked again until it passes."""
    while True:
        try:
            value = getpass.getpass(f"  {ORANGE}›{RESET} {title}: ")
        except EOFError:
            raise Cancelled(_NO_ANSWER) from None
        problem = check(spec, value)
        if not problem:
            return value
        dim(f"  {problem}")


def _confirmed(spec: dict, value) -> bool:
    """Ask the field's `x-confirm` question about `value`; no asks again.
    Lines before the last are said, the last is the question."""
    question = spec.get("x-confirm")
    if not question:
        return True
    *said, asked = question.format(value=value).split("\n")
    for line in said:
        out(line)
    return confirm(asked, default=False)
