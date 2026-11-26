"""koffan stacklet: manifest discovery and env rendering.

Roots the Stack at the real repo (so it discovers the real koffan
stacklet) but points instance_dir/data at tmp_path so generated
secrets never touch the working tree.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB_DIR = REPO_ROOT / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))


@pytest.fixture
def koffan_stack(tmp_path):
    """A Stack rooted at the real repo, with an isolated instance dir."""
    (tmp_path / "stack.toml").write_text(
        '[core]\n'
        'domain = ""\n'
        f'data_dir = "{tmp_path / "data"}"\n'
        'timezone = "Europe/Berlin"\n'
        'language = "en"\n'
        '\n'
        '[ai]\n'
        'default = "test-model"\n'
        'language = "en"\n'
        '\n'
        '[messages]\n'
        'server_name = "testserver"\n'
    )
    (tmp_path / "users.toml").write_text(
        '[[users]]\n'
        'name = "Test Admin"\n'
        'email = "admin@test.local"\n'
        'password = "testpass"\n'
        'role = "admin"\n'
    )
    from stack import Stack
    return Stack(root=REPO_ROOT, data=tmp_path / "data", instance_dir=tmp_path)


def test_manifest_discovered(koffan_stack):
    by_id = {s["id"]: s for s in koffan_stack.discover()}
    assert "koffan" in by_id
    koffan = by_id["koffan"]
    assert koffan["port"] == 42090
    assert koffan["category"] == "productivity"


def test_env_rendering(koffan_stack):
    env = koffan_stack.env("koffan")
    assert env["KOFFAN_DATA_DIR"].endswith("/koffan")
    assert env["DB_PATH"] == "/data/shopping.db"
    assert env["DEFAULT_LANG"] == "en"
    assert env["APP_ENV"] == "development"
    assert env["DISABLE_AUTH"] == "false"
    assert env["TZ"] == "Europe/Berlin"
    # Shared password is auto-generated and non-empty.
    assert env["APP_PASSWORD"]
