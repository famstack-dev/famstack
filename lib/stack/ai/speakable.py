"""Text for the ear: what a speech engine is given to say.

Speech engines read markup literally ("asterisk asterisk three emails"),
so text that may be markdown is turned into what a person would say
before it is spoken. Standard library only: the host CLI (`stack ai say`)
and the bots (voice messages) both use it.
"""

from __future__ import annotations

import re

_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://([^/\s]+)\S*")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)(\S(?:.*?\S)?)\1")
_CODE = re.compile(r"`([^`]*)`")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_ITEM = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_RULE = re.compile(r"^\s*\|?\s*:?-{3,}")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+")
_ENDS_SENTENCE = (".", "!", "?", ":", ";")
# A clock time before "Uhr", "am" or "pm". Read as written it is "einundzwanzig
# null null Uhr" (rig, 2026-10-18); people say "21 Uhr", "21 Uhr 30", "9 pm".
_TIME = re.compile(r"\b0?(\d{1,2}):(\d{2})\s*(Uhr|[ap]\.?m\.?)(?!\w)", re.IGNORECASE)


def _said_time(match: re.Match) -> str:
    hour, minutes, word = match.groups()
    if minutes == "00":
        return f"{hour} {word}"
    if word.lower() == "uhr":
        return f"{hour} Uhr {minutes.lstrip('0') or '0'}"
    return f"{hour} {minutes} {word}"


def speakable(text: str) -> str:
    """`text`, maybe markdown, as a person would say it: one line of sentences.

    Emphasis and code marks go, a link becomes its text, a bare URL its
    domain, emoji go; a heading, a list item or a table row becomes a
    sentence of its own (a row as "column: value, ..."); a clock time is
    said the way people say it ("21:00 Uhr" as "21 Uhr").
    """
    lines, header = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _RULE.match(line):
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if header is None:
                header = cells
                continue
            line = ", ".join(f"{h}: {c}" for h, c in zip(header, cells))
        line = _ITEM.sub("", _HEADING.sub("", line))
        line = _URL.sub(r"\1", _LINK.sub(r"\1", line))
        line = _EMPHASIS.sub(r"\2", _CODE.sub(r"\1", line))
        line = _TIME.sub(_said_time, line)
        line = re.sub(r"\s+([.!?,:;])", r"\1", _EMOJI.sub("", line)).strip()
        if line:
            lines.append(line)
    # A line that ran into the next gets a full stop; the last stays as written.
    stopped = [ln if ln.endswith(_ENDS_SENTENCE) else ln + "." for ln in lines[:-1]]
    return " ".join(stopped + lines[-1:])


