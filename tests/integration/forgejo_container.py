"""A Forgejo of a test's own, for what only Forgejo can answer.

The memory's cards are written and read through Forgejo's API, and what
that API does (several files in one commit, a write against a blob hash
the file is no longer at, the files a single commit lists) is Forgejo's
to say, not a stand-in's. A test module starts one here, with the image
the code stacklet ships, read from its compose file so the two cannot
drift apart: its install done, an admin, and the empty repo
`family/memory`.

    pytestmark = needs_docker

    @pytest.fixture(scope="module")
    def forgejo():
        with running_forgejo("stack-test-card-write-forgejo") as forgejo:
            yield forgejo        # Forgejo(url, token), the token the admin's

Each module names its own container, and Docker picks the port, so the
rig's `stack-code` is never touched. The container is removed afterwards
whatever happens.
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from stack.forgejo import ForgejoClient  # noqa: E402

ADMIN, PASSWORD = "stackadmin", "only-for-this-test-7f3a"
OWNER, REPO = "family", "memory"


def _has_docker() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return True


needs_docker = pytest.mark.skipif(not _has_docker(), reason="Docker not available")


class Forgejo(NamedTuple):
    """A running Forgejo: its address, and a token of its admin's."""

    url: str
    token: str


def code_image() -> str:
    """The Forgejo image the code stacklet runs."""
    compose = yaml.safe_load((REPO_ROOT / "stacklets" / "code" / "docker-compose.yml").read_text())
    return compose["services"]["stack-code"]["image"]


@contextmanager
def running_forgejo(container: str) -> Iterator[Forgejo]:
    """A fresh Forgejo in the container `container`, with its install done,
    an admin, and the empty repo `family/memory`, removed on the way out."""
    subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=60)
    _docker("run", "-d", "--name", container, "-p", "127.0.0.1::3000",
            "-e", "FORGEJO__security__INSTALL_LOCK=true",
            "-e", "FORGEJO__database__DB_TYPE=sqlite3",
            code_image(), timeout=600)
    try:
        url = "http://" + _docker("port", container, "3000/tcp").splitlines()[0]
        _wait_until_up(url, container)
        _docker("exec", "-u", "git", container, "forgejo", "admin", "user", "create",
                "--username", ADMIN, "--password", PASSWORD, "--email", "stackadmin@local",
                "--admin", "--must-change-password=false")
        admin = ForgejoClient(url=url, admin_user=ADMIN, admin_password=PASSWORD)
        token = admin.issue_token(ADMIN, PASSWORD, container,
                                  ["write:repository", "write:user", "write:organization"])
        admin.create_org(OWNER)
        admin.create_repo(OWNER, REPO, owner_is_org=True)
        yield Forgejo(url, token)
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=60)


# ── Docker ───────────────────────────────────────────────────────────────

def _docker(*args: str, timeout: int = 120) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True,
                          timeout=timeout).stdout.strip()


def _wait_until_up(url: str, container: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/healthz", timeout=2) as resp:
                if resp.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.5)
    raise AssertionError(f"Forgejo did not answer at {url}: {_docker('logs', container)[-2000:]}")
