"""A setup form's choice field, asked in the terminal.

`stack up` and `stack configure` hand a stacklet's form to `ask_form`
when there is a terminal. A choice field (`enum` or `oneOf`) is asked as
a numbered list: the person types the number of an option, or presses
Enter for the default. Anything else asks again. The terminal's input is
given through `input`, the one place the prompt reads it.
"""

from unittest.mock import patch

import pytest

from stack.forms import Form
from stack.form_prompt import ask_form
from stack.hooks import Cancelled

DISK = Form(
    id="backup.disk", title="Backup disk",
    schema={"type": "object", "properties": {"disk": {
        "title": "Which disk",
        "oneOf": [{"const": "usb", "title": "The USB disk"},
                  {"const": "nas", "title": "The NAS"},
                  {"const": "cloud", "title": "A cloud bucket"}],
        "default": "nas"}}})

LANGUAGE = Form(
    id="core.language", title="Language",
    schema={"type": "object", "properties": {"language": {
        "title": "Language", "enum": ["de", "en"]}}})


def _ask(form, *typed):
    """`ask_form` with these lines typed, then the end of input."""
    with patch("builtins.input", side_effect=[*typed, EOFError]):
        return ask_form(form, {})


def test_lists_the_options_with_numbers_and_marks_the_default(capsys):
    _ask(DISK, "1")
    shown = capsys.readouterr().out
    assert "1) The USB disk" in shown
    assert "3) A cloud bucket" in shown
    nas = next(line for line in shown.splitlines() if "The NAS" in line)
    assert "2)" in nas and "default" in nas


def test_the_number_typed_picks_the_option():
    assert _ask(DISK, "3") == {"disk": "cloud"}


def test_enter_takes_the_default():
    assert _ask(DISK, "") == {"disk": "nas"}


def test_a_number_out_of_range_or_a_word_asks_again():
    assert _ask(DISK, "4", "0", "nas", "1") == {"disk": "usb"}


def test_enter_without_a_default_asks_again():
    assert _ask(LANGUAGE, "", "2") == {"language": "en"}


def test_end_of_input_cancels():
    with pytest.raises(Cancelled):
        _ask(DISK)
