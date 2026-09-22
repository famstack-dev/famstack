"""ai on_start hook: STACK_AI_NO_VOICE gates the voice compose profile.

This is the behavior that `stack up ai --no-voice` (and the bare
STACK_AI_NO_VOICE=1 env var) relies on: when set, on_start clears the
"voice" compose profile so docker skips the Piper TTS container. With a
configured provider the hook does no docker or network work, so this runs
fast and offline.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(REPO / "lib"))


_STATE_DIR = None


def _load_on_start():
    # on_start.py lives under stacklets/ai/hooks/, not in a package.
    path = REPO / "stacklets" / "ai" / "hooks" / "on_start.py"
    spec = importlib.util.spec_from_file_location("ai_on_start", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Its state markers live in the checkout; keep the test out of it.
    mod.STATE_DIR = _STATE_DIR
    return mod


@pytest.fixture(autouse=True)
def _state_dir(tmp_path):
    global _STATE_DIR
    _STATE_DIR = tmp_path / "state"
    yield
    _STATE_DIR = None


def _ctx(make_stack, env, engine_url):
    from stack.hooks import StackContext

    stck = make_stack()
    # Managed with an engine that answers, so on_start neither bails
    # early, nor asks about leaving a remote endpoint, nor runs
    # `brew services start omlx` for real.
    stck._set_cfg("ai", "provider", "managed")
    stck._set_cfg("ai", "openai_url", engine_url)
    return StackContext(stck, "ai", env)


@pytest.fixture
def engine_url(httpserver):
    httpserver.expect_request("/v1/models").respond_with_json({"data": []})
    return httpserver.url_for("/v1")


@pytest.fixture(autouse=True)
def _engine_installed(monkeypatch):
    """Pretend oMLX is on the machine, because that is not what these cover.

    on_start refuses to start a managed provider whose oMLX binary has gone
    missing; that behaviour has its own tests. Without this stub these would
    pass or fail on whether the machine running the suite happens to have
    oMLX installed -- green on a developer Mac, red on CI, for reasons that
    have nothing to do with compose profiles.
    """
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/local/bin/omlx")


@pytest.fixture(autouse=True)
def _restore_no_voice():
    old = os.environ.get("STACK_AI_NO_VOICE")
    yield
    if old is None:
        os.environ.pop("STACK_AI_NO_VOICE", None)
    else:
        os.environ["STACK_AI_NO_VOICE"] = old


def test_no_voice_clears_compose_profile(make_stack, engine_url):
    os.environ["STACK_AI_NO_VOICE"] = "1"
    env = {"COMPOSE_PROFILES": "voice"}
    _load_on_start().run(_ctx(make_stack, env, engine_url))
    assert env["COMPOSE_PROFILES"] == ""


def test_voice_profile_kept_by_default(make_stack, engine_url):
    os.environ.pop("STACK_AI_NO_VOICE", None)
    env = {"COMPOSE_PROFILES": "voice"}
    _load_on_start().run(_ctx(make_stack, env, engine_url))
    assert env["COMPOSE_PROFILES"] == "voice"
