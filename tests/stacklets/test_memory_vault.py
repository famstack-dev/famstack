"""Vault sync + read tests for the memory stacklet.

These exercise the real `git` binary against a local bare repo that
acts as the Forgejo stand-in — no network, no auth, no httpserver.
Validates clone-if-missing, fast-forward pulls, ontology reads from a
working copy, and the get_ontology → seed fallback chain.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "stacklets" / "memory"))

from lib import (  # noqa: E402
    authenticated_remote,
    ensure_vault_cloned,
    get_ontology,
    load_ontology_from_vault,
    load_seed_ontology,
    pull_vault,
    refresh_vault_if_stale,
    vault_local_head,
    vault_path_for,
    vault_remote_head,
    vault_remote_url,
)


# ─── git fixtures ────────────────────────────────────────────────────────

def _run(*args, cwd=None):
    """Run a git command, capturing output for clearer failures."""
    return subprocess.run(
        list(args), cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, check=True,
    )


@pytest.fixture
def upstream(tmp_path):
    """A bare repo that plays the role Forgejo plays in production.

    Yields the path to the bare repo. Tests clone *from* this path
    using `ensure_vault_cloned` and push *to* it from a side working
    copy when they need to simulate edits made in Forgejo.
    """
    bare = tmp_path / "upstream.git"
    _run("git", "init", "--bare", "--initial-branch=main", str(bare))
    return bare


@pytest.fixture
def seeded_upstream(tmp_path, upstream):
    """An `upstream` with an initial `ontology.toml` already pushed."""
    side = tmp_path / "side-workdir"
    _run("git", "clone", str(upstream), str(side))
    _run("git", "config", "user.email", "test@test", cwd=side)
    _run("git", "config", "user.name", "Test", cwd=side)

    (side / "ontology.toml").write_text(
        "[topic.simracing]\n"
        "names = { en = 'Sim Racing' }\n"
    )
    _run("git", "add", ".", cwd=side)
    _run("git", "commit", "-m", "seed", cwd=side)
    _run("git", "push", "origin", "main", cwd=side)
    return upstream


def _push_change(upstream_repo, tmp_path, name, content):
    """Make a commit on `upstream` from a fresh side checkout."""
    side = tmp_path / f"side-{name}"
    _run("git", "clone", str(upstream_repo), str(side))
    _run("git", "config", "user.email", "test@test", cwd=side)
    _run("git", "config", "user.name", "Test", cwd=side)
    (side / name).write_text(content)
    _run("git", "add", ".", cwd=side)
    _run("git", "commit", "-m", f"add {name}", cwd=side)
    _run("git", "push", "origin", "main", cwd=side)


# ─── Path + URL helpers ──────────────────────────────────────────────────

class TestPathHelpers:

    def test_vault_path_for_appends_memory_vault(self, tmp_path):
        assert vault_path_for(tmp_path) == tmp_path / "memory" / "vault"

    def test_vault_remote_url_builds_clone_url(self):
        url = vault_remote_url("http://stack-code:3000")
        assert url == "http://stack-code:3000/family/memory.git"

    def test_vault_remote_url_strips_trailing_slash(self):
        url = vault_remote_url("http://stack-code:3000/")
        assert url == "http://stack-code:3000/family/memory.git"

    def test_authenticated_remote_injects_credentials(self):
        result = authenticated_remote(
            "http://stack-code:3000/family/memory.git",
            "memory-bot", "abc123",
        )
        assert result == "http://memory-bot:abc123@stack-code:3000/family/memory.git"

    def test_authenticated_remote_leaves_pathless_string_alone(self):
        # Not a real URL — no clobber.
        assert authenticated_remote("not a url", "u", "t") == "not a url"


# ─── ensure_vault_cloned ─────────────────────────────────────────────────

class TestEnsureVaultCloned:

    def test_clones_from_upstream_when_missing(self, tmp_path, seeded_upstream):
        vault = tmp_path / "memory" / "vault"

        ok = ensure_vault_cloned(vault, str(seeded_upstream))

        assert ok is True
        assert (vault / ".git").is_dir()
        assert (vault / "ontology.toml").exists()

    def test_idempotent_when_already_cloned(self, tmp_path, seeded_upstream):
        vault = tmp_path / "memory" / "vault"
        ensure_vault_cloned(vault, str(seeded_upstream))

        ok = ensure_vault_cloned(vault, str(seeded_upstream))

        assert ok is True

    def test_returns_false_on_bad_remote(self, tmp_path):
        vault = tmp_path / "memory" / "vault"
        ok = ensure_vault_cloned(vault, "/nonexistent/repo", timeout=5)
        assert ok is False
        assert not (vault / ".git").exists()


# ─── pull_vault ──────────────────────────────────────────────────────────

class TestPullVault:

    def test_fast_forwards_after_upstream_change(self, tmp_path, seeded_upstream):
        vault = tmp_path / "memory" / "vault"
        ensure_vault_cloned(vault, str(seeded_upstream))

        # Make a change to upstream from a side workdir.
        _push_change(
            seeded_upstream, tmp_path, "newfile.toml", "# fresh from upstream\n",
        )

        ok = pull_vault(vault)

        assert ok is True
        assert (vault / "newfile.toml").exists()

    def test_returns_false_when_vault_missing(self, tmp_path):
        ok = pull_vault(tmp_path / "nope")
        assert ok is False

    def test_returns_false_when_remote_unreachable(self, tmp_path, seeded_upstream):
        vault = tmp_path / "memory" / "vault"
        ensure_vault_cloned(vault, str(seeded_upstream))

        # Point the remote at a dead path — pull should fail cleanly.
        _run("git", "-C", str(vault), "remote", "set-url", "origin",
             "/nonexistent/repo")

        ok = pull_vault(vault, timeout=5)
        assert ok is False


# ─── load_ontology_from_vault ────────────────────────────────────────────

class TestLoadOntologyFromVault:

    def test_reads_ontology_toml_from_vault_root(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "ontology.toml").write_text(
            "[topic.cycling]\nnames = { en = 'Cycling' }\n"
        )

        ont = load_ontology_from_vault(vault)

        assert ont is not None
        assert "cycling" in ont.topics

    def test_returns_none_when_file_missing(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        assert load_ontology_from_vault(vault) is None

    def test_returns_none_when_vault_missing(self, tmp_path):
        assert load_ontology_from_vault(tmp_path / "nope") is None


# ─── get_ontology (policy: vault, fall back to seed) ─────────────────────

class TestGetOntology:

    def test_returns_seed_when_no_vault_path_passed(self):
        ont = get_ontology(vault_path=None)
        seed = load_seed_ontology()
        # Seed mirrors the family ontology shipped with this release.
        assert set(ont.topics) == set(seed.topics)
        assert "insurance" in ont.topics

    def test_returns_vault_ontology_when_file_present(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "ontology.toml").write_text(
            "[topic.cycling]\nnames = { en = 'Cycling' }\n"
        )

        ont = get_ontology(vault)

        # Vault wins — seed-only topics are absent.
        assert "cycling" in ont.topics
        assert "insurance" not in ont.topics

    def test_falls_back_to_seed_when_vault_has_no_ontology(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        # No ontology.toml in the vault.

        ont = get_ontology(vault)

        # Seed reappears.
        assert "insurance" in ont.topics

    def test_falls_back_to_seed_when_vault_directory_missing(self, tmp_path):
        ont = get_ontology(tmp_path / "nope")
        assert "insurance" in ont.topics


# ─── Refresh helper ──────────────────────────────────────────────────────

class TestRefreshVault:
    """`refresh_vault_if_stale` is the cheap rev-check guard that read
    commands (e.g. `stack memory search`) call before walking the vault.

    The contract: one `git ls-remote` round-trip per call, full pull
    only when the remote HEAD actually moved. Best-effort across all
    failure modes — never raises.
    """

    def test_returns_up_to_date_when_heads_match(self, tmp_path, seeded_upstream):
        vault = tmp_path / "vault"
        ensure_vault_cloned(vault, str(seeded_upstream))

        head_before = _run("git", "-C", str(vault), "rev-parse", "HEAD").stdout.strip()
        status = refresh_vault_if_stale(vault, timeout=10)
        head_after = _run("git", "-C", str(vault), "rev-parse", "HEAD").stdout.strip()

        assert status == "up_to_date"
        # No pull ran — HEAD unchanged. (Pull would have been a no-op
        # anyway, but skipping it is the whole point of the check.)
        assert head_before == head_after

    def test_pulls_when_remote_has_a_new_commit(
        self, tmp_path, seeded_upstream,
    ):
        vault = tmp_path / "vault"
        ensure_vault_cloned(vault, str(seeded_upstream))

        head_before = _run("git", "-C", str(vault), "rev-parse", "HEAD").stdout.strip()
        _push_change(seeded_upstream, tmp_path, "fresh-note.md", "hello")

        status = refresh_vault_if_stale(vault, timeout=10)
        head_after = _run("git", "-C", str(vault), "rev-parse", "HEAD").stdout.strip()

        assert status == "pulled"
        assert head_before != head_after
        assert (vault / "fresh-note.md").read_text() == "hello"

    def test_unreachable_when_no_git_dir(self, tmp_path):
        """A non-git directory (used by `--vault` overrides and some
        tests) should bow out cleanly with "unreachable" — no crash,
        no spurious pull attempt."""
        vault = tmp_path / "filesystem-only-vault"
        vault.mkdir()
        (vault / "stray-note.md").write_text("hello")
        assert refresh_vault_if_stale(vault, timeout=5) == "unreachable"

    def test_unreachable_when_remote_not_configured(self, tmp_path):
        """A git repo without an `origin` remote — same outcome as a
        plain directory: nothing to refresh against, helper exits
        cleanly."""
        vault = tmp_path / "no-remote-vault"
        _run("git", "init", "--initial-branch=main", str(vault))
        assert refresh_vault_if_stale(vault, timeout=5) == "unreachable"

    def test_vault_remote_head_returns_none_on_no_git(self, tmp_path):
        """The probe helper individually — same defensive shape."""
        vault = tmp_path / "no-git"
        vault.mkdir()
        assert vault_remote_head(vault) is None

    def test_vault_local_head_returns_none_on_no_git(self, tmp_path):
        vault = tmp_path / "no-git"
        vault.mkdir()
        assert vault_local_head(vault) is None
