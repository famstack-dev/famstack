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
is left out leaves no blank line behind. Blocks of different names may
nest; a block inside a block of the same name is not supported.

A marker that does not resolve is an error that names the file and the
marker, never text sent to the model: a mistyped closer, an unclosed
block, a same-name nested block, `{{ name }}` with spaces, `{{first-name}}`,
or a name the page module gives no value for.

    >>> render_text("Hi {{name}}.\\n{{#pets}}\\nPets: {{pets}}\\n{{/pets}}\\nBye.\\n",
    ...             {"name": "Bart", "pets": ""})
    'Hi Bart.\\nBye.\\n'

The comment at the top of each file says what it is and which code fills
it, and is not sent to the model. Files are read like the page templates
(`page_template.read_template`): cached, BOM and CRLF tolerated. Prompts
are files rather than strings in the code because a person reads and
edits them as the prompt, and because each prompt's fixed text can then
be hashed and measured on its own. Pure functions: no I/O beyond
reading a prompt file.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .page_template import SLOT, read_template

PROMPTS = Path(__file__).resolve().parent / "templates" / "prompts"

_STANDALONE_BLOCK_MARKER = re.compile(r"^[ \t]*(\{\{[#^/]\w+\}\})[ \t]*\n", re.M)
_BLOCK = re.compile(r"\{\{([#^])(\w+)\}\}(.*?)\{\{/\2\}\}", re.S)
_LEFTOVER = re.compile(r"\{\{.*?(?:\}\}|$)", re.M)

Values = dict[str, str | bool]


class PromptError(ValueError):
    """A prompt file whose markers do not resolve: a bug in the file or its caller."""


def render(kind: str, values: Values) -> str:
    """The prompt for a page kind (`templates/prompts/<kind>.md`), filled from `values`."""
    path = PROMPTS / f"{kind}.md"
    return render_text(read_template(path), values, source=path.name)


def fingerprint(kind: str) -> str:
    """A short hash of the prompt for a page kind as the model reads it.

    A page's base (`fold.py`) carries the fingerprint of the prompt it was
    written under, and a changed prompt rebuilds it. The comment at the
    top is not part of it: editing the comment changes nothing the model
    reads.
    """
    text = read_template(PROMPTS / f"{kind}.md")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def render_text(text: str, values: Values, *, source: str = "<text>") -> str:
    """Fill a prompt's markers from `values`; `source` names the file in errors.

    Raises `PromptError` for a marker that does not resolve, and for a
    `{{name}}` or block with no entry in `values`.
    """
    text = _STANDALONE_BLOCK_MARKER.sub(r"\1", text)
    text = _blocks(text, values, source)
    if leftover := _LEFTOVER.search(SLOT.sub("", text)):
        raise PromptError(f"{source}: marker {leftover[0]!r} does not resolve")
    # One pass, so a value that happens to contain `{{x}}` stays as written.
    return SLOT.sub(lambda m: str(_value(values, m[1], source)), text)


def _blocks(text: str, values: Values, source: str) -> str:
    """Keep or drop each block; blocks inside a kept block are decided in turn."""
    def keep(m: re.Match) -> str:
        wanted = bool(_value(values, m[2], source)) == (m[1] == "#")
        return _blocks(m[3], values, source) if wanted else ""
    return _BLOCK.sub(keep, text)


def _value(values: Values, name: str, source: str) -> str | bool:
    try:
        return values[name]
    except KeyError:
        raise PromptError(f"{source}: no value for {{{{{name}}}}}") from None
