"""The terminal a test types into, for code that reads answers with `input()`.

A hook, the installer and a CLI command ask a question with `input()`, which
reads standard input and writes the question to standard output. A test
gives it the answers there and reads the questions with `capsys`, so the
code under test runs as it does in a terminal:

    def test_an_empty_answer_takes_the_default(terminal, capsys):
        terminal.types("")
        assert ask("Name", default="world") == "world"
        assert "[world]" in capsys.readouterr().out

An answer is one line. When the lines are used up, the next `input()` raises
`EOFError`, as it does on a closed terminal.

    terminal.types("a", "b")    the answers, in order
    terminal.closes()           no answer: the terminal is closed
    terminal.interrupts()       the next question ends with Ctrl+C
"""

from __future__ import annotations

import io

import pytest

__all__ = ["terminal"]


class _Interrupted(io.StringIO):
    """Standard input where the person pressed Ctrl+C at the question."""

    def readline(self, *args, **kwargs) -> str:
        raise KeyboardInterrupt


class Terminal:
    """The standard input of one test; `types`, `closes` and `interrupts` set it."""

    def __init__(self, monkeypatch) -> None:
        self._monkeypatch = monkeypatch

    def types(self, *answers: str) -> None:
        self._stdin(io.StringIO("".join(f"{answer}\n" for answer in answers)))

    def closes(self) -> None:
        self.types()

    def interrupts(self) -> None:
        self._stdin(_Interrupted())

    def _stdin(self, stream: io.StringIO) -> None:
        self._monkeypatch.setattr("sys.stdin", stream)


@pytest.fixture
def terminal(monkeypatch) -> Terminal:
    """A terminal that has no answer typed yet, so a question reads end of input."""
    handle = Terminal(monkeypatch)
    handle.closes()
    return handle
