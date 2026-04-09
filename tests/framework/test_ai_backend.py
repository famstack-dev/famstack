"""AI backend: probes endpoints, verifies configured provider."""

import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# backend.py lives in stacklets/ai/, not in a package
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "stacklets" / "ai"))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mock_urlopen(responses):
    """Mock urlopen: responses is {url_substring: response_or_exception}."""
    def _urlopen(req, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for pattern, response in responses.items():
            if pattern in url:
                if isinstance(response, int):
                    raise urllib.error.HTTPError(url, response, "", {}, None)
                if isinstance(response, Exception):
                    raise response
                mock_resp = MagicMock()
                mock_resp.read.return_value = json.dumps(response).encode()
                mock_resp.__enter__ = lambda s: s
                mock_resp.__exit__ = MagicMock(return_value=False)
                return mock_resp
        raise urllib.error.URLError("Connection refused")
    return _urlopen


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

    def test_reachable(self):
        from backend import _probe
        resp = {"data": [{"id": "model-1"}]}
        with patch("urllib.request.urlopen", _mock_urlopen({"/models": resp})):
            result = _probe("http://localhost:42060/v1")
        assert result.reachable
        assert not result.needs_auth
        assert "model-1" in result.models

    def test_needs_auth_401(self):
        from backend import _probe
        with patch("urllib.request.urlopen", _mock_urlopen({"/models": 401})):
            result = _probe("http://localhost:42060/v1")
        assert not result.reachable
        assert result.needs_auth

    def test_needs_auth_403(self):
        from backend import _probe
        with patch("urllib.request.urlopen", _mock_urlopen({"/models": 403})):
            result = _probe("http://localhost:42060/v1")
        assert not result.reachable
        assert result.needs_auth

    def test_connection_refused(self):
        from backend import _probe
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = _probe("http://localhost:42060/v1")
        assert not result.reachable
        assert not result.needs_auth

    def test_timeout(self):
        from backend import _probe
        with patch("urllib.request.urlopen", side_effect=TimeoutError()):
            result = _probe("http://localhost:42060/v1")
        assert not result.reachable

    def test_auth_with_key(self):
        from backend import _probe
        resp = {"data": [{"id": "m1"}]}
        with patch("urllib.request.urlopen", _mock_urlopen({"/models": resp})):
            result = _probe("http://localhost:42060/v1", key="secret")
        assert result.reachable


# ── Ensure backend ───────────────────────────────────────────────────────────

class TestEnsureBackend:

    def test_configured_and_working(self, tmp_path):
        from backend import ensure_backend
        _stack_toml(tmp_path, ai_url="http://localhost:42060/v1")
        resp = {"data": [{"id": "m1"}]}
        with patch("urllib.request.urlopen", _mock_urlopen({"42060": resp})):
            result = ensure_backend(tmp_path, interactive=False)
        assert result["url"] == "http://localhost:42060/v1"

    def test_external_endpoint_working(self, tmp_path):
        from backend import ensure_backend
        _stack_toml(tmp_path, ai_url="https://api.example.com/v1", ai_key="sk-123", provider="external")
        resp = {"data": [{"id": "gpt-4"}]}
        with patch("urllib.request.urlopen", _mock_urlopen({"example.com": resp})):
            result = ensure_backend(tmp_path, interactive=False)
        assert result["url"] == "https://api.example.com/v1"
        assert result["key"] == "sk-123"

    def test_configured_but_down_returns_error(self, tmp_path):
        """No waterfall — if the configured endpoint is down, return error."""
        from backend import ensure_backend
        _stack_toml(tmp_path, ai_url="http://localhost:42060/v1")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = ensure_backend(tmp_path, interactive=False)
        assert "error" in result

    def test_no_url_configured_returns_error(self, tmp_path):
        from backend import ensure_backend
        _stack_toml(tmp_path)
        result = ensure_backend(tmp_path, interactive=False)
        assert "error" in result

    def test_auth_required_no_key_returns_error(self, tmp_path):
        from backend import ensure_backend
        _stack_toml(tmp_path, ai_url="http://localhost:42060/v1")
        with patch("urllib.request.urlopen", _mock_urlopen({"42060": 401})):
            result = ensure_backend(tmp_path, interactive=False)
        assert "error" in result
        assert "API key" in result["error"]

    def test_auth_with_key_succeeds(self, tmp_path):
        from backend import ensure_backend
        _stack_toml(tmp_path, ai_url="http://localhost:42060/v1", ai_key="secret")
        resp = {"data": [{"id": "m1"}]}
        with patch("urllib.request.urlopen", _mock_urlopen({"42060": resp})):
            result = ensure_backend(tmp_path, interactive=False)
        assert result["url"] == "http://localhost:42060/v1"
        assert result["key"] == "secret"

    def test_error_message_says_omlx_for_managed(self, tmp_path):
        from backend import ensure_backend
        _stack_toml(tmp_path, ai_url="http://localhost:42060/v1", provider="managed")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = ensure_backend(tmp_path, interactive=False)
        assert "oMLX" in result["error"]

    def test_error_message_says_external_for_external(self, tmp_path):
        from backend import ensure_backend
        _stack_toml(tmp_path, ai_url="https://api.example.com/v1", provider="external")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = ensure_backend(tmp_path, interactive=False)
        assert "external" in result["error"]


# ── Ensure model ─────────────────────────────────────────────────────────────

class TestEnsureModel:

    def test_model_loaded(self, tmp_path):
        from backend import ensure_model
        _stack_toml(tmp_path, ai_url="http://localhost:42060/v1")
        resp = {"data": [{"id": "Qwen3.5-9B-MLX-8bit"}]}
        with patch("urllib.request.urlopen", _mock_urlopen({"42060": resp})):
            result = ensure_model(tmp_path, "Qwen3.5-9B-MLX-8bit", interactive=False)
        assert result.get("loaded")

    def test_external_model_missing_warns(self, tmp_path):
        from backend import ensure_model
        _stack_toml(tmp_path, ai_url="https://api.example.com/v1", provider="external")
        resp = {"data": [{"id": "gpt-4"}]}
        with patch("urllib.request.urlopen", _mock_urlopen({"example.com": resp})):
            result = ensure_model(tmp_path, "Qwen3.5-9B-MLX-8bit", interactive=False)
        assert "warning" in result

    def test_no_endpoint_returns_error(self, tmp_path):
        from backend import ensure_model
        _stack_toml(tmp_path)
        result = ensure_model(tmp_path, "some-model", interactive=False)
        assert "error" in result
