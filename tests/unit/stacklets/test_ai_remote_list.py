"""`stack list` when the AI runs on another machine.

After `stack ai switch <url>` the family's AI is served elsewhere. The
ai stacklet's row used to say `online localhost:42060` whenever its local
containers happened to run, because its LLM check probes the configured
address and the remote server answered it. That claimed the engine on
this Mac was serving when the stack did not use it at all; and on an
instance that never installed it, the row said `available`, as if there
were no AI.

A stacklet whose manifest renders a `remote` address is shown as
remote, with that server's host, set up locally or not. Only its checks
marked `remote = true` run: the others probe this Mac.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import stack.docker
from stack import Stack
from stack.prompt import status_list

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def instance(tmp_path, monkeypatch):
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\n\n[ai]\nlanguage = "en"\n'
        '\n[messages]\nserver_name = "simpson"\n')
    (tmp_path / ".stack").mkdir()
    monkeypatch.setattr(stack.docker, "project_states", lambda: {})
    return Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path)


def _ai_row(instance):
    return next(s for s in instance.list()["stacklets"] if s["id"] == "ai")


def _remote(instance, url):
    instance._set_cfg("ai", "provider", "external")
    instance._set_cfg("ai", "openai_url", url)
    instance._set_cfg("ai", "openai_key", "sk-test")


class TestTheRow:

    def test_a_remote_server_is_shown_as_remote_even_when_never_set_up_here(
            self, instance, httpserver):
        httpserver.expect_request("/v1/models").respond_with_json({"data": []})
        _remote(instance, httpserver.url_for("/v1"))

        row = _ai_row(instance)

        assert row["remote"] == httpserver.url_for("/v1")
        assert not row["degraded"]

    def test_a_remote_server_that_does_not_answer_is_degraded_and_named(self, instance):
        _remote(instance, "http://127.0.0.1:9/v1")

        row = _ai_row(instance)

        assert row["degraded"]
        assert row["health_issues"] == [
            "AI server not answering at http://127.0.0.1:9/v1"]

    def test_the_local_engine_is_not_remote(self, instance):
        instance._set_cfg("ai", "provider", "managed")
        instance._set_cfg("ai", "openai_url", "http://localhost:42060/v1")

        assert "remote" not in _ai_row(instance)


class TestVoiceOnThisMac:
    """Chat on a server elsewhere, speech-to-text and text-to-speech from
    the ai stacklet on this Mac. The row reports the server, and the
    checks of what runs here still apply: a dead Whisper was never
    reported, because only the checks marked `remote` ran."""

    @pytest.fixture
    def probed(self, monkeypatch):
        """The addresses the listing checks, each answering. The checks
        of this Mac's voice services probe fixed ports, which a test
        cannot own."""
        urls = []
        monkeypatch.setattr(stack.docker, "check_health",
                            lambda url, headers=None: urls.append(url) or True)
        return urls

    def test_what_runs_here_is_checked_too(self, instance, monkeypatch, probed):
        _remote(instance, "http://localhost:8888/v1")
        (instance.instance_dir / ".stack" / "ai.setup-done").touch()
        monkeypatch.setattr(stack.docker, "project_states", lambda: {"ai": "running"})

        row = _ai_row(instance)

        assert row["remote"] == "http://localhost:8888/v1"
        assert "http://localhost:42062/" in probed
        assert "http://localhost:42063/" in probed
        assert "http://localhost:8888/v1/models" in probed

    def test_without_it_running_here_only_the_server_is_checked(self, instance, probed):
        _remote(instance, "http://localhost:8888/v1")

        _ai_row(instance)

        assert probed == ["http://localhost:8888/v1/models"]


class TestTheListing:

    def test_it_reads_remote_with_the_servers_host(self, capsys):
        status_list([{"id": "ai", "name": "AI", "remote": "https://ai.example.test/v1",
                      "enabled": True, "online": True}])

        line = capsys.readouterr().out
        assert "remote" in line
        assert "ai.example.test" in line
        assert "localhost" not in line

    def test_a_server_on_this_mac_reads_localhost(self, capsys):
        """Not another machine: an app on this Mac the stack does not run."""
        status_list([{"id": "ai", "name": "AI", "remote": "http://localhost:8888/v1",
                      "enabled": True, "online": True}])

        line = capsys.readouterr().out
        assert "localhost" in line
        assert "port 8888, not managed by the stack" in line
        assert "remote" not in line
