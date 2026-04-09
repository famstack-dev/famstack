from __future__ import annotations

"""Output adapters for the Stack lifecycle.

The Stack class reports progress through an output object instead of
printing directly. This decouples framework logic from presentation.

Implementations:
  SilentOutput     — discards all messages (default when no output given)
  CollectorOutput  — captures messages in lists (for tests and JSON)
  TerminalOutput   — prints to stderr with formatting (for the CLI)
"""

import itertools
import sys
import threading
import time


class SilentOutput:
    """Discards all output. Used as the default when no output is provided."""

    def debug(self, msg: str) -> None:
        pass

    def step(self, msg: str) -> None:
        pass

    def warn(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass

    def flush(self) -> None:
        pass

    def spinner(self, msg: str):
        return _NoopSpinner()


class CollectorOutput:
    """Captures all output in lists for inspection."""

    def __init__(self):
        self.debug_msgs: list[str] = []
        self.steps: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def debug(self, msg: str) -> None:
        self.debug_msgs.append(msg)

    def step(self, msg: str) -> None:
        self.steps.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def flush(self) -> None:
        pass

    def spinner(self, msg: str):
        return _NoopSpinner()


_BRAILLE = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"


class TerminalOutput:
    """Prints progress to stderr with stack colors.

    debug() is suppressed unless --verbose is set.
    step() prints a static line with a checkmark — for fast operations.
    spinner() returns a context manager with animated braille — for slow ones.
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def debug(self, msg: str) -> None:
        if self.verbose:
            from .prompt import DIM, RESET
            print(f"  {DIM}  {msg}{RESET}", file=sys.stderr)

    def step(self, msg: str) -> None:
        from .prompt import GREEN, RESET
        print(f"  {GREEN}\u2713{RESET}  {msg}", file=sys.stderr)

    def warn(self, msg: str) -> None:
        from .prompt import ORANGE, RESET
        print(f"  {ORANGE}\u26a0{RESET}  {msg}", file=sys.stderr)

    def error(self, msg: str) -> None:
        from .prompt import RED, RESET
        print(f"  {RED}\u2717{RESET}  {msg}", file=sys.stderr)

    def flush(self) -> None:
        pass

    def spinner(self, msg: str):
        """Context manager that shows an animated spinner for slow operations.

        Falls back to a static line when stderr is not a TTY (captured output).
        """
        if sys.stderr.isatty():
            return _TerminalSpinner(msg)
        return _StaticSpinner(msg)


class _NoopSpinner:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def fail(self, hint=None):
        pass


class _StaticSpinner:
    """Non-animated spinner for non-TTY output (captured in tests/pipes)."""

    def __init__(self, msg):
        self.msg = msg
        self._ok = True
        self._hint = None

    def __enter__(self):
        from .prompt import DIM, RESET
        print(f"  {DIM}\u2022{RESET} {self.msg}...", file=sys.stderr)
        return self

    def __exit__(self, *args):
        from .prompt import GREEN, RED, DIM, RESET
        icon = f"{GREEN}\u2713{RESET}" if self._ok else f"{RED}\u2717{RESET}"
        print(f"  {icon}  {self.msg}", file=sys.stderr)
        if not self._ok and self._hint:
            print(f"     {DIM}{self._hint}{RESET}", file=sys.stderr)

    def fail(self, hint=None):
        self._ok = False
        self._hint = hint


class _TerminalSpinner:
    """Animated braille spinner. Use as context manager."""

    def __init__(self, msg):
        self.msg = msg
        self._running = False
        self._thread = None
        self._ok = True
        self._hint = None

    def __enter__(self):
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._running = False
        self._thread.join()
        from .prompt import GREEN, RED, DIM, RESET
        icon = f"{GREEN}\u2713{RESET}" if self._ok else f"{RED}\u2717{RESET}"
        print(f"\r  {icon}  {self.msg}" + " " * 10, file=sys.stderr)
        if not self._ok and self._hint:
            print(f"     {DIM}{self._hint}{RESET}", file=sys.stderr)

    def fail(self, hint=None):
        self._ok = False
        self._hint = hint

    def _spin(self):
        from .prompt import ORANGE, RESET
        for frame in itertools.cycle(_BRAILLE):
            if not self._running:
                break
            print(f"\r  {ORANGE}{frame}{RESET}  {self.msg}",
                  end="", flush=True, file=sys.stderr)
            time.sleep(0.08)
