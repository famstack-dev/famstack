"""ctx.shell(): run system commands from Python hooks.

Hooks that need to install packages, start services, or run builds
use ctx.shell() instead of subprocess.run() directly. This gives the
framework control over error handling, output streaming, and env vars.
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))


@pytest.fixture(autouse=True)
def isolated_env():
    yield


def _stacklet_with_hook(tmp_path, hook_content):
    """Create a stacklet with a single on_install.py hook."""
    stacklet_dir = tmp_path / "stacklets" / "myapp"
    hooks_dir = stacklet_dir / "hooks"
    hooks_dir.mkdir(parents=True)
    (stacklet_dir / "stacklet.toml").write_text('id = "myapp"\n')
    (hooks_dir / "on_install.py").write_text(hook_content)
    return stacklet_dir


class TestCtxShell:
    """ctx.shell() runs commands and returns output."""

    def test_runs_command(self, tmp_path):
        from stack.hooks import HookResolver
        marker = tmp_path / "ran"
        stacklet = _stacklet_with_hook(tmp_path, textwrap.dedent(f"""
            def run(ctx):
                ctx.shell('touch {marker}')
        """))
        resolver = HookResolver(stacklet)
        ctx = _make_ctx(tmp_path)
        resolver.run("on_install", ctx)
        assert marker.exists()

    def test_returns_stdout(self, tmp_path):
        from stack.hooks import HookResolver
        result_file = tmp_path / "output"
        stacklet = _stacklet_with_hook(tmp_path, textwrap.dedent(f"""
            def run(ctx):
                output = ctx.shell('echo hello')
                from pathlib import Path
                Path('{result_file}').write_text(output.strip())
        """))
        resolver = HookResolver(stacklet)
        ctx = _make_ctx(tmp_path)
        resolver.run("on_install", ctx)
        assert result_file.read_text() == "hello"

    def test_raises_on_failure(self, tmp_path):
        from stack.hooks import HookResolver
        marker = tmp_path / "caught"
        stacklet = _stacklet_with_hook(tmp_path, textwrap.dedent(f"""
            def run(ctx):
                try:
                    ctx.shell('exit 1')
                except RuntimeError:
                    from pathlib import Path
                    Path('{marker}').write_text('caught')
        """))
        resolver = HookResolver(stacklet)
        ctx = _make_ctx(tmp_path)
        resolver.run("on_install", ctx)
        assert marker.read_text() == "caught"

    def test_inherits_env(self, tmp_path):
        from stack.hooks import HookResolver
        result_file = tmp_path / "tz"
        stacklet = _stacklet_with_hook(tmp_path, textwrap.dedent(f"""
            def run(ctx):
                output = ctx.shell('echo $MY_VAR')
                from pathlib import Path
                Path('{result_file}').write_text(output.strip())
        """))
        resolver = HookResolver(stacklet)
        ctx = _make_ctx(tmp_path, env={"MY_VAR": "hello_from_env"})
        resolver.run("on_install", ctx)
        assert result_file.read_text() == "hello_from_env"


def _make_ctx(tmp_path, env=None):
    """Build a ctx with shell() support."""
    from stack.hooks import build_hook_ctx
    return build_hook_ctx("test", env=env or {}, step_fn=lambda msg: None)
