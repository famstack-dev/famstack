"""A `launchctl` for tests: a launchd that remembers its jobs in a folder.

    bin_dir, jobs = launchd(tmp_path, monkeypatch)   # first on PATH
    (jobs / "dev.famstack.omlx").exists()            # loaded

The unit lane refuses the real `launchctl`, because `launchctl load` puts
a job into the login session of the Mac that runs the tests. This one
answers as launchd does for the commands the stack sends:

    load <plist>      the job named by the file is loaded
    unload <plist>    it is not
    remove <label>    it is not; fails when launchd has no such job
    kickstart -k gui/<uid>/<label>
                      restarts a loaded job in place; fails when there is none
    list <label>      exits 113 when not loaded, else names a PID

Every call is written to `$CALLS`. `$LOAD_FAILS` set makes `load` fail,
as launchd does with a broken agent.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["LAUNCHCTL", "launchd"]

LAUNCHCTL = """#!/bin/sh
echo "launchctl $*" >> "$CALLS"
jobs="$LAUNCHD_JOBS"
case "$1" in
  load)   [ -n "$LOAD_FAILS" ] && exit 1; touch "$jobs/$(basename "$2" .plist)" ;;
  unload) rm -f "$jobs/$(basename "$2" .plist)" ;;
  remove) [ -f "$jobs/$2" ] || exit 3; rm -f "$jobs/$2" ;;
  kickstart) [ -f "$jobs/$(basename "$3")" ] || exit 113 ;;
  list)   [ -f "$jobs/$2" ] || exit 113; echo '{ "PID" = 4242; };' ;;
esac
exit 0
"""


def launchd(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Put the stand-in first on PATH. Returns its folder, which a test may
    add other stand-ins to, and the folder of loaded jobs. Calls go to
    `<tmp_path>/calls`."""
    import os

    bin_dir, jobs, calls = tmp_path / "bin", tmp_path / "launchd", tmp_path / "calls"
    bin_dir.mkdir(exist_ok=True)
    jobs.mkdir(exist_ok=True)
    if not calls.exists():
        calls.write_text("")
    (bin_dir / "launchctl").write_text(LAUNCHCTL)
    (bin_dir / "launchctl").chmod(0o755)
    monkeypatch.setenv("CALLS", str(calls))
    monkeypatch.setenv("LAUNCHD_JOBS", str(jobs))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return bin_dir, jobs
