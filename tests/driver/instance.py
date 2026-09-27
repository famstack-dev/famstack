"""Where the instance lives, and how to reach its surfaces.

Commands run on the instance's Mac in a login shell, so the admin's own PATH
applies: in this checkout, or over ssh in the checkout named by `root`. The
only surface this module touches is `./stack`.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
REMOTE_CHECKOUT = "~/famstack"


class Instance:
    """The one famstack instance a driver command acts on.

    Every other module reaches the stack through it: it knows whether the
    instance is this checkout or another Mac over ssh, runs `./stack` there
    with `--json` and hands back the answer, and copies a local file over
    when a command needs one on that Mac.

        rig = Instance(host="famstack-e2e", root=None)
        rig.stack("messages", "send", "picnic", "hi", "--as", "marge")["event_id"]
    """

    def __init__(self, host: str | None, root: str | None):
        self.host = host
        self.root = root or (REMOTE_CHECKOUT if host else str(REPO))

    @property
    def name(self) -> str:
        return self.host or "local"

    def argv(self, command: str, *, tty: bool = False) -> list[str]:
        """The process that runs `command` in the instance's checkout."""
        full = f"cd {self.root} && {command}"
        if not self.host:
            return ["zsh", "-lc", full]
        return ["ssh", *(["-tt"] if tty else []), self.host, f"zsh -lc {shlex.quote(full)}"]

    def run(self, command: str) -> subprocess.CompletedProcess:
        return subprocess.run(self.argv(command), capture_output=True, text=True)

    def stack(self, *args: str) -> dict:
        """`./stack <args> --json` on the instance; its answer, or exit with its error."""
        line = "./stack " + " ".join(shlex.quote(a) for a in args)
        result = self.run(line + " --json")
        answer = _json_or_exit(line, result)
        if result.returncode != 0 or "error" in answer:
            sys.exit(f"driver: `{line}` failed: {answer.get('error') or result.stderr.strip()}")
        return answer

    def put(self, local: Path) -> str:
        """Copy a file to the instance's Mac; the path it has there.

        The file keeps its name, in a directory of its own: the upload is
        posted under that name, as a phone posts a file under its own.
        """
        if not self.host:
            return str(local.resolve())
        remote = f"/tmp/driver-{time.time_ns()}/{local.name}"
        subprocess.run(["ssh", self.host, f"mkdir -p {shlex.quote(str(Path(remote).parent))}"],
                       check=True)
        subprocess.run(["scp", "-q", str(local), f"{self.host}:{remote}"], check=True)
        return remote

    def address(self) -> str:
        """The name other machines reach the instance's Mac by."""
        if not self.host:
            return "localhost"
        config = subprocess.run(["ssh", "-G", self.host], capture_output=True, text=True).stdout
        return next((line.split()[1] for line in config.splitlines()
                     if line.startswith("hostname ")), self.host)


def _json_or_exit(line: str, result: subprocess.CompletedProcess) -> dict:
    if not result.stdout.strip():
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.exit(f"driver: `{line}` gave no JSON:\n{result.stdout}{result.stderr}")
