"""A message sent from the command line looks as a bot's would.

Every bot converts markdown to Matrix HTML with one function,
`stack.matrix.html.matrix_html`. `stack messages send` runs on the host,
where the `markdown` package is not installed, so it carries a
standard-library subset. For the markdown it supports, it must give the
same HTML as that function, with newlines kept as breaks the way a chat
keeps them; otherwise one message reads two ways depending on who sent it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from stacklets.messages.cli import send
from stack.matrix.html import matrix_html

SUPPORTED = [
    "**Backup done** in `4 min`",
    "Line one\nline two",
    "One paragraph\n\nand another",
    "- Kohlrabi\n- **Eier**",
    "Photos backed up:\n\n- 41 new\n- 2 `failed`\n\nNext run tonight.",
    "Status:\n- a line that only looks like an item",
    "  indented by the shell\n",
]


@pytest.mark.parametrize("text", SUPPORTED)
def test_the_host_subset_gives_the_html_a_bot_would_send(text):
    assert send._simple_markdown_to_html(text) == matrix_html(text, line_breaks=True)


def test_text_without_formatting_is_sent_without_html():
    assert send._simple_markdown_to_html("Backup done") is None
