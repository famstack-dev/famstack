"""A `launchctl` for tests: a launchd that remembers its jobs in a folder.

    bin_dir, jobs = launchd(tmp_path, monkeypatch)   # first on PATH
    (jobs / "dev.famstack.omlx").exists()            # loaded
    (jobs / "dev.famstack.api").write_text(wrapper)  # loaded, runs `wrapper`

The unit lane refuses the real `launchctl`, because `launchctl load` puts
a job into the login session of the Mac that runs the tests. This one
answers as launchd does for the commands the stack sends:

    load <plist>      the job named by the file is loaded
    unload <plist>    it leaves, as with `remove`
    remove <label>    it leaves; fails when launchd has no such job
    kickstart -k gui/<uid>/<label>
                      restarts a loaded job in place; fails when there is none
    list <label>      exits 113 when not loaded, else names a PID and
                      the program the agent file named

A job leaves launchd as it does on a real Mac: `remove` and `unload`
return at once, and launchd still lists the job, with its PID, until its
process has ended. A `load` of the same label in that moment prints
`Load failed: 5: Input/output error`, loads nothing, and exits 0. Here the
moment lasts for the next `LEAVING_LISTS` calls of `list` for that label,
so a caller that waits by asking launchd gets through it without a clock.

Every call is written to `$CALLS`. `$LOAD_FAILS` set makes `load` fail,
as launchd does with a broken agent. `$LAUNCHD_STUCK` names a label whose
process does not end after a stop, so launchd lists it as leaving for good.
`$LAUNCHD_NO_PID` names a label whose process ended right after its start:
launchd lists the job without a PID.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["LAUNCHCTL", "LEAVING_LISTS", "launchd"]

# How many `list` calls a leaving job is still listed for.
LEAVING_LISTS = 2

# A loaded job is the file `<label>` in the jobs folder, holding the
# program its agent file named (the first string of `ProgramArguments`),
# or nothing. A leaving job is the file `<label>.leaving`: its first line
# is how many more `list` calls still find it, its second the program.
LAUNCHCTL = """#!/bin/sh
echo "launchctl $*" >> "$CALLS"
jobs="$LAUNCHD_JOBS"
leave() { left=%d; [ "$1" = "$LAUNCHD_STUCK" ] && left=999999
          { echo $left; cat "$jobs/$1"; } > "$jobs/$1.leaving"; rm -f "$jobs/$1"; }
says() { echo '{'; [ "$label" = "$LAUNCHD_NO_PID" ] || echo '  "PID" = 4242;'
         [ -n "$1" ] && printf '  "ProgramArguments" = (\\n    "%%s";\\n  );\\n' "$1"
         echo '};'; }
case "$1" in
  load)   label="$(basename "$2" .plist)"
          if [ -f "$jobs/$label.leaving" ]; then
            echo "Load failed: 5: Input/output error" >&2; exit 0; fi
          [ -n "$LOAD_FAILS" ] && exit 1
          sed -n '/ProgramArguments/,/<\\/array>/s:.*<string>\\(.*\\)</string>.*:\\1:p' "$2" \\
            | head -1 > "$jobs/$label" ;;
  unload) label="$(basename "$2" .plist)"; [ -f "$jobs/$label" ] && leave "$label" ;;
  remove) [ -f "$jobs/$2" ] || exit 3; leave "$2" ;;
  kickstart) [ -f "$jobs/$(basename "$3")" ] || exit 113 ;;
  list)   label="$2"
          if [ -f "$jobs/$2.leaving" ]; then
            left=$(head -1 "$jobs/$2.leaving"); program=$(sed -n 2p "$jobs/$2.leaving")
            if [ "$left" -gt 0 ]; then
              { echo $((left - 1)); echo "$program"; } > "$jobs/$2.leaving"
              says "$program"; exit 0; fi
            rm -f "$jobs/$2.leaving"; fi
          [ -f "$jobs/$2" ] || exit 113; says "$(cat "$jobs/$2")" ;;
esac
exit 0
""" % LEAVING_LISTS


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
