"""A `ps` for tests that answers `ps -axo comm=` as macOS does, on any host.

    ps = macos_ps(tmp_path)                    # the stand-in's path
    omlx_app.app_check(bundle, ps=ps)          # the app check, with it
    omlx_service.agent(data, program, ps=ps)   # the guard, with it

The oMLX app check (`stacklets/ai/omlx_app.py`) finds the app by the
executable path of each process: on a Mac `ps -axo comm=` prints the
path a process was started from, for example
`/Applications/oMLX.app/Contents/MacOS/oMLX`. On Linux the same command
prints only the name (`oMLX`), so a test that starts a stand-in app and
asks the real `ps` passes on a Mac and fails on CI's Linux runner.

This stand-in gives the Mac's answer on both. On a Mac it is the real
`/bin/ps`. On Linux it prints the first argument of each process
(`/proc/<pid>/cmdline`), which is the path the process was started from:
the path a Mac prints, and never a later argument, so a process that only
names the app's path is not taken for the app. It answers only the
`-axo comm=` question and ignores its arguments.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["macos_ps"]

_PS = """#!/bin/sh
[ -d /proc/self ] || exec /bin/ps "$@"
for cmdline in /proc/[0-9]*/cmdline; do
  /usr/bin/tr '\\0' '\\n' < "$cmdline" 2>/dev/null | /usr/bin/head -n 1
done
exit 0
"""


def macos_ps(folder: Path) -> str:
    """Write the stand-in into `folder/ps-stand-in/ps` and return its path.

    It is called by its full path, as the guard calls `/bin/ps`, so it
    needs no place on `PATH`.
    """
    bin_dir = folder / "ps-stand-in"
    bin_dir.mkdir(parents=True, exist_ok=True)
    ps = bin_dir / "ps"
    ps.write_text(_PS)
    ps.chmod(0o755)
    return str(ps)
