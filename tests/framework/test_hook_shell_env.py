"""Shell hooks receive framework-provided env vars.

Shell hooks (on_install.sh, on_stop.sh, etc.) rely on
`$FAMSTACK_DATA_DIR` and `$FAMSTACK_DOMAIN` being set by the framework
before the script runs. Without these, hooks fall back to hardcoded
defaults (e.g. `$HOME/famstack-data`), which caused a production data
directory to be reused by test runs.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))


@pytest.fixture(autouse=True)
def isolated_env():
    yield


def _shell_hook_writing_env(tmp_path, var_name: str, out_file: Path) -> Path:
    """Build a stacklet with an on_install.sh that writes $VAR to a file."""
    stacklet = tmp_path / "stacklets" / "myapp"
    hooks = stacklet / "hooks"
    hooks.mkdir(parents=True)
    (stacklet / "stacklet.toml").write_text('id = "myapp"\n')
    (hooks / "on_install.sh").write_text(
        f'#!/usr/bin/env bash\necho -n "${{{var_name}}}" > {out_file}\n'
    )
    return stacklet


class TestShellHookEnv:
    """Shell hooks see FAMSTACK_DATA_DIR and FAMSTACK_DOMAIN from the stack."""

    def test_data_dir_injected(self, tmp_path):
        from stack.hooks import HookResolver, build_hook_ctx
        from stack import Stack

        out = tmp_path / "seen_data_dir"
        stacklet = _shell_hook_writing_env(tmp_path, "FAMSTACK_DATA_DIR", out)
        stck = Stack(root=tmp_path, data=tmp_path / "my-data")
        ctx = build_hook_ctx("myapp", env={}, step_fn=lambda m: None, stack=stck)

        HookResolver(stacklet).run("on_install", ctx)

        assert out.read_text() == str(tmp_path / "my-data")

    def test_domain_injected(self, tmp_path):
        from stack.hooks import HookResolver, build_hook_ctx
        from stack import Stack

        (tmp_path / "stack.toml").write_text(
            '[core]\ndomain = "home.example"\n'
        )
        out = tmp_path / "seen_domain"
        stacklet = _shell_hook_writing_env(tmp_path, "FAMSTACK_DOMAIN", out)
        stck = Stack(root=tmp_path, data=tmp_path / "data")
        ctx = build_hook_ctx("myapp", env={}, step_fn=lambda m: None, stack=stck)

        HookResolver(stacklet).run("on_install", ctx)

        assert out.read_text() == "home.example"

    def test_stacklet_env_still_overrides(self, tmp_path):
        """A shell hook's stacklet-declared env vars must win over framework
        defaults — stacklets sometimes need to override paths for dev."""
        from stack.hooks import HookResolver, build_hook_ctx
        from stack import Stack

        out = tmp_path / "seen"
        stacklet = _shell_hook_writing_env(tmp_path, "FAMSTACK_DATA_DIR", out)
        stck = Stack(root=tmp_path, data=tmp_path / "framework-default")
        ctx = build_hook_ctx(
            "myapp",
            env={"FAMSTACK_DATA_DIR": str(tmp_path / "stacklet-override")},
            step_fn=lambda m: None, stack=stck,
        )

        HookResolver(stacklet).run("on_install", ctx)

        assert out.read_text() == str(tmp_path / "stacklet-override")
