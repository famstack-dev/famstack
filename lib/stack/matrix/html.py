"""Markdown as the HTML a Matrix message carries (`formatted_body`).

Every bot sends markdown as the plain `body` and this HTML next to it, so
a client that shows HTML and one that does not read the same message
(ADR-016 §4). One function, so a card or an answer looks the same
whichever bot sends it.

    html = matrix_html("**Filed** the invoice\\n\\n- EUR 340.00 a year")

The host's `stack messages send` cannot import the `markdown` package;
it carries a standard-library subset that a test pins to this output.
"""

from __future__ import annotations

import re

import markdown

# A task line (`- [ ] buy milk`): markdown has no checkbox Matrix allows,
# so the box is drawn as a character.
_TASK = re.compile(r"^(\s*[-*+]\s+)\[([ xX])\](?=\s)", re.MULTILINE)
_BOX = {" ": "☐", "x": "☑", "X": "☑"}


def matrix_html(text: str, *, line_breaks: bool = False) -> str:
    """`text` (markdown) as HTML for `formatted_body`, tables and fenced code included.

    `line_breaks` keeps every newline as a break, as a chat does; a pasted
    email body needs it, where single newlines would otherwise run together.

        >>> matrix_html("- [ ] Pay the invoice")
        '<ul>\\n<li>☐ Pay the invoice</li>\\n</ul>'
    """
    extensions = ["tables", "fenced_code"] + (["nl2br"] if line_breaks else [])
    boxed = _TASK.sub(lambda m: f"{m[1]}{_BOX[m[2]]}", text)
    return markdown.markdown(boxed, extensions=extensions)
