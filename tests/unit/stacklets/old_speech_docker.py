"""A `docker` for tests of the ai hooks: one container, 0.4's speech container.

    calls = old_speech_docker(tmp_path, monkeypatch, project="stack-ai")
    ...                                   # run a hook
    calls()   # ["rm -f stack-ai-speech", "image rm sha256:0ld1mage"]

The unit lane refuses the real `docker`, because it acts on the containers
of the instance on the same Mac. This one knows one container,
`stack-ai-speech`, in the compose project `project`, and answers as the
real client does for the commands the hooks send:

    ps -a --filter label=com.docker.compose.project=<project>
                      lists the container until it is removed
    inspect           names its image, `sha256:0ld1mage`
    rm -f <name>      removes it; with `rm_fails` it exits 1 and it stays
    image rm <image>  removes the image

Every `rm` and `image` call is written to `$CALLS`, which the launchd
stand-in shares; `calls()` leaves launchd's lines out.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

__all__ = ["old_speech_docker"]

DOCKER = """#!/bin/sh
[ "$1" = "--context" ] && shift 2
case "$1" in
  ps)      [ "$4" = "label=com.docker.compose.project=$PROJECT" ] && [ ! -e "$CALLS.removed" ] &&
             printf 'stack-ai-speech\\t%s\\trunning\\tUp 2 hours\\n' "$PROJECT" ;;
  inspect) echo "sha256:0ld1mage" ;;
  rm)      echo "$*" >> "$CALLS"
           [ -n "$RM_FAILS" ] && { echo "Error: daemon busy" >&2; exit 1; }
           touch "$CALLS.removed" ;;
  image)   echo "$*" >> "$CALLS" ;;
  *)       echo "unexpected: docker $*" >&2; exit 125 ;;
esac
"""


def old_speech_docker(tmp_path: Path, monkeypatch, project: str,
                      rm_fails: bool = False) -> Callable[[], list[str]]:
    """Put the stand-in first on PATH, with the container in `project`.
    Returns a function that lists the docker commands it ran."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "docker").write_text(DOCKER)
    (bin_dir / "docker").chmod(0o755)
    calls = tmp_path / "calls"
    monkeypatch.setenv("PATH", str(bin_dir), prepend=":")
    monkeypatch.setenv("CALLS", str(calls))
    monkeypatch.setenv("PROJECT", project)
    if rm_fails:
        monkeypatch.setenv("RM_FAILS", "1")
    return lambda: [line for line in (calls.read_text().splitlines() if calls.exists() else [])
                    if not line.startswith("launchctl ")]
