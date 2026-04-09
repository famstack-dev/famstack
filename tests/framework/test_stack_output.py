"""Stack output: lifecycle operations report progress through an injected printer.

The Stack class doesn't print directly — it calls methods on an output
object. This decouples framework logic from presentation:
  - TerminalOutput: colors, spinners, live updates
  - CollectorOutput: captures steps for testing and JSON
  - Custom: whatever the caller wants

The output interface:
  output.step(msg)     — progress update: "Settings saved"
  output.warn(msg)     — non-fatal issue: "oMLX not responding"
  output.error(msg)    — fatal: "on_install hook failed"
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))


@pytest.fixture(autouse=True)
def isolated_env():
    yield


def _make_stack(tmp_path, stacklets=None):
    """Create a Stack with a CollectorOutput."""
    from stack import Stack
    from stack.output import CollectorOutput

    (tmp_path / "stack.toml").write_text("""
[core]
timezone = "Europe/Berlin"
""")
    stack_dir = tmp_path / ".stack"
    stack_dir.mkdir(exist_ok=True)
    (stack_dir / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')

    for sid, spec in (stacklets or {}).items():
        sdir = tmp_path / "stacklets" / sid
        hooks_dir = sdir / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)

        manifest = f'id = "{sid}"\nname = "{sid.title()}"\nversion = "0.1.0"\ncategory = "test"\n'
        if spec.get("env_defaults"):
            manifest += "\n[env.defaults]\n"
            for k, v in spec["env_defaults"].items():
                manifest += f'{k} = "{v}"\n'

        (sdir / "stacklet.toml").write_text(manifest)

        for hook_name, content in spec.get("hooks", {}).items():
            (hooks_dir / hook_name).write_text(content)

    output = CollectorOutput()
    stck = Stack(root=tmp_path, data=tmp_path / "data", output=output)
    return stck, output


class TestCollectorOutput:
    """CollectorOutput captures all messages for inspection."""

    def test_captures_steps(self, tmp_path):
        from stack.output import CollectorOutput
        out = CollectorOutput()
        out.step("one")
        out.step("two")
        assert out.steps == ["one", "two"]

    def test_captures_warnings(self, tmp_path):
        from stack.output import CollectorOutput
        out = CollectorOutput()
        out.warn("careful")
        assert out.warnings == ["careful"]

    def test_captures_errors(self, tmp_path):
        from stack.output import CollectorOutput
        out = CollectorOutput()
        out.error("boom")
        assert out.errors == ["boom"]


class TestStackUsesOutput:
    """Stack lifecycle operations report progress through the output object."""

    def test_up_first_run_reports_through_output(self, tmp_path):
        """First run with a hook reports via the output adapter."""
        stck, output = _make_stack(tmp_path, {"myapp": {
            "hooks": {
                "on_install.py": "def run(ctx): ctx.step('hello')\n",
            },
        }})
        stck.up("myapp")
        assert "hello" in output.steps

    def test_up_first_run_reports_install(self, tmp_path):
        stck, output = _make_stack(tmp_path, {"myapp": {
            "hooks": {
                "on_install.py": textwrap.dedent("""
                    def run(ctx):
                        ctx.step('installing stuff')
                """),
            },
        }})
        stck.up("myapp")
        assert "installing stuff" in output.steps

    def test_up_error_reported(self, tmp_path):
        stck, output = _make_stack(tmp_path, {"myapp": {
            "hooks": {
                "on_install.py": textwrap.dedent("""
                    def run(ctx):
                        raise RuntimeError("broken")
                """),
            },
        }})
        result = stck.up("myapp")
        assert "error" in result

    def test_destroy_reports_steps(self, tmp_path):
        stck, output = _make_stack(tmp_path, {"myapp": {}})
        stck.up("myapp")
        output.steps.clear()

        stck.destroy("myapp")
        # Should have reported cleanup actions
        assert len(output.steps) > 0

    def test_down_silent_when_no_hooks(self, tmp_path):
        stck, output = _make_stack(tmp_path, {"myapp": {}})
        stck.up("myapp")
        output.steps.clear()

        stck.down("myapp")
        # No hooks, no steps to report
        assert len(output.steps) == 0


class TestOutputDefault:
    """Stack works without an explicit output — uses a silent default."""

    def test_up_works_without_output(self, tmp_path):
        from stack import Stack
        (tmp_path / "stack.toml").write_text("[core]\ntimezone = 'UTC'\n")
        (tmp_path / ".stack").mkdir(exist_ok=True)
        (tmp_path / ".stack" / "secrets.toml").write_text("")

        sdir = tmp_path / "stacklets" / "bare"
        sdir.mkdir(parents=True)
        (sdir / "stacklet.toml").write_text('id = "bare"\nname = "Bare"\nversion = "0.1.0"\ncategory = "test"\n')

        stck = Stack(root=tmp_path, data=tmp_path / "data")
        result = stck.up("bare")
        assert result["ok"]
