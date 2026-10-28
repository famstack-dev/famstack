"""The wiki's model prompts as files: one per page kind, read as the model reads it.

A prompt is `templates/prompts/<kind>.md`, written in English whatever
the household language (the prompt names the language to answer in).
The file holds the prompt as the model gets it: JSON examples are
written plainly, and only three kinds of marker are filled in.

- `{{name}}` is replaced by a value the page module computes.
- `{{#name}}...{{/name}}` is kept only when the value is set (a
  non-empty string, or True).
- `{{^name}}...{{/name}}` is kept only when it is not.

The markers are a subset of Mustache, without its HTML escaping. A
block marker alone on its line takes the line with it, so a block that
is left out leaves no blank line behind.

    >>> fill("Hi {{name}}.\\n{{#pets}}\\nPets: {{pets}}\\n{{/pets}}\\nBye.\\n", {"name": "Bart", "pets": ""})
    'Hi Bart.\\nBye.\\n'

The comment at the top of each file says what it is and which code fills
it, and is not sent to the model. Prompts are files rather than strings
in the code because a person reads and edits them as the prompt, and
because each prompt's fixed text can then be hashed and measured on its
own. Pure functions: no I/O beyond reading a prompt file.
"""

from __future__ import annotations

import re
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent / "templates" / "prompts"

_COMMENT = re.compile(r"\A\s*<!--.*?-->\s*", re.S)
_STANDALONE_BLOCK_MARKER = re.compile(r"^[ \t]*(\{\{[#^/]\w+\}\})[ \t]*\n", re.M)
_BLOCK = re.compile(r"\{\{([#^])(\w+)\}\}(.*?)\{\{/\2\}\}", re.S)
_VALUE = re.compile(r"\{\{(\w+)\}\}")


def prompt_text(kind: str) -> str:
    """The prompt file for a page kind, without its comment and still unfilled."""
    text = (PROMPTS / f"{kind}.md").read_text(encoding="utf-8")
    return _COMMENT.sub("", text, count=1)


def fill(text: str, values: dict[str, str | bool]) -> str:
    """Fill a prompt's markers from `values`. A marker without a value is a KeyError."""
    text = _STANDALONE_BLOCK_MARKER.sub(r"\1", text)
    text = _blocks(text, values)
    # One pass, so a value that happens to contain `{{x}}` stays as written.
    return _VALUE.sub(lambda m: str(values[m[1]]), text)


def _blocks(text: str, values: dict[str, str | bool]) -> str:
    """Keep or drop each block; blocks inside a kept block are decided in turn."""
    def keep(m: re.Match) -> str:
        wanted = bool(values[m[2]]) == (m[1] == "#")
        return _blocks(m[3], values) if wanted else ""
    return _BLOCK.sub(keep, text)


def prompt(kind: str, values: dict[str, str | bool]) -> str:
    """The filled prompt for a page kind."""
    return fill(prompt_text(kind), values)
