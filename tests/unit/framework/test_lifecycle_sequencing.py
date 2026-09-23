"""What a stacklet can rely on when several things happen in one `stack up`.

Two sequencing rules, both about values that are written during a run
and read later in it:

- An OIDC provider comes up before its clients when they are brought up
  together. A client reads its credentials when its env is rendered, at
  the start of its own `up`, and the provider stores them in its own.
  Brought up first, a client would miss them until its next `up`.
- `on_start_ready` sees the env as it is after `on_install_success`.
  A first install writes tokens and seeds in `on_install_success`; a
  hook that reads the env captured at the start of the run misses them.

The CLI runs for real here; only the Docker calls are replaced.
"""

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "lib"))


@pytest.fixture(autouse=True)
def no_docker():
    with patch("stack.docker.ensure_network", return_value=("mocked", None)), \
         patch("stack.docker.compose_up", return_value=(0, "")), \
         patch("stack.docker.compose_stop", return_value=(0, "")), \
         patch("stack.docker.compose_down", return_value=(0, "")):
        yield


def _cli(tmp_path, stacklets: dict[str, str], hooks: dict[str, dict[str, str]] | None = None):
    """A CLI over stacklets given as manifest bodies, with optional hooks."""
    from stack import Stack
    from stack.cli import CLI
    from stack.output import CollectorOutput

    (tmp_path / "stack.toml").write_text('[core]\ntimezone = "Europe/Berlin"\n')
    (tmp_path / ".stack").mkdir(exist_ok=True)
    (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')
    for sid, body in stacklets.items():
        sdir = tmp_path / "stacklets" / sid
        (sdir / "hooks").mkdir(parents=True)
        (sdir / "stacklet.toml").write_text(f'id = "{sid}"\nname = "{sid}"\n{body}')
        for name, code in (hooks or {}).get(sid, {}).items():
            (sdir / "hooks" / f"{name}.py").write_text(textwrap.dedent(code))
    return CLI(Stack(root=tmp_path, data=tmp_path / "data", output=CollectorOutput()))


PROVIDER = "port = 42100\n[oidc_provider]\n"
CLIENT = '[oidc]\ncallbacks = ["{url}/cb"]\n'


class TestProvidersComeFirst:

    def test_a_provider_starts_before_the_clients_brought_up_with_it(self, tmp_path):
        """`zid` sorts after both clients; only the provider rule puts it first."""
        cli = _cli(tmp_path, {"alpha": CLIENT, "beta": "", "zid": PROVIDER})
        result = cli.up_many(["alpha", "beta", "zid"])
        assert result["ok"]
        assert result["started"].index("zid") < result["started"].index("alpha")

    def test_a_client_brought_up_alone_does_not_wait_for_the_provider(self, tmp_path):
        cli = _cli(tmp_path, {"alpha": CLIENT, "zid": PROVIDER})
        assert cli.up_many(["alpha"])["started"] == ["alpha"]

    def test_requires_still_decides_among_the_rest(self, tmp_path):
        cli = _cli(tmp_path, {
            "alpha": CLIENT + 'requires = ["base"]\n',
            "base": "",
            "zid": PROVIDER,
        })
        started = cli.up_many(["alpha", "base", "zid"])["started"]
        assert started.index("base") < started.index("alpha")
        assert started.index("zid") < started.index("alpha")
