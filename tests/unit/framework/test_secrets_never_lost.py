"""The stack's secrets survive a secrets file that cannot be read.

`.stack/secrets.toml` holds every generated password, token and key of an
instance. The store reads the whole file, changes one key and writes the
whole file back. So a file that cannot be read must never count as "no
secrets": the next write would save only the one new key, and every other
secret would be gone.

These tests pin what a caller of the store observes:

- a write over a broken file refuses, names the file and changes nothing;
- a read of a broken file fails with the same message;
- a write is one rename of a file that has mode 0600 from its first byte,
  so a write stopped halfway leaves the old file as it was;
- a missing file is the normal start: the first write creates it;
- `./stack` turns the refusal into one plain message and exit code 1.

Every instance here is a temporary folder with made-up secrets.
"""

from __future__ import annotations

import os
import stat

import pytest

from stack.secrets import SecretsUnreadable, TomlSecretStore
from tests.springfield import write_instance

# Two keys and a line that does not parse: what a hand edit can leave behind.
BROKEN = (
    '# stack secrets\n'
    'global__ADMIN_PASSWORD = "donuts"\n'
    'photos__DB_PASSWORD = "duff-beer"\n'
    'docs__API_TOKEN = "half a line\n'
)


@pytest.fixture
def broken(tmp_path):
    """A store over a secrets file that does not parse."""
    path = tmp_path / ".stack" / "secrets.toml"
    path.parent.mkdir()
    path.write_text(BROKEN)
    return TomlSecretStore(path)


def _file(store: TomlSecretStore) -> str:
    return store.path.read_text()


# ── A broken file refuses every write ───────────────────────────────────

def test_a_write_keeps_the_other_secrets_of_a_broken_file(broken):
    with pytest.raises(SecretsUnreadable):
        broken.set("messages", "BOT_PASSWORD", "new")
    assert 'global__ADMIN_PASSWORD = "donuts"' in _file(broken)
    assert 'photos__DB_PASSWORD = "duff-beer"' in _file(broken)


@pytest.mark.parametrize("write", [
    lambda s: s.set("messages", "BOT_PASSWORD", "new"),
    lambda s: s.ensure("messages", "BOT_PASSWORD"),
    lambda s: s.clear("photos"),
], ids=["set", "ensure", "clear"])
def test_no_write_changes_a_broken_file(broken, write):
    with pytest.raises(SecretsUnreadable):
        write(broken)
    assert _file(broken) == BROKEN


def test_the_refusal_names_the_file_and_the_fix(broken):
    with pytest.raises(SecretsUnreadable) as refused:
        broken.set("messages", "BOT_PASSWORD", "new")
    message = str(refused.value)
    assert message.startswith(
        f"The stack's secrets file `{broken.path}` cannot be read: ")
    assert message.endswith(
        "Nothing was changed. Fix or restore the file, then run the command again.")


# ── A broken file fails every read ──────────────────────────────────────

@pytest.mark.parametrize("read", [
    lambda s: s.get("photos", "DB_PASSWORD"),
    lambda s: s.get("messages", "NOT_THERE"),
    lambda s: s.all(),
], ids=["get", "get-missing", "all"])
def test_a_read_of_a_broken_file_fails_instead_of_finding_none(broken, read):
    with pytest.raises(SecretsUnreadable):
        read(broken)


def test_a_file_that_cannot_be_opened_is_unreadable_too(tmp_path):
    # A folder where the file should be: open() fails with an OSError.
    path = tmp_path / ".stack" / "secrets.toml"
    path.mkdir(parents=True)
    store = TomlSecretStore(path)
    with pytest.raises(SecretsUnreadable):
        store.set("messages", "BOT_PASSWORD", "new")
    assert path.is_dir()


# ── A write is one rename of a private file ─────────────────────────────

def test_a_write_replaces_the_file_in_one_rename(tmp_path, monkeypatch):
    store = TomlSecretStore(tmp_path / ".stack" / "secrets.toml")
    store.set("global", "ADMIN_PASSWORD", "donuts")
    seen = []
    real_replace = os.replace

    def watching(src, dst):
        # The new file is complete and private before it takes the name.
        assert stat.S_IMODE(os.stat(src).st_mode) == 0o600
        assert 'photos__DB_PASSWORD = "duff-beer"' in open(src).read()
        seen.append((os.path.dirname(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", watching)
    store.set("photos", "DB_PASSWORD", "duff-beer")

    assert seen == [(str(store.path.parent), str(store.path))]
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert sorted(p.name for p in store.path.parent.iterdir()) == ["secrets.toml"]


def test_a_write_stopped_before_the_rename_leaves_the_old_file(tmp_path, monkeypatch):
    store = TomlSecretStore(tmp_path / ".stack" / "secrets.toml")
    store.set("global", "ADMIN_PASSWORD", "donuts")
    before = _file(store)

    def crash(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        store.set("photos", "DB_PASSWORD", "duff-beer")

    assert _file(store) == before
    assert sorted(p.name for p in store.path.parent.iterdir()) == ["secrets.toml"]


# ── The normal cases stay as they were ──────────────────────────────────

def test_the_first_write_creates_the_file(tmp_path):
    store = TomlSecretStore(tmp_path / ".stack" / "secrets.toml")
    assert store.all() == {}
    store.set("photos", "DB_PASSWORD", "duff-beer")
    assert store.get("photos", "DB_PASSWORD") == "duff-beer"
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_a_readable_file_keeps_its_format_and_keys(tmp_path):
    store = TomlSecretStore(tmp_path / ".stack" / "secrets.toml")
    store.set("global", "ADMIN_PASSWORD", 'don"uts\\')
    store.set("photos", "DB_PASSWORD", "duff-beer")
    assert _file(store) == (
        "# stack secrets — auto-generated, do not commit\n"
        "\n"
        'global__ADMIN_PASSWORD = "don\\"uts\\\\"\n'
        'photos__DB_PASSWORD = "duff-beer"\n'
    )
    assert store.get("photos", "ADMIN_PASSWORD") == 'don"uts\\'


# ── ./stack says it in one message ──────────────────────────────────────

def test_stack_stops_with_the_message_when_the_file_is_broken(tmp_path, monkeypatch, capsys):
    from stack import cli

    instance = write_instance(tmp_path)
    secrets = instance / ".stack" / "secrets.toml"
    secrets.write_text(BROKEN)
    monkeypatch.setenv("STACK_DIR", str(instance))
    monkeypatch.setattr("sys.argv", ["stack", "config", "admin"])

    with pytest.raises(SystemExit) as stopped:
        cli.main()

    assert stopped.value.code == 1
    err = capsys.readouterr().err
    assert f"The stack's secrets file `{secrets}` cannot be read" in err
    assert "Traceback" not in err
    assert secrets.read_text() == BROKEN
