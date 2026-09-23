"""Paperless login through the stack's OIDC provider.

The docs stacklet is an OIDC client. Without a provider its login is
Paperless's own. Once a provider has registered it, Paperless offers
the provider's login next to the password form.

Paperless uses django-allauth, which serves one callback per provider
entry at `/accounts/oidc/<provider_id>/login/callback/`. The provider
redirects only to callbacks it was given, so every entry in the compose
file needs its callback in the `[oidc]` table.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO / "lib"))

from stack import Stack  # noqa: E402


@pytest.fixture
def stack(tmp_path):
    """The repo's stacklets over a throwaway instance in domain mode."""
    (tmp_path / "stack.toml").write_text(
        '[core]\ndomain = "home.example.family"\n'
        f'extension_dirs = ["{tmp_path / "ext"}"]\n')
    (tmp_path / ".stack").mkdir()
    return Stack(root=REPO, data=tmp_path / "data", instance_dir=tmp_path)


@pytest.fixture
def with_provider(stack, tmp_path):
    """The same instance with an OIDC provider installed as an extension."""
    (tmp_path / "ext" / "idp").mkdir(parents=True)
    (tmp_path / "ext" / "idp" / "stacklet.toml").write_text(
        'id = "idp"\nport = 42100\n[oidc_provider]\n')
    return stack


def _login(env: dict) -> tuple[str, str, str]:
    return env["OIDC_ISSUER"], env["OIDC_CLIENT_ID"], env["OIDC_CLIENT_SECRET"]


def _compose_provider_ids() -> list[str]:
    compose = (REPO / "stacklets" / "docs" / "docker-compose.yml").read_text()
    line = next(row for row in compose.splitlines()
                if "PAPERLESS_SOCIALACCOUNT_PROVIDERS" in row)
    providers = json.loads(line.split(":", 1)[1].strip().strip("'"))
    return [app["provider_id"] for app in providers["openid_connect"]["APPS"]]


def test_without_a_provider_paperless_keeps_its_own_login(stack):
    assert _login(stack.env("docs")) == ("", "", "")


def test_a_registered_provider_turns_the_login_on(with_provider):
    with_provider.store_oidc_client("docs", client_id="c-1", client_secret="s-1")
    assert _login(with_provider.env("docs")) == (
        "http://idp.home.example.family", "c-1", "s-1")


def test_every_paperless_provider_entry_has_a_registered_callback(with_provider):
    """A missing callback fails only at login time, with a provider error
    page, so the two lists are compared here."""
    [docs] = [c for c in with_provider.oidc_clients() if c["stacklet"] == "docs"]
    expected = [f"http://docs.home.example.family/accounts/oidc/{pid}/login/callback/"
                for pid in _compose_provider_ids()]
    assert sorted(docs["callbacks"]) == sorted(expected)


def test_one_provider_entry_keeps_linked_accounts_working():
    """allauth resolves the app by client id during signup, so a second
    entry with the same client id fails that step with a server error.
    Paperless stores the provider_id on each linked account, and existing
    accounts are linked as `pocketid`, so that id stays."""
    assert _compose_provider_ids() == ["pocketid"]
