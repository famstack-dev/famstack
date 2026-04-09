"""Hooks: lifecycle callbacks that stacklets use to customize behavior.

Hooks live in hooks/. Python (.py) is preferred over shell (.sh).
The framework resolves hooks by name, builds a ctx for Python hooks,
and passes env vars to shell hooks.
"""

from pathlib import Path


class TestHookResolution:
    """The framework finds hooks by name, preferring .py over .sh."""

    def test_python_hook_exists(self, fixtures_dir):
        """on_install.py exists for with_hooks fixture."""
        hook = fixtures_dir / "with_hooks" / "hooks" / "on_install.py"
        assert hook.exists()

    def test_shell_fallback_exists(self, fixtures_dir):
        """on_stop has only .sh for with_hooks fixture."""
        py = fixtures_dir / "with_hooks" / "hooks" / "on_stop.py"
        sh = fixtures_dir / "with_hooks" / "hooks" / "on_stop.sh"
        assert not py.exists(), ".py should not exist for this hook"
        assert sh.exists(), ".sh should be the fallback"

    def test_no_hooks_dir_for_basic_stacklet(self, fixtures_dir):
        """Basic fixture has no hooks/ directory at all."""
        hooks_dir = fixtures_dir / "basic" / "hooks"
        assert not hooks_dir.exists()
