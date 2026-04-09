"""Stack secrets: auto-generated credentials scoped to stacklets.

Secrets are stored in .stack/secrets.toml, namespaced by stacklet ID.
Reading falls back from stacklet-specific to global:
  secret("DB_PASSWORD") checks photos__DB_PASSWORD, then global__DB_PASSWORD.
Writing always goes to the stacklet namespace.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))


@pytest.fixture(autouse=True)
def isolated_env():
    """Override conftest — secrets tests manage their own state."""
    yield


def _make_stack(tmp_path, secrets_content=""):
    """Create a Stack with optional pre-existing secrets."""
    from stack import Stack

    (tmp_path / "stack.toml").write_text("[core]\ntimezone = 'UTC'\n")
    stack_dir = tmp_path / ".stack"
    stack_dir.mkdir(exist_ok=True)
    if secrets_content:
        (stack_dir / "secrets.toml").write_text(secrets_content)

    return Stack(root=tmp_path, data=tmp_path / "data")


class TestSecretRead:
    """Reading secrets: stacklet-specific first, global fallback."""

    def test_reads_stacklet_secret(self, tmp_path):
        s = _make_stack(tmp_path, 'photos__DB_PASSWORD = "abc123"\n')
        assert s.secret("photos", "DB_PASSWORD") == "abc123"

    def test_falls_back_to_global(self, tmp_path):
        """secret("photos", "ADMIN_PASSWORD") finds global__ADMIN_PASSWORD
        when photos__ADMIN_PASSWORD doesn't exist."""
        s = _make_stack(tmp_path, 'global__ADMIN_PASSWORD = "secret"\n')
        assert s.secret("photos", "ADMIN_PASSWORD") == "secret"

    def test_stacklet_specific_wins_over_global(self, tmp_path):
        s = _make_stack(tmp_path,
            'global__TOKEN = "global"\n'
            'photos__TOKEN = "specific"\n'
        )
        assert s.secret("photos", "TOKEN") == "specific"

    def test_returns_none_when_missing(self, tmp_path):
        s = _make_stack(tmp_path)
        assert s.secret("photos", "NONEXISTENT") is None

    def test_works_without_secrets_file(self, tmp_path):
        """No secrets.toml at all — returns None, doesn't crash."""
        from stack import Stack
        (tmp_path / "stack.toml").write_text("[core]\n")
        s = Stack(root=tmp_path, data=tmp_path / "data")
        assert s.secret("photos", "DB_PASSWORD") is None


class TestSecretWrite:
    """Writing secrets: always to the stacklet namespace."""

    def test_writes_to_stacklet_namespace(self, tmp_path):
        s = _make_stack(tmp_path)
        s.set_secret("photos", "API_TOKEN", "tok_123")
        assert s.secret("photos", "API_TOKEN") == "tok_123"

    def test_persists_to_disk(self, tmp_path):
        s = _make_stack(tmp_path)
        s.set_secret("photos", "API_TOKEN", "tok_123")

        # Read back from disk with a fresh Stack instance
        s2 = _make_stack(tmp_path)
        assert s2.secret("photos", "API_TOKEN") == "tok_123"

    def test_preserves_existing_secrets(self, tmp_path):
        s = _make_stack(tmp_path, 'global__ADMIN_PASSWORD = "keep"\n')
        s.set_secret("photos", "NEW_KEY", "new_value")

        content = (tmp_path / ".stack" / "secrets.toml").read_text()
        assert "ADMIN_PASSWORD" in content
        assert "NEW_KEY" in content


class TestSecretGenerate:
    """Generating secrets: random, stable, namespaced."""

    def test_generates_random_value(self, tmp_path):
        s = _make_stack(tmp_path)
        s.ensure_secret("photos", "DB_PASSWORD")
        val = s.secret("photos", "DB_PASSWORD")
        assert val is not None
        assert len(val) >= 16

    def test_idempotent(self, tmp_path):
        """Generating twice doesn't change the value."""
        s = _make_stack(tmp_path)
        s.ensure_secret("photos", "DB_PASSWORD")
        first = s.secret("photos", "DB_PASSWORD")

        s.ensure_secret("photos", "DB_PASSWORD")
        second = s.secret("photos", "DB_PASSWORD")

        assert first == second

    def test_different_keys_get_different_values(self, tmp_path):
        s = _make_stack(tmp_path)
        s.ensure_secret("photos", "DB_PASSWORD")
        s.ensure_secret("photos", "SECRET_KEY")
        assert s.secret("photos", "DB_PASSWORD") != s.secret("photos", "SECRET_KEY")


class TestSecretCleanup:
    """Destroying a stacklet removes its secrets, keeps others."""

    def test_clear_removes_stacklet_secrets(self, tmp_path):
        s = _make_stack(tmp_path,
            'photos__DB_PASSWORD = "abc"\n'
            'photos__SECRET_KEY = "def"\n'
            'global__ADMIN_PASSWORD = "keep"\n'
        )
        s.clear_secrets("photos")

        assert s.secret("photos", "DB_PASSWORD") is None
        assert s.secret("photos", "SECRET_KEY") is None

    def test_clear_preserves_global(self, tmp_path):
        s = _make_stack(tmp_path,
            'photos__DB_PASSWORD = "abc"\n'
            'global__ADMIN_PASSWORD = "keep"\n'
        )
        s.clear_secrets("photos")
        assert s.secret("photos", "ADMIN_PASSWORD") == "keep"

    def test_clear_preserves_other_stacklets(self, tmp_path):
        s = _make_stack(tmp_path,
            'photos__DB_PASSWORD = "abc"\n'
            'docs__DB_PASSWORD = "def"\n'
        )
        s.clear_secrets("photos")

        assert s.secret("photos", "DB_PASSWORD") is None
        assert s.secret("docs", "DB_PASSWORD") == "def"
