"""Take the spoken reminder out of the agent's skills when the house has no speech.

    python3 speech_off.py <skills dir> <speech off>   # entrypoint.sh, after the skills are copied

A reminder is two scheduler entries (`skills/reminders`): `remind-<x>` posts
the message, its twin `remind-<x>-say` runs `ai say`. With `[ai] speech =
false` nothing speaks, so every twin fails at its time and fills the
schedule log with failed runs. The model cannot be trusted to skip a line
it reads as an instruction, so with speech off the start writes the skill
without the twin: every line that names `-say` goes. With speech on, or no
value, the skill stays as shipped.

The setting reaches the container as `AGENT_SPEECH_OFF` (`{ai_speech_off}`,
"true" or empty), one value per install. The skill stays the same for every
turn, so nanobot's cached prompt prefix holds.

It reads and writes only `reminders/SKILL.md` in the skills folder, which the
start has just copied from the image. The `speaking` skill also runs `ai
say`, on request; it is left alone.

    >>> without_spoken("post it\\nremind-x-say says it\\nlist them\\n")
    'post it\\nlist them\\n'
"""

from __future__ import annotations

import sys
from pathlib import Path

# The skill writes the spoken twin's name on every line that belongs to it
# only, so one marker finds them all.
SPOKEN_MARKER = "-say"
REMINDERS = Path("reminders") / "SKILL.md"


def without_spoken(text: str) -> str:
    """`text` without the lines that name the spoken twin; the rest unchanged."""
    lines = text.splitlines(keepends=True)
    return "".join(line for line in lines if SPOKEN_MARKER not in line)


def main(argv: list[str]) -> int:
    """Rewrite `<skills>/reminders/SKILL.md` without its spoken lines when
    `<speech off>` is "true". Any other value, or no reminders skill,
    changes nothing. Returns the exit code."""
    if len(argv) != 2:
        print("usage: speech_off.py <skills dir> <speech off>", file=sys.stderr)
        return 2
    skills, speech_off = Path(argv[0]), argv[1]
    skill = skills / REMINDERS
    if speech_off != "true" or not skill.is_file():
        return 0
    skill.write_text(without_spoken(skill.read_text()))
    print("reminders: speech is off, so a reminder is the chat message only")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
