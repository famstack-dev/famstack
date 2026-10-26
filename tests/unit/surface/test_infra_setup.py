"""The infra stacklet's setup questions: the network warning, the DNS token.

Infra is DNS and the entry point for the whole home network. Once the
router hands out the Mac as its DNS server, every device depends on it,
and setting it up takes networking knowledge most stacklets do not ask
for. So its first setup shows that warning as a form that needs a yes. With
`[core] dns_provider` set and no token stored, it then asks for the
provider's API token, a secret. These tests run `./stack` in this process.
"""

from __future__ import annotations

import pytest

from .conftest import StackCLI, write_instance

TOKEN = "hcloud-7Q2mK9xV4pL8nR3t"


@pytest.fixture
def infra(tmp_path, monkeypatch, as_stack_runs_it) -> StackCLI:
    """An instance in domain mode, with plain HTTP."""
    instance = write_instance(tmp_path)
    toml = instance / "stack.toml"
    toml.write_text(toml.read_text().replace('domain = ""', 'domain = "home.example.family"'))
    return StackCLI(instance, monkeypatch)


@pytest.fixture
def https(infra) -> StackCLI:
    """The same, with certificates through Hetzner's DNS API."""
    toml = infra.instance / "stack.toml"
    toml.write_text(toml.read_text().replace(
        'domain = "home.example.family"', 'domain = "home.example.family"\ndns_provider = "hetzner"'))
    return infra


def _secrets(stack_cli) -> str:
    return (stack_cli.instance / ".stack" / "secrets.toml").read_text()


def test_the_first_setup_shows_the_network_warning_and_needs_a_yes(infra):
    form = infra("configure", "infra", "--json").answer["form"]

    assert form["id"] == "infra.network"
    assert "every device" in form["text"]
    assert "networking knowledge" in form["text"]
    assert "stacklets/infra/README.md" in form["text"]
    assert form["schema"]["required"] == ["understood"]


def test_without_the_yes_up_names_the_form_and_installs_nothing(infra):
    ran = infra("up", "infra")

    assert ran.code == 1
    assert "infra.network" in ran.err and "understood" in ran.err


def test_a_no_cancels(infra):
    ran = infra("configure", "infra", "--answer", "understood=no")

    assert ran.code == 1
    assert "Cancelled" in ran.err


def test_with_a_dns_provider_the_token_is_asked_next_as_a_secret(https):
    form = https("configure", "infra", "--json", "--answer", "understood=yes").answer["form"]

    assert form["id"] == "infra.dns-token"
    assert "a Hetzner Cloud API token" in form["text"]
    assert form["schema"]["properties"]["dns_token"]["writeOnly"] is True


def test_the_token_is_stored_and_shown_nowhere(https):
    ran = https("configure", "infra", "--json", "--answers", "-",
                stdin=f'{{"understood": true, "dns_token": "{TOKEN}"}}')

    assert ran.answer == {"ready": True}
    assert f'infra__DNS_API_TOKEN = "{TOKEN}"' in _secrets(https)
    assert TOKEN not in ran.out + ran.err + https.toml()


def test_plain_http_asks_for_no_token(infra):
    assert infra("configure", "infra", "--json", "--answer", "understood=yes").answer == {"ready": True}


def test_an_installed_infra_with_its_token_asks_nothing(https):
    (https.instance / ".stack" / "infra.setup-done").write_text("")
    secrets = https.instance / ".stack" / "secrets.toml"
    secrets.write_text(secrets.read_text() + f'infra__DNS_API_TOKEN = "{TOKEN}"\n')

    ran = https("configure", "infra")

    assert ran.code == 0
    assert "Nothing to ask" in ran.out


def test_in_a_terminal_the_warning_reads_as_a_question_and_the_token_is_hidden(
        https, monkeypatch):
    import getpass
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: TOKEN)

    ran = https("configure", "infra", terminal=True)

    assert ran.code == 0, ran.err
    assert "every device" in ran.out
    assert f'infra__DNS_API_TOKEN = "{TOKEN}"' in _secrets(https)
    assert TOKEN not in ran.out + ran.err
