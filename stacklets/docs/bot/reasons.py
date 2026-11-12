"""Why a filing was not written, in the stack's language.

    from stacklets.docs.bot.reasons import why

    why("de", "changed")       # 'die Karte hat sich geändert, während ich ...'
    why("en", "unreachable")   # 'the memory does not answer'
    why("de", "exploded")      # the `why_unknown` line: never the code itself

A filing that fails carries a reason code (`CaptureOutcome.failure_reason`),
not a sentence: the memory's codes (`stack.memory.why_not_written`), the
mirror's and the capture pipeline's. Each surface words the code where it
shows it. This module words it for the archivist's answers in the chat and
for `stack docs capture`, from the archivist's messages file
(`messages/archivist.yml`, keys `why_<code>`), so both say the same and a
new code needs one line per language there.

It loads only the messages file, because the capture command runs without
the bot and its Matrix client.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_MESSAGES = yaml.safe_load((Path(__file__).parent / "messages" / "archivist.yml")
                           .read_text(encoding="utf-8"))


def why(language: str, code: str | None) -> str:
    """The reason `code` worded in `language`, English when the stack's
    language has no messages. A code without a line reads as `why_unknown`,
    so a person never sees a code or the curator's own words."""
    key = f"why_{code}"
    if key not in _MESSAGES["en"]:
        key = "why_unknown"
    return _MESSAGES.get(language, _MESSAGES["en"]).get(key, _MESSAGES["en"][key])
