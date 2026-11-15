"""omlx_start.py: famstack's oMLX started, and taken back when the run that started it fails.

    from .omlx_start import running

    with running(ctx, answer_seconds=60) as key:   # the preparation
        download_the_model(ctx)                    # fails: the oMLX it started goes again
    with running(ctx):                             # `stack up ai`
        pass

The preparation (`prepare.py`) starts famstack's oMLX to download through
it, and `stack up ai` (`hooks/on_start.py`) starts it to run. Both start
it here, in the same four steps:

    1  settings      port 42060, the bind address, the shared models
                     (`omlx_service.configure`), a small Mac's memory
                     settings (`omlx_fit`)
    2  key           `omlx.own_key`, which the settings file owns; a copy
                     goes into `[ai] openai_key`
    3  LaunchAgent   written and loaded, or restarted when a setting
                     changed or the oMLX running there refuses the key
                     (`omlx_service.start`); left alone otherwise
    4  the answer    with `answer_seconds`: wait until it lists its models

**It takes back what it started.** A failure in the start or in the
`with` block, Ctrl-C included, stops an oMLX this run loaded and removes
its agent file, so launchd does not start it at the next login although
the run never finished. An oMLX that ran before the run keeps running:
the run took nothing from it. The error is the run's own; a stop that
launchd refuses is only said.

What it does not do: install oMLX (`prepare.install_omlx`), move an
instance off an earlier release's Homebrew service (`homebrew_service.move`,
which wraps this start in that move), or touch an oMLX famstack does not run.
"""

from __future__ import annotations

import contextlib
import urllib.error
import urllib.request
from collections.abc import Generator
from pathlib import Path

from stack.profile import profile_of
from stack.prompt import done, warn
from stack.said import Said

from . import homebrew_omlx, host_services, omlx_service
from .omlx import own_key
from .omlx_fit import fit_settings_file

__all__ = ["OMLX_URL", "running"]

# The address of famstack's oMLX, as the stack on this Mac reaches it.
OMLX_URL = f"http://localhost:{omlx_service.PORT}/v1"
# oMLX answers a request with a key it does not take with this status.
KEY_REFUSED = 401


@contextlib.contextmanager
def running(ctx, *, answer_seconds: float | None = None) -> Generator[str]:
    """Run famstack's oMLX with the stack's settings and its own key for
    the length of the `with` block; yields the key. `ctx` is a hook
    context. Writes `[ai] openai_key`, and `[ai] openai_url` when it names
    no address. The key is never printed or put on a command line.

    With `answer_seconds`, waits until oMLX lists its models and raises
    RuntimeError, naming its log, when it does not in time. Raises
    `omlx_service.UnreadableSettings` before it changes anything for a
    settings file it cannot read, and RuntimeError when launchd cannot
    load the agent.

    When the start or the block fails, an oMLX this call loaded is stopped
    and its agent file removed; one that ran before keeps running.

        with running(ctx, answer_seconds=60) as key:
            ...          # "oMLX running on port 42060, in .../ai/omlx"
    """
    ran_before = omlx_service.LABEL in host_services.up(ctx.shell)
    try:
        yield _start(ctx, answer_seconds)
    except BaseException:
        try:
            if not ran_before:
                host_services.stop(ctx.shell, omlx_service.LABEL)
        except RuntimeError as e:   # said, so the run's own error stays the one reported
            warn(f"famstack's oMLX could not be stopped after the failed start: {e}")
        raise


# ── The start, step by step ──────────────────────────────────────────────

def _start(ctx, answer_seconds: float | None) -> str:
    """The four steps of the start; returns the key."""
    data_dir = Path(ctx.stack.data)
    host = ctx.env.get("PORT_BIND_IP") or "127.0.0.1"
    changed = omlx_service.configure(data_dir, host=host)
    changed = fit_settings_file(omlx_service.settings_file(data_dir),
                                profile_of(ctx.stack.config or {})) or changed
    key = own_key(data_dir)
    ctx.cfg("openai_key", key)
    url = ctx.cfg("openai_url", default="") or ctx.cfg("openai_url", OMLX_URL)
    # A new key in the file reaches an oMLX that runs only at its restart:
    # one that refuses the key reads its settings again.
    changed = _refuses(url, key) or changed
    if changed:
        ctx.step(Said("Starting famstack's oMLX", "ai.start_omlx"))
    omlx_service.start(ctx, data_dir, homebrew_omlx.program(), changed=changed)
    if answer_seconds is not None:
        _wait_for_the_answer(url, key, answer_seconds, data_dir)
    return key


def _refuses(url: str, key: str) -> bool:
    """Whether an oMLX answers at `url` and refuses `key`. False when
    nothing answers there: a stopped oMLX reads its settings when it starts."""
    request = urllib.request.Request(f"{url.rstrip('/')}/models",
                                     headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=omlx_service.ANSWER_SECONDS):
            return False
    except urllib.error.HTTPError as answer:
        return answer.code == KEY_REFUSED
    except (OSError, ValueError):
        return False


def _wait_for_the_answer(url: str, key: str, seconds: float, data_dir: Path) -> None:
    """Return once famstack's oMLX lists its models at `url` with `key`;
    raise RuntimeError, naming its log, when it does not within `seconds`."""
    if not omlx_service.answers(url, key, seconds):
        raise RuntimeError(f"famstack's oMLX did not answer at {url} within {seconds} s. "
                           f"Its log: {omlx_service.folder(data_dir) / 'omlx.log'}")
    done(f"oMLX running on port {omlx_service.PORT}, in {omlx_service.folder(data_dir)}")

