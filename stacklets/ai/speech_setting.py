"""speech_setting.py: whether this instance runs speech on this Mac.

    from ..speech_setting import speech_is_off

    if speech_is_off(ctx.stack.config):
        return          # no Whisper build, no Whisper start, no voice download

Speech is the Whisper server for voice messages and the house voice that
speaks answers. Two things turn it off:

- `[ai] speech = false` in `stack.toml`. The install writes it on a Mac
  with less than 24 GB of memory (`stack.profile`), because Whisper alone
  keeps 1.6 GB loaded. An admin may write it, or `speech = true`, by hand.
  The key's name lives in `stack.profile.SPEECH_KEY`, because the install
  and doctor, which write and read it too, may not import this stacklet.
- `stack up ai --no-voice`, which sets `STACK_AI_NO_VOICE=1` for one run,
  to work on the language model alone.

A missing `speech` key means speech on: that is every instance installed
before the setting existed, and an update changes nothing it uses.

The health check of the Whisper server reads the same two things through
the manifest (`skip_when_env`, `skip_when_off`), because the framework
runs it, not this stacklet.

What it does not do: turning speech off on an instance that runs Whisper
stops nothing. The next `stack up ai` only no longer installs or
reconciles it.
"""

from __future__ import annotations

import os

from stack.profile import SPEECH_KEY

__all__ = ["speech_is_off"]


def speech_is_off(config: dict) -> bool:
    """Whether speech is off for this run: `[ai] speech = false` in the
    parsed `stack.toml`, or `STACK_AI_NO_VOICE=1` (`--no-voice`).

    Only the boolean `false` turns it off; a missing key is speech on.

        >>> speech_is_off({"ai": {"speech": False}})
        True
        >>> speech_is_off({"ai": {}})
        False
    """
    if os.environ.get("STACK_AI_NO_VOICE") == "1":
        return True
    return config.get("ai", {}).get(SPEECH_KEY, True) is False
