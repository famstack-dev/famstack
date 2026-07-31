"""AI-mode resolution and the scoped [ai] rewrite.

Pure logic, so it belongs in the offline lane. `apply()` is deliberately
untested here - it writes the live stack.toml, and a unit test that
mutates the running instance is a unit test that breaks the rig.
"""

from __future__ import annotations

import pytest

from tests.integration._ai_mode import (
    MOCK_MODEL,
    MOCK_URL,
    AIModeError,
    _rewrite,
    settings_for,
)

SAMPLE = """\
[core]
name = "stack"
default    = "not-the-ai-one"

[ai]
# a comment that must survive
openai_url = "http://localhost:42199/v1"
openai_key = "test"
default    = "test-model"
language   = "en"

[messages]
server_name = "simpson"
default    = "also-not-the-ai-one"
"""


def test_mock_needs_no_environment(monkeypatch):
    # The fallback must work in a bare checkout with nothing exported.
    for var in ("FAMSTACK_AI_URL", "FAMSTACK_AI_MODEL", "FAMSTACK_AI_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert settings_for("mock") == {
        "openai_url": MOCK_URL,
        "openai_key": "test",
        "default": MOCK_MODEL,
    }


def test_local_reports_what_is_missing(monkeypatch):
    monkeypatch.delenv("FAMSTACK_AI_URL", raising=False)
    with pytest.raises(AIModeError, match="FAMSTACK_AI_URL"):
        settings_for("local")


def test_local_reads_the_environment(monkeypatch):
    monkeypatch.setenv("FAMSTACK_AI_URL", "http://elsewhere:9/v1")
    monkeypatch.setenv("FAMSTACK_AI_MODEL", "some-model")
    monkeypatch.delenv("FAMSTACK_AI_KEY", raising=False)
    assert settings_for("local") == {
        "openai_url": "http://elsewhere:9/v1",
        "openai_key": "",  # self-hosted endpoints commonly ignore the key
        "default": "some-model",
    }


def test_unknown_mode_lists_the_valid_ones():
    with pytest.raises(AIModeError, match="mock, local, external"):
        settings_for("locale")


def test_rewrite_only_touches_the_ai_table():
    # `default` appears in [core] and [messages] too. A sloppy regex would
    # rewrite the first match in the file and silently corrupt another
    # stacklet's config while appearing to work.
    out = _rewrite(SAMPLE, "default", "new-model")
    assert 'default    = "new-model"' in out
    assert 'default    = "not-the-ai-one"' in out
    assert 'default    = "also-not-the-ai-one"' in out
    assert out.count("new-model") == 1


def test_rewrite_preserves_comments():
    out = _rewrite(SAMPLE, "openai_url", "http://x/v1")
    assert "# a comment that must survive" in out
    assert 'openai_url = "http://x/v1"' in out


def test_rewrite_reports_a_missing_key():
    with pytest.raises(AIModeError, match="openai_org"):
        _rewrite(SAMPLE, "openai_org", "whatever")
