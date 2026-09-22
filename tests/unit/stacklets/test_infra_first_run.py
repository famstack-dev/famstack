"""The first `stack up infra` says what the admin is taking on.

Infra is DNS and the entry point for the whole home network. Once the
router hands out the Mac as its DNS server, every device depends on it,
and setting it up takes networking knowledge most stacklets do not ask
for. That is said once, when the stacklet is first set up, through the
same warning channel the framework uses for a stacklet's stage.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO / "lib"))

from stack import Stack  # noqa: E402
from stack.hooks import StackContext  # noqa: E402
from stack.output import CollectorOutput  # noqa: E402


def _configure(tmp_path) -> CollectorOutput:
    (tmp_path / "stack.toml").write_text(
        '[core]\ndomain = "home.example.family"\n'
        f'extension_dirs = ["{tmp_path / "ext"}"]\n')
    output = CollectorOutput()
    stck = Stack(root=REPO, data=tmp_path / "data", instance_dir=tmp_path, output=output)

    path = REPO / "stacklets" / "infra" / "hooks" / "on_configure.py"
    spec = importlib.util.spec_from_file_location("infra_on_configure", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run(StackContext(stck, "infra", {}))
    return output


def test_setting_up_infra_warns_that_the_whole_network_depends_on_it(tmp_path):
    warnings = " ".join(_configure(tmp_path).warnings)
    assert "every device" in warnings
    assert "networking knowledge" in warnings
    assert "stacklets/infra/README.md" in warnings
