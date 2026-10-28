"""Output adapters for the Stack lifecycle.

The Stack class reports progress through an output object instead of
printing directly. This decouples framework logic from presentation.

Implementations:
  SilentOutput     — discards all messages (default when no output given)
  CollectorOutput  — captures messages in lists (for tests and JSON)
  TerminalOutput   — prints to stderr with formatting (for the CLI)

EventOutput wraps one of them: it passes every call on, and writes each
message as an event line too, for a screen that follows the run (ADR-020).
"""
from __future__ import annotations

import contextlib
import itertools
import os
import sys
import threading
import time

from .live.emitter import Emitter
from .live.envelope import (RESULT, STEP_DONE, STEP_FAILED, STEP_STARTED, WARNING,
                            result_for_screens)


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

    _thread: threading.Thread  # started by __enter__

    def __init__(self, msg):
        self.msg = msg
        self._running = False
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


# ── Events ───────────────────────────────────────────────────────────────
# A run reported as it happens, one JSON object per line, for screens: the
# terminal with `stack up --events`, the host API that relays it, the tools
# server behind it (ADR-020, the `stack.live` package). This is the adapter
# between `Stack.output` and the package's emitter.

class EventOutput:
    """Reports through `inner` and as events on `stream`.

    Every call reaches `inner` as it is, so the terminal shows what it
    always showed. What the terminal shows is also written as an event and
    flushed at once: a step as `step.done`, an error as `step.failed`, a
    warning as `warning`, a spinner as `step.started` and then `step.done`
    or `step.failed` naming the event that started it. `result()` writes
    the last one. Debug lines stay with `inner`: the terminal hides them too.
    No type is called `error`, which is `EventSource`'s own event for a lost
    connection.

        events = EventOutput(TerminalOutput(), sys.stdout)
        events.step("Rendering environment")
        # {"id": 1, "type": "step.done", "time": "...", "data": {"text": "Rendering environment"}}
    """

    def __init__(self, inner, stream):
        self.inner = inner
        self.emitter = Emitter(stream)

    def debug(self, msg: str) -> None:
        self.inner.debug(msg)

    def step(self, msg: str) -> None:
        self.inner.step(msg)
        self.emitter.emit(STEP_DONE, {"text": msg})

    def warn(self, msg: str) -> None:
        self.inner.warn(msg)
        self.emitter.emit(WARNING, {"text": msg})

    def error(self, msg: str) -> None:
        self.inner.error(msg)
        self.emitter.emit(STEP_FAILED, {"text": msg})

    def flush(self) -> None:
        self.inner.flush()

    def spinner(self, msg: str):
        return _EventSpinner(self, msg, self.inner.spinner(msg))

    def result(self, result: dict) -> None:
        """The last event: what the command returns, as far as a screen may
        see it (`result_for_screens`): `ok`, `error` or `cancelled` and
        what goes with them, never the env or the hints."""
        self.emitter.emit(RESULT, result_for_screens(result))


@contextlib.contextmanager
def events_on_stdout(inner):
    """An EventOutput over `inner` that writes to this process's stdout,
    which it keeps for the events while the block runs.

    One stray line would break the one-object-per-line contract the host
    API relays, so everything else that would reach stdout meanwhile goes
    to stderr: Python's own prints (the welcome screen, a hook's output),
    and at the file descriptor the tools a run starts, which inherit it
    (`docker compose pull` and `ctx.shell_live` stream through it).
    """
    sys.stdout.flush()
    events_fd = os.dup(1)
    os.dup2(2, 1)
    stream = os.fdopen(events_fd, "w", encoding="utf-8")
    try:
        with contextlib.redirect_stdout(sys.stderr):
            yield EventOutput(inner, stream)
    finally:
        sys.stdout.flush()
        stream.flush()
        os.dup2(events_fd, 1)
        stream.close()


class _EventSpinner:
    """A spinner as two events: its start, and its end naming the start.

    An exception out of the block ends it as failed. The terminal's
    spinner shows a tick then, and the event does not repeat that.
    """

    def __init__(self, events: EventOutput, msg: str, inner):
        self.events = events
        self.msg = msg
        self.inner = inner
        self._started = 0
        self._ok = True
        self._hint = None

    def __enter__(self):
        self.inner.__enter__()
        self._started = self.events.emitter.emit(STEP_STARTED, {"text": self.msg}).id
        return self

    def __exit__(self, exc_type, exc, tb):
        self.inner.__exit__(exc_type, exc, tb)
        failed = not self._ok or exc_type is not None
        data = {"text": self.msg, "started": self._started}
        if failed and self._hint:
            data["hint"] = self._hint
        self.events.emitter.emit(STEP_FAILED if failed else STEP_DONE, data)

    def fail(self, hint=None):
        self._ok = False
        self._hint = hint
        self.inner.fail(hint)
