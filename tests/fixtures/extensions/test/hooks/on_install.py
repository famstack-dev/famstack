"""Test stacklet on_install: creates the data directory and a marker file.

It reports three steps, so a test can follow them as events
(`stack up test --events`). Two switches come from the environment the
CLI runs in, both off unless a test sets them:

- `TEST_STEP_SECS`: seconds to wait before each step (default 0), so a
  run lasts long enough to watch it stream.
- `TEST_FAIL=install`: the hook fails, as a broken install does.
"""

import os
import time
from pathlib import Path


def run(ctx):
    pause = float(os.environ.get("TEST_STEP_SECS") or 0)
    if os.environ.get("TEST_FAIL") == "install":
        raise RuntimeError("TEST_FAIL=install: failing on purpose")

    data_dir = ctx.env.get("TEST_DATA_DIR", "")
    if not data_dir:
        ctx.step("ERROR: TEST_DATA_DIR not set")
        return

    time.sleep(pause)
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    ctx.step("Creating the data directory")
    time.sleep(pause)
    (Path(data_dir) / ".install-marker").write_text("installed")
    ctx.step("Writing the install marker")
    time.sleep(pause)
    ctx.step("on_install complete")
