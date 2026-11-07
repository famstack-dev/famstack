"""A new memory's first commit is its seed, on a real Forgejo.

The curator seeds a memory with no commit, so what Forgejo puts in a new
repository decides whether a new instance's memory gets its seed. Only
Forgejo can answer that, and a bare repository in the unit lane cannot: it
has no first commit to add. So the memory is created here by
`ensure_memory_repo`, as `stack up memory` creates it, and the curator's
`sync_now` runs as the install's hook asks for it. The checks read what
Forgejo holds, through a clone of its own.

    uv run --extra test pytest tests/integration/test_new_memory_seed.py

Each test starts its own Forgejo, because each needs an instance with no
memory yet. That costs about 5 seconds a test. The containers have their own
names and a port Docker picks, so the rig's `stack-code` is never touched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from stack.forgejo import ForgejoClient
from stack.stacklet_code import import_stacklets
from tests.integration.forgejo_container import ADMIN, PASSWORD, needs_docker, running_forgejo

REPO_ROOT = Path(__file__).parent.parent.parent
import_stacklets([REPO_ROOT / "stacklets"])

from stacklets.memory.bot.curator import Vault  # noqa: E402
from stacklets.memory.cards import Put  # noqa: E402
from stacklets.memory.lib import (  # noqa: E402
    REPO_NAME,
    REPO_OWNER,
    authenticated_remote,
    ensure_memory_repo,
    vault_remote_url,
)

pytestmark = needs_docker

# The shipped seed as the memory holds it, in the default household bucket.
SEED = ["family/correspondents/README.md", ".gitignore", "README.md", "facts.toml", "index.md",
        "ontology.toml"]


@pytest.fixture
def forgejo(monkeypatch):
    """A Forgejo with no memory, and the curator's environment pointing at it."""
    with running_forgejo("stack-test-new-memory-seed-forgejo") as running:
        monkeypatch.setenv("CODE_URL", running.url)
        monkeypatch.setenv("MATRIX_ADMIN_USER", ADMIN)
        monkeypatch.setenv("MATRIX_ADMIN_PASSWORD", PASSWORD)
        yield ForgejoClient(url=running.url, admin_user=ADMIN, admin_password=PASSWORD)


class Held:
    """What Forgejo's memory repository holds, read through a new clone."""

    def __init__(self, forgejo: ForgejoClient, folder: Path):
        remote = authenticated_remote(vault_remote_url(forgejo.url), ADMIN, PASSWORD)
        subprocess.run(["git", "clone", "--quiet", remote, str(folder)], check=True,
                       capture_output=True, text=True)
        self.folder = folder

    def _git(self, *args: str) -> list[str]:
        return subprocess.run(["git", "-C", str(self.folder), *args], check=True,
                              capture_output=True, text=True).stdout.splitlines()

    def authors_newest_first(self) -> list[str]:
        return self._git("log", "--format=%an", "origin/main")

    def commits(self) -> list[str]:
        return self._git("rev-list", "origin/main")

    def files(self) -> list[str]:
        return self._git("ls-tree", "-r", "--name-only", "origin/main")


def test_a_new_memorys_first_commit_is_its_seed(forgejo, tmp_path):
    """Given a new instance, when the install creates the memory and the
    curator syncs, then Forgejo holds the shipped seed as the memory's one
    commit, by the memory bot. Without it a new instance has no ontology.toml
    to edit, no facts.toml and no start page for the wiki."""
    created = ensure_memory_repo(forgejo)
    vault = Vault(tmp_path / "vault")

    synced = vault.sync_now()

    assert created["created_repo"] is True
    assert synced.state == "ok", synced.detail
    held = Held(forgejo, tmp_path / "held")
    assert held.authors_newest_first() == ["memory-bot"]
    assert sorted(held.files()) == sorted(SEED)


def test_what_is_written_next_lands_on_top_of_the_seed(forgejo, tmp_path):
    """Given a seeded memory, when a change is written and the curator syncs
    again, then Forgejo holds that change on top of the seed and no second
    seed. The person files and the migrations reach the memory this way."""
    ensure_memory_repo(forgejo)
    vault = Vault(tmp_path / "vault")
    vault.sync_now()

    vault.store.commit([Put("family/people/lisa.md", "# Lisa\n", base="")],
                       by="memory-bot", kind="cards", title="person file")
    synced = vault.sync_now()

    assert synced.state == "ok", synced.detail
    held = Held(forgejo, tmp_path / "held")
    assert len(held.commits()) == 2
    assert sorted(held.files()) == sorted([*SEED, "family/people/lisa.md"])


def test_a_memory_with_commits_gets_no_seed_and_no_commit(forgejo, tmp_path):
    """Given a memory an earlier install made, which has Forgejo's first
    commit, when the install runs again and the curator syncs, then nothing
    changes in Forgejo. A family's memory is never written just for being there."""
    forgejo.create_org(REPO_OWNER)
    forgejo.create_repo(REPO_OWNER, REPO_NAME, owner_is_org=True)
    before = Held(forgejo, tmp_path / "before")

    created = ensure_memory_repo(forgejo)
    synced = Vault(tmp_path / "vault").sync_now()

    assert created == {"created_org": False, "created_repo": False}
    assert synced.state == "ok", synced.detail
    after = Held(forgejo, tmp_path / "after")
    assert after.commits() == before.commits()
    assert after.files() == before.files()
