"""Integration test: hooks can read stacklet-scoped and global secrets.

No mocking. Creates a real Stack with real files, writes real secrets,
and verifies the hook context can find them through the actual lookup path.
"""

import tomllib
from pathlib import Path


def _make_stack(tmp_path):
    """Create a minimal but real Stack with a stacklet and secrets."""
    from stack.stack import Stack

    # Minimal stack.toml
    (tmp_path / "stack.toml").write_text('[core]\ndata_dir = "data"\ntimezone = "UTC"\n')

    # Minimal stacklet with a generated secret
    sd = tmp_path / "stacklets" / "testapp"
    sd.mkdir(parents=True)
    (sd / "stacklet.toml").write_text(
        'id = "testapp"\nname = "Test"\ndescription = "test"\n'
        '[env.defaults]\nFOO = "bar"\n'
        '[env]\ngenerate = ["DB_PASSWORD"]\n'
    )

    data = tmp_path / "data"
    data.mkdir()

    return Stack(tmp_path, data)


class TestHookSecretLookup:
    """Verify the full chain: env() → hook ctx → secret() → secrets.toml."""

    def test_stacklet_scoped_secret_found(self, tmp_path):
        """A hook can read a secret generated for its own stacklet."""
        from stack.hooks import build_hook_ctx

        stck = _make_stack(tmp_path)
        stck.secrets.set("testapp", "DB_PASSWORD", "s3cret")

        env_dict = stck.env("testapp")
        ctx = build_hook_ctx("testapp", env=env_dict, step_fn=lambda m: None, stack=stck)

        assert ctx.secret("DB_PASSWORD") == "s3cret"

    def test_global_secret_found(self, tmp_path):
        """A hook can read a global secret via fallback."""
        from stack.hooks import build_hook_ctx

        stck = _make_stack(tmp_path)
        stck.secrets.set("global", "ADMIN_PASSWORD", "admin123")

        env_dict = stck.env("testapp")
        ctx = build_hook_ctx("testapp", env=env_dict, step_fn=lambda m: None, stack=stck)

        assert ctx.secret("ADMIN_PASSWORD") == "admin123"

    def test_stacklet_id_on_context(self, tmp_path):
        """Hook context has stacklet_id as a first-class field, not from env."""
        from stack.hooks import build_hook_ctx

        stck = _make_stack(tmp_path)
        env_dict = stck.env("testapp")
        ctx = build_hook_ctx("testapp", env=env_dict, step_fn=lambda m: None, stack=stck)

        assert ctx.stacklet_id == "testapp"
        assert "stacklet_id" not in env_dict

    def test_missing_secret_returns_none(self, tmp_path):
        """A secret that doesn't exist returns None, not an error."""
        from stack.hooks import build_hook_ctx

        stck = _make_stack(tmp_path)
        env_dict = stck.env("testapp")
        ctx = build_hook_ctx("testapp", env=env_dict, step_fn=lambda m: None, stack=stck)

        assert ctx.secret("NONEXISTENT") is None

    def test_stacklet_secret_wins_over_global(self, tmp_path):
        """Stacklet-scoped secret takes precedence over global."""
        from stack.hooks import build_hook_ctx

        stck = _make_stack(tmp_path)
        stck.secrets.set("global", "DB_PASSWORD", "global_pw")
        stck.secrets.set("testapp", "DB_PASSWORD", "stacklet_pw")

        env_dict = stck.env("testapp")
        ctx = build_hook_ctx("testapp", env=env_dict, step_fn=lambda m: None, stack=stck)

        assert ctx.secret("DB_PASSWORD") == "stacklet_pw"

    def test_secret_write_persists(self, tmp_path):
        """A hook writing a secret can read it back immediately."""
        from stack.hooks import build_hook_ctx

        stck = _make_stack(tmp_path)
        env_dict = stck.env("testapp")
        ctx = build_hook_ctx("testapp", env=env_dict, step_fn=lambda m: None, stack=stck)

        ctx.secret("NEW_TOKEN", "tok_abc123")
        assert ctx.secret("NEW_TOKEN") == "tok_abc123"

        # Verify it's actually on disk
        raw = (tmp_path / ".stack" / "secrets.toml").read_text()
        assert "tok_abc123" in raw

    def test_config_reads_fresh_from_disk(self, tmp_path):
        """stack.toml changes are visible immediately, no stale cache."""
        stck = _make_stack(tmp_path)

        assert stck._cfg("ai", "language") == ""

        # Write a new value to disk
        toml_path = tmp_path / "stack.toml"
        content = toml_path.read_text()
        content += '\n[ai]\nlanguage = "de"\n'
        toml_path.write_text(content)

        # Should see the new value without any reload
        assert stck._cfg("ai", "language") == "de"
