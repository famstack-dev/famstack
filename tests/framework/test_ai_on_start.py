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

REPO = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO / "lib"))


def _load_on_start():
    # on_start.py lives under stacklets/ai/hooks/, not in a package.
    path = REPO / "stacklets" / "ai" / "hooks" / "on_start.py"
    spec = importlib.util.spec_from_file_location("ai_on_start", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ctx(make_stack, env):
    from stack.hooks import StackContext

    stck = make_stack()
    stck._set_cfg("ai", "provider", "managed")  # so on_start doesn't bail early
    return StackContext(stck, "ai", env)


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


def test_no_voice_clears_compose_profile(make_stack):
    os.environ["STACK_AI_NO_VOICE"] = "1"
    env = {"COMPOSE_PROFILES": "voice"}
    _load_on_start().run(_ctx(make_stack, env))
    assert env["COMPOSE_PROFILES"] == ""


def test_voice_profile_kept_by_default(make_stack):
    os.environ.pop("STACK_AI_NO_VOICE", None)
    env = {"COMPOSE_PROFILES": "voice"}
    _load_on_start().run(_ctx(make_stack, env))
    assert env["COMPOSE_PROFILES"] == "voice"
