"""Markdown page templates for the wiki: one file per page kind and language.

A template is the layout of a generated page as the family reads it,
`templates/<kind>.<lang>.md`. Its `{{slot}}`s are filled by the compiler,
from facts it knows or prose the model wrote. A paragraph with an empty
slot is left out whole, heading included, so a page never shows an empty
section or a line saying that nothing was recorded.

    >>> fill("# {{name}}\\n\\n## Pets\\n{{pets}}", {"name": "Homer", "pets": ""})
    '# Homer\\n'

The comment at the top of each template explains its slots to whoever
edits it, and is not published. Pure functions: no I/O beyond reading a
template file. `read_template` and `SLOT` are shared with the model
prompts (`prompt_files`), so both kinds of template are read alike.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "templates"

# A `{{name}}` slot, in a page template and in a prompt file alike.
SLOT = re.compile(r"\{\{(\w+)\}\}")
_TEMPLATE_COMMENT = re.compile(r"\A\s*<!--.*?-->\s*", re.S)

LANGUAGE_NAMES = {"en": "English", "de": "German"}

_MONTHS = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "de": ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni",
           "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."],
}


def language(lang: str) -> str:
    """The template language for a household language code; English otherwise."""
    code = (lang or "").strip().lower()[:2]
    return code if code in LANGUAGE_NAMES else "en"


def format_date(iso: str, lang: str) -> str:
    """`2026-02-14` as a reader writes it: `14 Feb 2026`, `14. Feb. 2026`."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", iso or "")
    if not m:
        return iso or ""
    year, month, day = int(m[1]), int(m[2]), int(m[3])
    name = _MONTHS[language(lang)][month - 1]
    return f"{day}. {name} {year}" if language(lang) == "de" else f"{day} {name} {year}"


def format_span(start: str, end: str, lang: str) -> str:
    """A span of days, short: `14–16 Oct 2026`, `30 Oct – 2 Nov 2026`, `14.–16. Okt. 2026`."""
    if not end or end <= start:
        return format_date(start, lang)
    first, last = format_date(start, lang), format_date(end, lang)
    if start[:7] == end[:7]:
        day = first.split(" ", 1)[0]
        return f"{day}–{last}"
    if start[:4] == end[:4]:
        return f"{first.rsplit(' ', 1)[0]} – {last}"
    return f"{first} – {last}"


@functools.cache
def read_template(path: Path) -> str:
    """A template file without its leading `<!-- -->` comment.

    Read as UTF-8 with an optional byte order mark, newlines made `\\n`,
    so a file saved by an editor that adds either reads the same. Cached:
    the files are part of the release and do not change while a process
    runs, and a compile reads each one several times.
    """
    with path.open(encoding="utf-8-sig", newline=None) as f:
        text = f.read()
    return _TEMPLATE_COMMENT.sub("", text, count=1)


def template(kind: str, lang: str) -> str:
    """The template text for a page kind and language, without its comment."""
    return read_template(TEMPLATES / f"{kind}.{language(lang)}.md")


def fill(template_text: str, slots: dict[str, str]) -> str:
    """Fill `{{slot}}`s, leaving out every paragraph that has an empty one."""
    kept: list[str] = []
    for block in re.split(r"\n\s*\n", template_text.strip()):
        names = SLOT.findall(block)
        if any(not (slots.get(n) or "").strip() for n in names):
            continue
        kept.append(SLOT.sub(lambda m: slots[m[1]].strip(), block))
    return "\n\n".join(kept) + "\n"


def text(value) -> str:
    """A model's string field on one line, or "" for anything else."""
    return " ".join(value.split()) if isinstance(value, str) else ""


def sentence(line: str) -> str:
    """`line` ending in a full stop unless it already ends a sentence."""
    line = line.strip()
    return line if not line or line[-1] in ".!?…" else line + "."


def fact(label: str, value: str, cited: str = "") -> str:
    """One "at a glance" line: `- **Born** · 18 Jan 2025 [3]`. A fact, not a sentence."""
    return f"- **{label}** · {value}{cited}"


def cites(value) -> str:
    """` [1, 3]` for a model's list of citation numbers, "" for none."""
    nums = [n for n in value if isinstance(n, int) and not isinstance(n, bool)] \
        if isinstance(value, list) else []
    return f" [{', '.join(map(str, nums))}]" if nums else ""
