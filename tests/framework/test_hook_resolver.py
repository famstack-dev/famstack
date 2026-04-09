"""HookResolver: finds and executes lifecycle hooks for stacklets.

Hooks are the extension points of the framework. Each lifecycle transition
(install, start, stop, destroy) can trigger stacklet-specific code. The
resolver finds hooks by convention (file name + location), the executor
runs them with the right context.

Resolution rules:
  1. Look in {stacklet}/hooks/{hook_name}.py first (preferred)
  2. Fall back to {stacklet}/hooks/{hook_name}.sh
  3. Return None if neither exists — the framework skips the step

Python hooks get a ctx dict (env, secrets, step, shell, http).
Shell hooks get environment variables.
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))


@pytest.fixture(autouse=True)
def isolated_env():
    """Override conftest — hook tests manage their own state."""
    yield


def _stacklet_with_hooks(tmp_path, hooks: dict[str, str]) -> Path:
    """Create a stacklet directory with the given hook files.

    hooks is a dict of {filename: content}, e.g.:
      {"on_install.py": "def run(ctx): ...", "on_stop.sh": "#!/bin/bash\\n..."}
    """
    stacklet_dir = tmp_path / "stacklets" / "myapp"
    hooks_dir = stacklet_dir / "hooks"
    hooks_dir.mkdir(parents=True)

    (stacklet_dir / "stacklet.toml").write_text(textwrap.dedent("""
        id = "myapp"
        name = "My App"
        version = "0.1.0"
        category = "test"
    """))

    for filename, content in hooks.items():
        path = hooks_dir / filename
        path.write_text(content)
        if filename.endswith(".sh"):
            path.chmod(0o755)

    return stacklet_dir


# ── Resolution ────────────────────────────────────────────────────────────

class TestHookResolution:
    """The resolver finds hooks by name, preferring .py over .sh."""

    def test_finds_python_hook(self, tmp_path):
        from stack import HookResolver
        stacklet = _stacklet_with_hooks(tmp_path, {
            "on_install.py": "def run(ctx): pass\n",
        })
        resolver = HookResolver(stacklet)
        hook = resolver.resolve("on_install")
        assert hook is not None
        assert hook.suffix == ".py"

    def test_finds_shell_hook(self, tmp_path):
        from stack import HookResolver
        stacklet = _stacklet_with_hooks(tmp_path, {
            "on_stop.sh": "#!/bin/bash\nexit 0\n",
        })
        resolver = HookResolver(stacklet)
        hook = resolver.resolve("on_stop")
        assert hook is not None
        assert hook.suffix == ".sh"

    def test_python_preferred_over_shell(self, tmp_path):
        """When both .py and .sh exist, Python wins. This is a deliberate
        design choice: Python hooks get the full ctx interface, shell hooks
        are a compatibility fallback."""
        from stack import HookResolver
        stacklet = _stacklet_with_hooks(tmp_path, {
            "on_install.py": "def run(ctx): pass\n",
            "on_install.sh": "#!/bin/bash\nexit 0\n",
        })
        resolver = HookResolver(stacklet)
        hook = resolver.resolve("on_install")
        assert hook.suffix == ".py"

    def test_returns_none_for_missing_hook(self, tmp_path):
        from stack import HookResolver
        stacklet = _stacklet_with_hooks(tmp_path, {})
        resolver = HookResolver(stacklet)
        assert resolver.resolve("on_install") is None

    def test_returns_none_without_hooks_dir(self, tmp_path):
        """A stacklet with no hooks/ directory at all."""
        from stack import HookResolver
        stacklet_dir = tmp_path / "stacklets" / "bare"
        stacklet_dir.mkdir(parents=True)
        (stacklet_dir / "stacklet.toml").write_text('id = "bare"\n')

        resolver = HookResolver(stacklet_dir)
        assert resolver.resolve("on_install") is None

    def test_lists_available_hooks(self, tmp_path):
        """available() returns the names of all hooks that exist."""
        from stack import HookResolver
        stacklet = _stacklet_with_hooks(tmp_path, {
            "on_install.py": "def run(ctx): pass\n",
            "on_stop.sh": "#!/bin/bash\n",
            "on_destroy.py": "def run(ctx): pass\n",
        })
        resolver = HookResolver(stacklet)
        available = resolver.available()
        assert "on_install" in available
        assert "on_stop" in available
        assert "on_destroy" in available
        assert "on_start" not in available


# ── Python hook execution ─────────────────────────────────────────────────

class TestPythonHookExecution:
    """Python hooks receive a ctx dict and run in the framework's process."""

    def test_hook_receives_env(self, tmp_path):
        """The hook can read environment variables via ctx.env."""
        from stack import HookResolver
        stacklet = _stacklet_with_hooks(tmp_path, {
            "on_install.py": textwrap.dedent("""
                from pathlib import Path
                def run(ctx):
                    Path(ctx.env['MARKER_PATH']).write_text(ctx.env['TZ'])
            """),
        })

        marker = tmp_path / "env_marker"
        env = {"TZ": "Europe/Berlin", "MARKER_PATH": str(marker)}
        ctx = _make_ctx(env)

        resolver = HookResolver(stacklet)
        resolver.run("on_install", ctx)

        assert marker.read_text() == "Europe/Berlin"

    def test_hook_receives_step(self, tmp_path):
        """ctx.step records progress messages."""
        from stack import HookResolver
        stacklet = _stacklet_with_hooks(tmp_path, {
            "on_install.py": textwrap.dedent("""
                def run(ctx):
                    ctx.step('doing the thing')
            """),
        })

        steps = []
        ctx = _make_ctx(step_fn=steps.append)
        resolver = HookResolver(stacklet)
        resolver.run("on_install", ctx)

        assert "doing the thing" in steps

    def test_hook_failure_returns_false(self, tmp_path):
        """A hook that raises returns False so the framework can handle it."""
        from stack import HookResolver
        stacklet = _stacklet_with_hooks(tmp_path, {
            "on_install.py": textwrap.dedent("""
                def run(ctx):
                    raise RuntimeError("broken")
            """),
        })

        ctx = _make_ctx()
        resolver = HookResolver(stacklet)
        result = resolver.run("on_install", ctx)
        assert result is False

    def test_successful_hook_returns_true(self, tmp_path):
        from stack import HookResolver
        stacklet = _stacklet_with_hooks(tmp_path, {
            "on_install.py": "def run(ctx): pass\n",
        })

        ctx = _make_ctx()
        resolver = HookResolver(stacklet)
        result = resolver.run("on_install", ctx)
        assert result is True

    def test_missing_hook_returns_true(self, tmp_path):
        """Running a hook that doesn't exist is a no-op, not an error."""
        from stack import HookResolver
        stacklet = _stacklet_with_hooks(tmp_path, {})
        resolver = HookResolver(stacklet)
        result = resolver.run("on_install", _make_ctx())
        assert result is True


