"""Installing oMLX: the Homebrew steps `stack up ai` runs on first boot.

Homebrew 6 refuses to load a formula from a tap outside its own
repositories until that tap is trusted. famstack publishes oMLX through
one, so without an explicit trust step `brew install` exits non-zero,
`on_install` fails, and the AI stacklet cannot be set up at all -- on
any machine with a current Homebrew, for every new user.

These pin the shape of that install: what runs, in what order, and that
an older Homebrew with no trust gate still reaches the install.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "ai" / "hooks"))

from on_install import OMLX_TAP, _install_omlx_formula  # noqa: E402


class FakeCtx:
    """Records the shell commands a hook issues.

    `no_trust_subcommand` reproduces a pre-6 Homebrew, where `brew trust`
    does not exist and ctx.shell raises.
    """

    def __init__(self, *, no_trust_subcommand: bool = False):
        self.commands: list[str] = []
        self.steps: list[str] = []
        self._no_trust = no_trust_subcommand

    def step(self, msg):
        self.steps.append(msg)

    def shell(self, cmd):
        self.commands.append(cmd)
        if self._no_trust and cmd.startswith("brew trust"):
            raise RuntimeError("Unknown command: trust")
        return ""

    def shell_live(self, cmd):
        self.commands.append(cmd)
        return ""


class TestOmlxInstallSteps:

    def test_the_tap_is_trusted_before_the_install_runs(self):
        """The whole point: an untrusted tap makes `brew install` fail."""
        ctx = FakeCtx()
        _install_omlx_formula(ctx)

        joined = " | ".join(ctx.commands)
        assert f"brew trust {OMLX_TAP}" in joined, "the trust step must run"

        trust_at = next(i for i, c in enumerate(ctx.commands)
                        if c.startswith("brew trust"))
        install_at = next(i for i, c in enumerate(ctx.commands)
                          if c.startswith("brew install omlx"))
        assert trust_at < install_at, "trusting after installing is too late"

    def test_the_tap_is_added_before_it_is_trusted(self):
        ctx = FakeCtx()
        _install_omlx_formula(ctx)

        tap_at = next(i for i, c in enumerate(ctx.commands)
                      if c.startswith("brew tap"))
        trust_at = next(i for i, c in enumerate(ctx.commands)
                        if c.startswith("brew trust"))
        assert tap_at < trust_at

    def test_grammar_is_requested_so_json_mode_works(self):
        # Structured classification needs xgrammar; without the flag the
        # formula installs a build whose JSON mode is broken.
        ctx = FakeCtx()
        _install_omlx_formula(ctx)
        assert any(c == "brew install omlx --with-grammar" for c in ctx.commands)

    def test_steps_are_separate_commands_not_one_chained_string(self):
        """Chained with `&&`, any failure reported the whole string, so a
        trust refusal read as a tap failure. Separate commands make the
        failing step the one that gets named."""
        ctx = FakeCtx()
        _install_omlx_formula(ctx)
        assert not any("&&" in c for c in ctx.commands)

    def test_a_homebrew_without_a_trust_gate_still_installs(self):
        """`brew trust` arrived in Homebrew 6. On older versions there is
        nothing to clear, so its absence is success, not a failed install."""
        ctx = FakeCtx(no_trust_subcommand=True)
        _install_omlx_formula(ctx)  # must not raise
        assert any(c.startswith("brew install omlx") for c in ctx.commands)
