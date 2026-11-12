"""The agent's first start on a small Mac asks first.

The agent sends the AI long prompts, and on a 16 GB Mac one of them takes
minutes while other services move to swap. So on a Mac with the small
memory profile (`stack.profile`), the first `stack up agent` asks whether
to start it anyway, default no. A no starts nothing and changes nothing.
On a medium or large Mac, and for an agent already set up, nothing is
asked.

The question is the agent's setup form (ADR-021): a terminal asks it, the
dashboard's Add shows it, and `--answer start_anyway=yes` answers it
without either. Driven through `./stack configure agent` and
`./stack up agent` in this process; the profile comes from
`[core] profile`, so the memory of the Mac running the tests decides
nothing.
"""

from __future__ import annotations

import tomllib

import pytest

from .conftest import StackCLI, write_instance

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

FORM = "agent.small-mac"


@pytest.fixture
def mac(tmp_path, monkeypatch, as_stack_runs_it):
    """An instance with messages set up and the agent not yet, on a Mac
    with this memory profile."""
    def make(profile: str) -> StackCLI:
        instance = write_instance(tmp_path, core=f'profile = "{profile}"')
        (instance / ".stack" / "messages.setup-done").touch()
        return StackCLI(instance, monkeypatch)
    return make


def _set_up(cli: StackCLI) -> bool:
    return (cli.instance / ".stack" / "agent.setup-done").exists()


# The one secret a declined start leaves: the agent's token for the host
# API, generated when `stack up` renders the agent's environment.
AGENT_TOKEN_SECRET = "agent__STACK_API_TOKEN"


def _secrets(cli: StackCLI) -> dict:
    path = cli.instance / ".stack" / "secrets.toml"
    return tomllib.loads(path.read_text()) if path.exists() else {}


def _only_the_agents_token_added(before: dict, after: dict) -> bool:
    """`after` is `before` plus the agent's generated token, and nothing else."""
    return (set(after) - set(before) == {AGENT_TOKEN_SECRET}
            and {k: after[k] for k in before} == before and bool(after[AGENT_TOKEN_SECRET]))


# ── On a small Mac ───────────────────────────────────────────────────────

def test_a_small_mac_is_asked_before_the_agent_starts(mac):
    """Given a small Mac, when the agent is configured for its first start,
    then its form asks whether to start it anyway, and no is the default."""
    form = mac("small")("configure", "agent", "--json").answer["form"]

    assert form["id"] == FORM
    assert form["schema"]["properties"]["start_anyway"]["default"] is False
    assert "16 GB" in form["text"]


def test_no_starts_nothing_and_changes_nothing(mac):
    """Given a small Mac, when `stack up agent` is answered no, then the run
    stops before the install: no setup marker, so nothing installed and no
    container started, and stack.toml as it was.

    The secret store gains exactly one key, the agent's host API token:
    `stack up` renders a stacklet's environment, which generates its
    `[env] generate` secrets, before `on_configure` asks, as it does for
    messages. Nothing else may change there."""
    cli = mac("small")
    toml, secrets = cli.toml(), _secrets(cli)

    ran = cli("up", "agent", "--answer", "start_anyway=no")

    assert ran.code == 1
    assert "Cancelled" in ran.err
    assert not _set_up(cli)
    assert cli.toml() == toml
    assert _only_the_agents_token_added(secrets, _secrets(cli))


def test_up_without_a_terminal_or_an_answer_names_the_form(mac):
    """Nobody to ask: the run stops with the open form, as every setup
    form does, and installs nothing."""
    cli = mac("small")

    ran = cli("up", "agent")

    assert ran.code == 1
    assert FORM in ran.err and "start_anyway" in ran.err
    assert not _set_up(cli)


def test_in_a_terminal_no_is_the_default(mac, monkeypatch):
    """Given a terminal, when the question is answered with Enter, then it
    is a no: nothing set up, and the secret store gains only the agent's
    host API token (rendered before the question, as above)."""
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    cli = mac("small")
    secrets = _secrets(cli)

    ran = cli("up", "agent", terminal=True)

    assert ran.code == 1
    assert "Cancelled" in ran.err
    assert not _set_up(cli)
    assert _only_the_agents_token_added(secrets, _secrets(cli))


def test_yes_goes_on_to_start_the_agent(mac):
    ran = mac("small")("configure", "agent", "--json", "--answer", "start_anyway=yes")

    assert ran.answer == {"ready": True}


def test_the_dashboard_offers_the_form_on_add(mac):
    """The dashboard reads `stack list` to choose what Add does: it opens
    the forms for a stacklet whose first start asks in forms."""
    listed = mac("small")("list", "--json").answer

    agent = next(s for s in listed["stacklets"] if s["id"] == "agent")
    assert agent["asks_in_forms"] is True


# ── Where nothing is asked ───────────────────────────────────────────────

@pytest.mark.parametrize("profile", ["medium", "large"])
def test_a_larger_mac_is_not_asked(mac, profile):
    assert mac(profile)("configure", "agent", "--json").answer == {"ready": True}


def test_an_agent_set_up_already_is_not_asked(mac):
    cli = mac("small")
    (cli.instance / ".stack" / "agent.setup-done").touch()

    assert cli("configure", "agent", "--json").answer == {"ready": True}
