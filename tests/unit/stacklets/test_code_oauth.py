"""Forgejo login through the stack's OIDC provider.

The code stacklet is an OIDC client. Forgejo keeps login sources in its
database, so the stacklet manages one source, "family account", through
Forgejo's admin CLI (`forgejo admin auth list | add-oauth | update-oauth`).
The source name is what the login button says ("Sign in with family
account") and part of the callback path Forgejo sends to the provider.

The CLI never shows a source's stored credentials, so the stacklet keeps
a fingerprint of what it applied and updates the source only when that
changes. The stand-in below behaves like those three commands, with the
flags Forgejo 14 documents in `--help`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO / "lib"))

from stack import Stack  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "code_oauth", REPO / "stacklets" / "code" / "oauth.py")
oauth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oauth)


# ── The manifest side ────────────────────────────────────────────────────

@pytest.fixture
def instance(tmp_path):
    (tmp_path / "stack.toml").write_text(
        '[core]\ndomain = "home.example.family"\n'
        f'extension_dirs = ["{tmp_path / "ext"}"]\n')
    (tmp_path / ".stack").mkdir()
    (tmp_path / "ext" / "idp").mkdir(parents=True)
    (tmp_path / "ext" / "idp" / "stacklet.toml").write_text(
        'id = "idp"\nport = 42100\n[oidc_provider]\n')
    return Stack(root=REPO, data=tmp_path / "data", instance_dir=tmp_path)


def test_the_callback_names_the_source_forgejo_sends(instance):
    """Forgejo builds the callback from the source name, path-escaped.
    The provider redirects only to callbacks it was given."""
    [code] = [c for c in instance.oidc_clients() if c["stacklet"] == "code"]
    assert code["callbacks"] == [
        f"http://code.home.example.family/user/oauth2/"
        f"{oauth.SOURCE.replace(' ', '%20')}/callback"]


def test_forgejo_links_existing_accounts_and_creates_new_ones(instance):
    """Family members already have Forgejo accounts from users.toml, with
    the same usernames the provider sends as `preferred_username`."""
    env = instance.env("code")
    assert env["FORGEJO__oauth2_client__ENABLE_AUTO_REGISTRATION"] == "true"
    assert env["FORGEJO__oauth2_client__ACCOUNT_LINKING"] == "auto"
    assert env["FORGEJO__oauth2_client__USERNAME"] == "preferred_username"


# ── The Forgejo side ─────────────────────────────────────────────────────

ENV = {"OIDC_ISSUER": "https://id.home.example.family",
       "OIDC_CLIENT_ID": "c-1", "OIDC_CLIENT_SECRET": "s-1"}
NONE = {"OIDC_ISSUER": "", "OIDC_CLIENT_ID": "", "OIDC_CLIENT_SECRET": ""}


class FakeForgejo:
    """`forgejo admin auth` list, add-oauth and update-oauth, in memory."""

    def __init__(self, sources=None, fail=False):
        self.sources = dict(sources or {})   # id -> {name, flags}
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if self.fail and args[0] != "list":
            # What ctx.run_in_container raises: no arguments in it.
            raise RuntimeError("forgejo in stack-code failed (exit 1): bad input")
        if args[0] == "list":
            rows = [f"{i}\t{s['name']}\tOAuth2\ttrue" for i, s in self.sources.items()]
            return "\n".join(["ID\tName\tType\tEnabled", *rows]) + "\n"
        flags = _flags(args[1:])
        if args[0] == "add-oauth":
            self.sources[len(self.sources) + 1] = {"name": flags["--name"], "flags": flags}
        elif args[0] == "update-oauth":
            self.sources[int(flags["--id"])]["flags"].update(flags)
        return ""

    def writes(self):
        return [c for c in self.calls if c[0] != "list"]


def _flags(args):
    return dict(zip(args[::2], args[1::2]))


class Store:
    """The stacklet's own secret namespace."""

    def __init__(self):
        self.values = {}

    def __call__(self, name, value=None):
        if value is not None:
            self.values[name] = value
        return self.values.get(name)


def _sync(forgejo, store, env=ENV):
    steps = []
    oauth.sync(env, forgejo, store, steps.append)
    return steps


def test_creates_the_source_with_the_providers_discovery_url():
    forgejo, store = FakeForgejo(), Store()
    _sync(forgejo, store)
    [(sid, source)] = forgejo.sources.items()
    assert source["name"] == "family account"
    assert source["flags"]["--provider"] == "openidConnect"
    assert source["flags"]["--key"] == "c-1"
    assert source["flags"]["--secret"] == "s-1"
    assert source["flags"]["--auto-discover-url"] == (
        "https://id.home.example.family/.well-known/openid-configuration")


def test_a_second_run_changes_nothing_and_says_nothing():
    forgejo, store = FakeForgejo(), Store()
    _sync(forgejo, store)
    steps = _sync(forgejo, store)
    assert len(forgejo.writes()) == 1
    assert steps == []


def test_new_credentials_update_the_existing_source():
    """A provider that issued a new secret, or moved, must reach Forgejo
    without a second source appearing next to the first."""
    forgejo, store = FakeForgejo(), Store()
    _sync(forgejo, store)
    _sync(forgejo, store, env={**ENV, "OIDC_CLIENT_SECRET": "s-2"})
    assert len(forgejo.sources) == 1
    assert forgejo.writes()[-1][0] == "update-oauth"
    assert forgejo.sources[1]["flags"]["--secret"] == "s-2"


def test_a_source_deleted_in_the_ui_comes_back():
    forgejo, store = FakeForgejo(), Store()
    _sync(forgejo, store)
    forgejo.sources.clear()
    _sync(forgejo, store)
    assert [s["name"] for s in forgejo.sources.values()] == ["family account"]


def test_other_login_sources_are_left_alone():
    forgejo, store = FakeForgejo({1: {"name": "github", "flags": {}}}), Store()
    _sync(forgejo, store)
    assert forgejo.sources[1] == {"name": "github", "flags": {}}
    assert sorted(s["name"] for s in forgejo.sources.values()) == ["family account", "github"]


def test_without_credentials_forgejo_is_not_touched():
    forgejo, store = FakeForgejo(), Store()
    assert _sync(forgejo, store, env=NONE) == []
    assert forgejo.calls == []


def test_the_secret_never_reaches_the_output():
    forgejo, store = FakeForgejo(), Store()
    steps = _sync(forgejo, store)
    steps += _sync(forgejo, store, env={**ENV, "OIDC_CLIENT_SECRET": "s-2"})
    assert steps and not any("s-1" in s or "s-2" in s for s in steps)
    assert not any("s-1" in v or "s-2" in v for v in store.values.values())


def test_a_failed_command_is_reported_not_raised_and_retried():
    """A failed sync must not stop `stack up code`; the next run retries,
    so nothing may be recorded as applied."""
    forgejo, store = FakeForgejo(fail=True), Store()
    steps = _sync(forgejo, store)
    assert len(steps) == 1 and "s-1" not in steps[0]
    forgejo.fail = False
    _sync(forgejo, store)
    assert [s["name"] for s in forgejo.sources.values()] == ["family account"]


def test_reads_the_padded_table_forgejo_prints():
    """Forgejo pads columns with tabs, as its header shows ("Name\\t\\t")."""
    listing = ("ID\tName\t\tType\tEnabled\n"
               "1\tgh\t\tOAuth2\ttrue\n"
               "2\tfamily account\tOAuth2\ttrue\n")
    assert oauth.source_ids(listing) == {"gh": 1, "family account": 2}
