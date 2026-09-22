"""The DNS provider token: stored once, checked on every start, never shown.

With `[core] dns_provider` set, the infra stacklet's Caddy obtains its
certificates through that provider's API, which needs a token. It lives in
the secret store, never in stack.toml, and `stack infra dns-token` is the
one way in, for the first run and for every rotation after it. `stack up
infra` refuses to start a proxy that would fail to get certificates, and
says how to fix it.

The tests drive the real infra stacklet: its CLI command through
`Stack.run_cli_command`, the same path as `stack infra dns-token`, and its
start hook the way the framework calls it.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO / "lib"))

from stack import Stack  # noqa: E402
from stack.hooks import StackContext  # noqa: E402

TOKEN = "hk_test_0123456789abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def stack(tmp_path):
    """The repo's stacklets over a throwaway instance."""
    def _make(dns_provider: str = "hetzner") -> Stack:
        (tmp_path / "stack.toml").write_text(
            '[core]\ndomain = "home.example.family"\n'
            f'dns_provider = "{dns_provider}"\n'
            f'extension_dirs = ["{tmp_path / "ext"}"]\n')
        (tmp_path / ".stack").mkdir(exist_ok=True)
        return Stack(root=REPO, data=tmp_path / "data", instance_dir=tmp_path)
    return _make


def _dns_token(stck: Stack, stdin: str, monkeypatch) -> dict:
    """`stack infra dns-token` with the token piped in, as a script would."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    result = stck.run_cli_command("infra", "dns-token")
    assert result is not None
    return result


def _start(stck: Stack) -> None:
    path = REPO / "stacklets" / "infra" / "hooks" / "on_start.py"
    spec = importlib.util.spec_from_file_location("infra_on_start", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run(StackContext(stck, "infra", {}))


# ── stack infra dns-token ─────────────────────────────────────────────────


def test_the_token_is_kept_in_the_secret_store(stack, monkeypatch):
    stck = stack()
    _dns_token(stck, TOKEN + "\n", monkeypatch)
    assert stck.secrets.get("infra", "DNS_API_TOKEN") == TOKEN


def test_the_token_is_never_echoed(stack, monkeypatch):
    """Command output ends up in terminals, logs and chat. Its length is
    enough to tell a pasted token from a truncated one."""
    result = _dns_token(stack(), TOKEN + "\n", monkeypatch)
    assert TOKEN not in repr(result)
    assert str(len(TOKEN)) in repr(result)


def test_a_new_token_replaces_the_old_one(stack, monkeypatch):
    stck = stack()
    _dns_token(stck, "old-token\n", monkeypatch)
    _dns_token(stck, TOKEN + "\n", monkeypatch)
    assert stck.secrets.get("infra", "DNS_API_TOKEN") == TOKEN


def test_an_empty_token_is_refused(stack, monkeypatch):
    stck = stack()
    _dns_token(stck, TOKEN + "\n", monkeypatch)
    result = _dns_token(stck, "\n", monkeypatch)
    assert "error" in result
    assert stck.secrets.get("infra", "DNS_API_TOKEN") == TOKEN


# ── stack up infra ────────────────────────────────────────────────────────


def test_start_needs_a_token_once_a_provider_is_set(stack):
    with pytest.raises(RuntimeError, match="stack infra dns-token"):
        _start(stack())


def test_start_goes_ahead_once_the_token_is_stored(stack, monkeypatch):
    stck = stack()
    _dns_token(stck, TOKEN + "\n", monkeypatch)
    _start(stck)


def test_plain_http_needs_no_token(stack):
    _start(stack(dns_provider=""))


def test_start_refuses_a_provider_the_image_has_no_plugin_for(stack, monkeypatch):
    stck = stack(dns_provider="route53")
    _dns_token(stck, TOKEN + "\n", monkeypatch)
    with pytest.raises(RuntimeError, match="route53"):
        _start(stck)
