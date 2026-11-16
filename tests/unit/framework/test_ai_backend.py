"""AI backend: probes endpoints, verifies configured provider.

The client talks to a real HTTP server on localhost (`httpserver`), so
the request it sends and the way it reads an answer are the ones a real
endpoint sees. The oMLX stand-in answers the key check; a plain
`/v1/models` handler lists models, which the stand-in leaves empty.
"""

import socket
import time

from tests.unit.stacklets.omlx_stand_in import KEY, OMLX


# ── Helpers ──────────────────────────────────────────────────────────────────

def _lists(httpserver, *ids):
    """Serve `/v1/models` listing `ids`; return the base URL a client uses."""
    httpserver.expect_request("/v1/models").respond_with_json(
        {"object": "list", "data": [{"id": i} for i in ids]})
    return httpserver.url_for("/v1")


def _closed_port_url():
    """A base URL on a port nothing listens on, so a connection is refused."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}/v1"


def _stack_toml(tmp_path, ai_url="", ai_key="", provider="managed"):
    content = f"""[core]
timezone = "UTC"

[ai]
provider = "{provider}"
openai_url = "{ai_url}"
openai_key = "{ai_key}"
whisper_url = "http://localhost:42062/v1"
language = "en"
default = ""
"""
    (tmp_path / "stack.toml").write_text(content)


# ── Probe ────────────────────────────────────────────────────────────────────

class TestProbe:

    def test_reachable(self, httpserver):
        from stacklets.ai.backend import _probe
        result = _probe(_lists(httpserver, "model-1"))
        assert result.reachable
        assert not result.needs_auth
        assert "model-1" in result.models

    def test_needs_auth_401(self, httpserver):
        from stacklets.ai.backend import _probe
        OMLX(httpserver, wants_key=True)
        result = _probe(httpserver.url_for("/v1"))
        assert not result.reachable
        assert result.needs_auth

    def test_needs_auth_403(self, httpserver):
        from stacklets.ai.backend import _probe
        httpserver.expect_request("/v1/models").respond_with_data("", status=403)
        result = _probe(httpserver.url_for("/v1"))
        assert not result.reachable
        assert result.needs_auth

    def test_server_error_is_not_an_auth_problem(self, httpserver):
        from stacklets.ai.backend import _probe
        httpserver.expect_request("/v1/models").respond_with_data("", status=500)
        result = _probe(httpserver.url_for("/v1"))
        assert not result.reachable
        assert not result.needs_auth

    def test_connection_refused(self):
        from stacklets.ai.backend import _probe
        result = _probe(_closed_port_url())
        assert not result.reachable
        assert not result.needs_auth

    def test_a_server_that_answers_too_late_is_unreachable(self, httpserver):
        """A server that accepts the connection and then says nothing must
        not hold the setup: the probe gives up after its timeout."""
        from werkzeug.wrappers import Response

        from stacklets.ai.backend import _probe

        def too_late(request):
            time.sleep(1.0)
            return Response("{}")

        httpserver.expect_request("/v1/models").respond_with_handler(too_late)

        result = _probe(httpserver.url_for("/v1"), timeout=0.2)

        assert not result.reachable
        assert not result.needs_auth

    def test_auth_with_key(self, httpserver):
        from stacklets.ai.backend import _probe
        OMLX(httpserver, wants_key=True)
        result = _probe(httpserver.url_for("/v1"), key=KEY)
        assert result.reachable
        assert httpserver.log[-1][0].headers["Authorization"] == f"Bearer {KEY}"

    def test_wrong_key_is_refused(self, httpserver):
        from stacklets.ai.backend import _probe
        OMLX(httpserver, wants_key=True)
        result = _probe(httpserver.url_for("/v1"), key="not-the-key")
        assert not result.reachable
        assert result.needs_auth


# ── Ensure backend ───────────────────────────────────────────────────────────

class TestEnsureBackend:

    def test_configured_and_working(self, tmp_path, httpserver):
        from stacklets.ai.backend import ensure_backend
        url = _lists(httpserver, "m1")
        _stack_toml(tmp_path, ai_url=url)
        result = ensure_backend(tmp_path, interactive=False)
        assert result["url"] == url

    def test_external_endpoint_working(self, tmp_path, httpserver):
        from stacklets.ai.backend import ensure_backend
        url = _lists(httpserver, "gpt-4")
        _stack_toml(tmp_path, ai_url=url, ai_key="sk-123", provider="external")
        result = ensure_backend(tmp_path, interactive=False)
        assert result["url"] == url
        assert result["key"] == "sk-123"
        assert httpserver.log[-1][0].headers["Authorization"] == "Bearer sk-123"

    def test_configured_but_down_returns_error(self, tmp_path):
        """No waterfall: if the configured endpoint is down, return error."""
        from stacklets.ai.backend import ensure_backend
        _stack_toml(tmp_path, ai_url=_closed_port_url())
        result = ensure_backend(tmp_path, interactive=False)
        assert "error" in result

    def test_no_url_configured_returns_error(self, tmp_path):
        from stacklets.ai.backend import ensure_backend
        _stack_toml(tmp_path)
        result = ensure_backend(tmp_path, interactive=False)
        assert "error" in result

    def test_auth_required_no_key_returns_error(self, tmp_path, httpserver):
        from stacklets.ai.backend import ensure_backend
        OMLX(httpserver, wants_key=True)
        _stack_toml(tmp_path, ai_url=httpserver.url_for("/v1"))
        result = ensure_backend(tmp_path, interactive=False)
        assert "error" in result
        assert "API key" in result["error"]

    def test_auth_with_key_succeeds(self, tmp_path, httpserver):
        from stacklets.ai.backend import ensure_backend
        OMLX(httpserver, wants_key=True)
        url = httpserver.url_for("/v1")
        _stack_toml(tmp_path, ai_url=url, ai_key=KEY)
        result = ensure_backend(tmp_path, interactive=False)
        assert result["url"] == url
        assert result["key"] == KEY

    def test_error_message_says_omlx_for_managed(self, tmp_path):
        from stacklets.ai.backend import ensure_backend
        _stack_toml(tmp_path, ai_url=_closed_port_url(), provider="managed")
        result = ensure_backend(tmp_path, interactive=False)
        assert "oMLX" in result["error"]

    def test_error_message_says_external_for_external(self, tmp_path):
        from stacklets.ai.backend import ensure_backend
        _stack_toml(tmp_path, ai_url=_closed_port_url(), provider="external")
        result = ensure_backend(tmp_path, interactive=False)
        assert "external" in result["error"]


# ── Ensure model ─────────────────────────────────────────────────────────────

class TestEnsureModel:

    def test_model_loaded(self, tmp_path, httpserver):
        from stacklets.ai.backend import ensure_model
        _stack_toml(tmp_path, ai_url=_lists(httpserver, "Qwen3.5-9B-MLX-8bit"))
        result = ensure_model(tmp_path, "Qwen3.5-9B-MLX-8bit", interactive=False)
        assert result.get("loaded")

    def test_external_model_missing_warns(self, tmp_path, httpserver):
        from stacklets.ai.backend import ensure_model
        _stack_toml(tmp_path, ai_url=_lists(httpserver, "gpt-4"), provider="external")
        result = ensure_model(tmp_path, "Qwen3.5-9B-MLX-8bit", interactive=False)
        assert "warning" in result
        assert "gpt-4" in result["warning"]

    def test_no_endpoint_returns_error(self, tmp_path):
        from stacklets.ai.backend import ensure_model
        _stack_toml(tmp_path)
        result = ensure_model(tmp_path, "some-model", interactive=False)
        assert "error" in result