# ── Shell hook execution ──────────────────────────────────────────────────

class TestShellHookExecution:
    """Shell hooks run as subprocesses with env vars."""

    def test_shell_hook_runs(self, tmp_path):
        from stack import HookResolver
        marker = tmp_path / "shell_ran"
        stacklet = _stacklet_with_hooks(tmp_path, {
            "on_stop.sh": f"#!/bin/bash\ntouch {marker}\n",
        })

        ctx = _make_ctx()
        resolver = HookResolver(stacklet)
        result = resolver.run("on_stop", ctx)

        assert result is True
        assert marker.exists()

    def test_shell_hook_receives_env(self, tmp_path):
        from stack import HookResolver
        marker = tmp_path / "tz_value"
        stacklet = _stacklet_with_hooks(tmp_path, {
            "on_stop.sh": f'#!/bin/bash\necho -n "$TZ" > {marker}\n',
        })

        ctx = _make_ctx(env={"TZ": "Asia/Tokyo"})
        resolver = HookResolver(stacklet)
        resolver.run("on_stop", ctx)

        assert marker.read_text() == "Asia/Tokyo"

    def test_shell_failure_returns_false(self, tmp_path):
        from stack import HookResolver
        stacklet = _stacklet_with_hooks(tmp_path, {
            "on_stop.sh": "#!/bin/bash\nexit 1\n",
        })

        ctx = _make_ctx()
        resolver = HookResolver(stacklet)
        result = resolver.run("on_stop", ctx)
        assert result is False


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_ctx(env=None, step_fn=None):
    """Build a minimal StackContext for testing."""
    from stack.hooks import StackContext
    return StackContext(stack=None, stacklet_id="test", env=env or {}, step_fn=step_fn)
