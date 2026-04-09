"""SecretStore: encapsulated secret storage with swappable backend.

v1 stores secrets in .stack/secrets.toml — simple, works everywhere.
v2 could use macOS Keychain, HashiCorp Vault, or any other backend
without changing the interface.

The interface:
  store.get(stacklet_id, name) → value or None
  store.set(stacklet_id, name, value)
  store.ensure(stacklet_id, name) → value (generates if missing)
  store.clear(stacklet_id) → removes all secrets for a stacklet
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))


@pytest.fixture(autouse=True)
def isolated_env():
    """Override conftest — these tests manage their own state."""
    yield


def _make_store(tmp_path, content=""):
    """Create a TomlSecretStore backed by a temp directory."""
    from stack import TomlSecretStore
    stack_dir = tmp_path / ".stack"
    stack_dir.mkdir(exist_ok=True)
    if content:
        (stack_dir / "secrets.toml").write_text(content)
    return TomlSecretStore(stack_dir / "secrets.toml")


class TestGet:
    """Reading: stacklet-specific first, global fallback."""

    def test_reads_stacklet_secret(self, tmp_path):
        store = _make_store(tmp_path, 'photos__DB_PASSWORD = "abc"\n')
        assert store.get("photos", "DB_PASSWORD") == "abc"

    def test_global_fallback(self, tmp_path):
        store = _make_store(tmp_path, 'global__ADMIN_PASSWORD = "pass"\n')
        assert store.get("photos", "ADMIN_PASSWORD") == "pass"

    def test_stacklet_wins_over_global(self, tmp_path):
        store = _make_store(tmp_path,
            'global__TOKEN = "g"\nphotos__TOKEN = "s"\n')
        assert store.get("photos", "TOKEN") == "s"

    def test_none_when_missing(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.get("photos", "NOPE") is None

    def test_no_file(self, tmp_path):
        """No secrets.toml at all — returns None, doesn't crash."""
        from stack import TomlSecretStore
        store = TomlSecretStore(tmp_path / "nonexistent" / "secrets.toml")
        assert store.get("photos", "X") is None


class TestSet:
    """Writing: always to stacklet namespace, persists to disk."""

    def test_set_and_read_back(self, tmp_path):
        store = _make_store(tmp_path)
        store.set("photos", "TOKEN", "abc")
        assert store.get("photos", "TOKEN") == "abc"

    def test_persists_across_instances(self, tmp_path):
        store = _make_store(tmp_path)
        store.set("photos", "TOKEN", "abc")

        store2 = _make_store(tmp_path)
        assert store2.get("photos", "TOKEN") == "abc"

    def test_preserves_existing(self, tmp_path):
        store = _make_store(tmp_path, 'global__KEEP = "yes"\n')
        store.set("photos", "NEW", "val")
        assert store.get("photos", "KEEP") == "yes"


class TestEnsure:
    """Generate if missing, idempotent."""

    def test_generates_value(self, tmp_path):
        store = _make_store(tmp_path)
        val = store.ensure("photos", "DB_PASSWORD")
        assert val is not None and len(val) >= 16

    def test_idempotent(self, tmp_path):
        store = _make_store(tmp_path)
        first = store.ensure("photos", "DB_PASSWORD")
        second = store.ensure("photos", "DB_PASSWORD")
        assert first == second

    def test_unique_per_key(self, tmp_path):
        store = _make_store(tmp_path)
        a = store.ensure("photos", "A")
        b = store.ensure("photos", "B")
        assert a != b


class TestClear:
    """Remove stacklet secrets, preserve global and other stacklets."""

    def test_removes_stacklet(self, tmp_path):
        store = _make_store(tmp_path, 'photos__A = "x"\nphotos__B = "y"\n')
        store.clear("photos")
        assert store.get("photos", "A") is None
        assert store.get("photos", "B") is None

    def test_preserves_global(self, tmp_path):
        store = _make_store(tmp_path,
            'photos__A = "x"\nglobal__ADMIN = "keep"\n')
        store.clear("photos")
        assert store.get("photos", "ADMIN") == "keep"

    def test_preserves_other_stacklets(self, tmp_path):
        store = _make_store(tmp_path,
            'photos__A = "x"\ndocs__A = "y"\n')
        store.clear("photos")
        assert store.get("docs", "A") == "y"
