"""stack memory sync - mirror memory source into the brain projection now.

The curator is the ONLY writer of the brain projection (one-writer
invariant, ADR-011). This command therefore mirrors nothing itself: it
drops a `mirror-now` trigger file the curator's tick loop watches,
then waits until the curator records the current memory HEAD as
mirrored. The explicit fast path for tests and operators who need
read-your-writes in the rendered wiki without waiting for the next
curator tick - without becoming a second git writer on brain.

Failure shape: if the curator is down (memory stacklet stopped) or
busy past the wait cap (a nightly LLM sweep can hold a cycle for
minutes), this times out with the lag printed and a nonzero exit.
The trigger file survives either way; the curator consumes it on its
next free tick, so the request is never lost.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import vault_path_for  # noqa: E402

HELP = "Mirror memory source into the brain projection now"

TRIGGER_NAME = "mirror-now"
MIRROR_SHA_NAME = "last-mirrored-sha"

# Wait cap per the test-loop rule: polls are cheap, caps are hard.
WAIT_SECS = 40.0
POLL_INTERVAL = 0.75


def request_mirror(state_dir: Path) -> Path:
    """Drop the trigger file the curator's tick loop watches.

    Content is a timestamp purely for debuggability; the curator only
    cares that the file exists and consumes it by deletion.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    trigger = state_dir / TRIGGER_NAME
    trigger.write_text(str(time.time()), encoding="utf-8")
    return trigger


def remote_head(memory: Path) -> str:
    """Memory's remote HEAD - the commit the mirror must reach.

    The origin URL of the working copy already embeds credentials (set
    at clone time), so `ls-remote` needs no token plumbing here. Falls
    back to the local HEAD when Forgejo is unreachable: the curator
    pulls before mirroring, so a local-only target is still a valid
    best-effort floor.
    """
    out = _git(memory, "ls-remote", "origin", "HEAD")
    if out and out.split():
        return out.split()[0]
    return (_git(memory, "rev-parse", "HEAD") or "").strip()


def mirrored_contains(memory: Path, target: str, mirrored: str) -> bool:
    """True when the curator's recorded mirror sha includes `target`.

    Checked against the memory clone's history: the curator pulls that
    same working copy before mirroring, so once `target` is mirrored
    both commits exist there. A sha git does not know yet is simply
    "not yet" - the caller keeps polling.
    """
    if not mirrored:
        return False
    if mirrored == target:
        return True
    rc = subprocess.run(
        ["git", "-C", str(memory), "merge-base", "--is-ancestor", target, mirrored],
        capture_output=True,
    ).returncode
    return rc == 0


def wait_for_mirror(
    state_dir: Path,
    memory: Path,
    target: str,
    *,
    timeout: float = WAIT_SECS,
    interval: float = POLL_INTERVAL,
) -> str | None:
    """Poll `last-mirrored-sha` until it contains `target`.

    Returns the mirrored sha on success, None on timeout. Checks the
    condition once before looking at the clock, so an already-current
    mirror succeeds even with a zero timeout.
    """
    sha_file = state_dir / MIRROR_SHA_NAME
    deadline = time.monotonic() + timeout
    while True:
        mirrored = ""
        if sha_file.exists():
            mirrored = sha_file.read_text(encoding="utf-8").strip()
        if mirrored_contains(memory, target, mirrored):
            return mirrored
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def _git(repo: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def run(args, stacklet, config):
    data_dir = config.get("data_dir")
    if not data_dir:
        return {"error": "stack data_dir not configured"}

    memory = vault_path_for(Path(data_dir))
    state_dir = Path(data_dir) / "memory" / "curator"

    if not (memory / ".git").exists():
        return {"error": f"memory vault not cloned at {memory}"}

    target = remote_head(memory)
    if not target:
        return {"error": "cannot resolve memory HEAD"}

    request_mirror(state_dir)
    mirrored = wait_for_mirror(state_dir, memory, target)
    if mirrored is None:
        return {"error": (
            f"curator did not mirror {target[:10]} within {int(WAIT_SECS)}s "
            "- is the memory stacklet running (or busy in a nightly sweep)?"
        )}

    print(f"Brain projection current at {mirrored[:10]} (source {target[:10]})")
    return {"mirrored": mirrored, "target": target}
