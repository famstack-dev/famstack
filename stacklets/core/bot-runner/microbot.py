"""Moved to `stack.bot.microbot` (lib/stack/bot). Kept until 0.6.

An extension bot written before the move imports `microbot` from here; this
file answers that import with the runtime's module and logs which bot to
change. The bots famstack ships import `stack.bot`.
"""

import sys

from loguru import logger

from stack.bot import microbot as _moved


def _importer() -> str:
    """The file whose import reached this module, past the import machinery."""
    frame = sys._getframe(1)
    while frame and ("importlib" in frame.f_code.co_filename
                     or frame.f_code.co_filename == __file__):
        frame = frame.f_back
    return frame.f_code.co_filename if frame else "a bot"


logger.warning("{} imports `microbot`, which moved: use `from stack.bot import MicroBot` instead. "
               "The old name goes in famstack 0.6.", _importer())
sys.modules[__name__] = _moved
