"""ai on_start hook: the speech container the stacklet used to run goes.

The stack's voices speak through oMLX, so the ai stacklet has no container
of its own. An instance that ran the old one, `stack-ai-speech` (Piper),
still has it after the update: `stack up ai` removes it and its image,
once, and only when Docker says it belongs to the ai stacklet's compose
project. With a configured provider and an engine that answers, the hook
does no other docker or network work, so this runs fast and offline.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(REPO / "lib"))


_STATE_DIR = None


def _load_on_start():
    # Afresh for each test, as the hook runner loads it: `STATE_DIR` is set below.
    from stack.stacklet_code import load
    mod = load(REPO / "stacklets" / "ai", REPO / "stacklets" / "ai" / "hooks" / "on_start.py")
    # Its state markers live in the checkout; keep the test out of it.
    mod.STATE_DIR = _STATE_DIR
    return mod


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    global _STATE_DIR
    _STATE_DIR = tmp_path / "state"
    # oMLX's settings live in the home directory; keep the test out of it.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
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


# `docker` with one container, `stack-ai-speech`, in the compose project
# `$PROJECT`. It answers `ps` only for a filter on that project, as the
# real client does, and writes every other command it runs to `$CALLS`.
DOCKER = """#!/bin/sh
[ "$1" = "--context" ] && shift 2
case "$1" in
  ps)      [ "$4" = "label=com.docker.compose.project=$PROJECT" ] &&
             printf 'stack-ai-speech\\t%s\\trunning\\tUp 2 hours\\n' "$PROJECT" ;;
  inspect) echo "sha256:0ld1mage" ;;
  rm|image) echo "$*" >> "$CALLS" ;;
  *)       echo "unexpected: docker $*" >&2; exit 125 ;;
esac
"""


@pytest.fixture
def docker(tmp_path, monkeypatch):
    """The stand-in first on PATH; the commands it was asked to run."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(DOCKER)
    (bin_dir / "docker").chmod(0o755)
    calls = tmp_path / "calls"
    monkeypatch.setenv("PATH", str(bin_dir), prepend=":")
    monkeypatch.setenv("CALLS", str(calls))
    return lambda: calls.read_text().splitlines() if calls.exists() else []


def test_an_updated_instance_loses_the_old_speech_container_and_its_image(
        make_stack, engine_url, docker, monkeypatch):
    monkeypatch.setenv("PROJECT", "stack-ai")

    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert docker() == ["rm -f stack-ai-speech", "image rm sha256:0ld1mage"]


def test_a_container_of_that_name_in_another_project_stays(make_stack, engine_url, docker,
                                                           monkeypatch):
    monkeypatch.setenv("PROJECT", "someone-else")

    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert docker() == []


# ── oMLX's memory guard on a small Mac ───────────────────────────────────
#
# oMLX refuses or aborts what does not fit under its memory guard's ceiling.
# Its default tier, "balanced", computes the ceiling from the memory free at
# that moment: on a 16 GB Mac with Docker's VM and Whisper resident, that was
# under the 5.8 GB of the default model. A fixed 9 GB ceiling and chunked
# prefill let a contract-sized request through. `sysctl` and `brew` are
# stand-ins that report the Mac's memory and log what they are asked to run.

GB = 1024 ** 3

# Settings written by an oMLX that has no idle timeout (before 0.7).
OLDER_OMLX = object()


@pytest.fixture
def small_mac(tmp_path, monkeypatch, docker):
    """A Mac with `ram` GB whose oMLX settings hold `tier`; returns a
    function that sets both and gives back the settings and brew's calls."""
    import json

    bin_dir = tmp_path / "bin"
    brew_calls = tmp_path / "brew-calls"
    (bin_dir / "brew").write_text(f'#!/bin/sh\necho "$*" >> "{brew_calls}"\n')
    (bin_dir / "brew").chmod(0o755)
    settings = tmp_path / "home" / ".omlx" / "settings.json"
    settings.parent.mkdir(parents=True)
    monkeypatch.setenv("PROJECT", "stack-ai")

    def mac(ram_gb: int, tier: str | None, chunked: bool = False, idle=OLDER_OMLX):
        """`idle` is the idle timeout in seconds, None for oMLX's unset
        value, or OLDER_OMLX for settings without the idle timeout."""
        (bin_dir / "sysctl").write_text(f"#!/bin/sh\necho {ram_gb * GB}\n")
        (bin_dir / "sysctl").chmod(0o755)
        memory = {"memory_guard_tier": tier} if tier else {}
        written = {"server": {"host": "127.0.0.1"}, "memory": memory,
                   "scheduler": {"chunked_prefill": chunked}}
        if idle is not OLDER_OMLX:
            written["idle_timeout"] = {"idle_timeout_seconds": idle}
        settings.write_text(json.dumps(written))

    def idle():
        written = json.loads(settings.read_text())
        return written.get("idle_timeout", OLDER_OMLX)

    def state():
        written = json.loads(settings.read_text())
        calls = brew_calls.read_text().splitlines() if brew_calls.exists() else []
        memory = written.get("memory", {})
        return (memory.get("memory_guard_tier"), memory.get("memory_guard_custom_ceiling_gb"),
                written.get("scheduler", {}).get("chunked_prefill"), calls)

    mac.state = state
    mac.idle = idle
    return mac


@pytest.mark.parametrize("tier", ["balanced", None])
def test_a_16_gb_mac_gets_a_fixed_9_gb_ceiling_chunked_prefill_and_one_restart(
        make_stack, engine_url, small_mac, tier):
    small_mac(16, tier)

    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert small_mac.state() == ("custom", 9.0, True, ["services restart omlx"])


def test_the_second_start_changes_nothing(make_stack, engine_url, small_mac):
    small_mac(16, "balanced")
    _load_on_start().run(_ctx(make_stack, {}, engine_url))
    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert small_mac.state() == ("custom", 9.0, True, ["services restart omlx"])


def test_a_tier_the_admin_chose_stays_and_the_prefill_is_still_chunked(make_stack, engine_url,
                                                                       small_mac):
    small_mac(16, "aggressive")

    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert small_mac.state() == ("aggressive", None, True, ["services restart omlx"])


def test_a_larger_mac_keeps_omlx_as_it_is(make_stack, engine_url, small_mac):
    small_mac(32, "balanced")

    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert small_mac.state() == ("balanced", None, False, [])


def test_a_mac_set_to_the_small_profile_gets_the_ceiling_whatever_its_memory(
        make_stack, engine_url, small_mac):
    """`[core] profile` overrides the memory: a 32 GB Mac the admin set to
    "small" is fitted as a 16 GB one is."""
    small_mac(32, "balanced")
    ctx = _ctx(make_stack, {}, engine_url)
    ctx.stack._set_cfg("core", "profile", "small")

    _load_on_start().run(ctx)

    assert small_mac.state() == ("custom", 9.0, True, ["services restart omlx"])


# ── Idle unload ──
#
# oMLX 0.7 writes `idle_timeout.idle_timeout_seconds` as null: the model
# stays loaded. On a small Mac the start sets it to 600 s, so a quiet
# night gives the model's 5.8 GB back to Docker and macOS.

def test_a_16_gb_mac_unloads_the_model_after_ten_idle_minutes(make_stack, engine_url,
                                                               small_mac):
    small_mac(16, "balanced", idle=None)

    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert small_mac.idle() == {"idle_timeout_seconds": 600}
    assert small_mac.state()[3] == ["services restart omlx"]


def test_an_idle_timeout_the_admin_set_stays(make_stack, engine_url, small_mac):
    small_mac(16, "custom", chunked=True, idle=3600)

    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert small_mac.idle() == {"idle_timeout_seconds": 3600}


def test_an_idle_unload_is_set_once(make_stack, engine_url, small_mac):
    small_mac(16, "balanced", idle=None)
    _load_on_start().run(_ctx(make_stack, {}, engine_url))
    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert small_mac.idle() == {"idle_timeout_seconds": 600}
    assert small_mac.state()[3] == ["services restart omlx"]


def test_a_larger_mac_keeps_its_model_loaded(make_stack, engine_url, small_mac):
    small_mac(32, "balanced", idle=None)

    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert small_mac.idle() == {"idle_timeout_seconds": None}
    assert small_mac.state()[3] == []


def test_an_older_omlx_gets_no_idle_timeout_and_still_starts(make_stack, engine_url, small_mac):
    small_mac(16, "custom", chunked=True)

    _load_on_start().run(_ctx(make_stack, {}, engine_url))

    assert small_mac.idle() is OLDER_OMLX
    assert small_mac.state()[3] == []


# ── The oMLX app on a small Mac ──
#
# The oMLX app (or any oMLX the admin runs on this Mac) is an external
# provider, but it is on this Mac and shares its memory with the stack.
# A small Mac's start gives it the same ceiling, chunked prefill and idle
# unload as the oMLX the stack installs, through oMLX's admin API: the app
# applies and saves them itself, and is never quit or restarted. The app
# is the oMLX stand-in; `small_mac` sets the Mac's memory and logs brew.

@pytest.fixture
def omlx_app(httpserver, make_stack, small_mac):
    """The oMLX app on a Mac with `ram` GB, as `stack ai switch` stores it;
    returns the stand-in and a function that runs the AI's start."""
    from tests.unit.stacklets.omlx_stand_in import KEY, OMLX

    def app(ram_gb: int, **stand_in):
        small_mac(ram_gb, "balanced")
        omlx = OMLX(httpserver, **stand_in)

        def start():
            from stack.hooks import StackContext
            stck = make_stack()
            stck._set_cfg("ai", "provider", "external")
            stck._set_cfg("ai", "openai_url", httpserver.url_for("/v1"))
            stck._set_cfg("ai", "openai_key", KEY)
            _load_on_start().run(StackContext(stck, "ai", {}))

        omlx.start = start
        return omlx
    return app


def _app_settings(omlx):
    """The app's ceiling, prefill and idle timeout, as it holds them now."""
    return (omlx.settings["memory"]["memory_guard_tier"],
            omlx.settings["memory"]["memory_guard_custom_ceiling_gb"],
            omlx.settings["scheduler"]["chunked_prefill"],
            omlx.settings["idle_timeout"]["idle_timeout_seconds"])


def test_a_16_gb_mac_on_the_omlx_app_gets_the_ceiling_prefill_and_idle_unload(
        omlx_app, small_mac, capsys):
    """Given a 16 GB Mac whose AI is the oMLX app at its defaults, when
    the AI starts, then the app holds the fixed 9 GB ceiling, chunked
    prefill and a 10-minute idle unload, the output names them, and
    nothing restarts it. oMLX's default guard refused the default model on
    a 16 GB Mac."""
    app = omlx_app(16)

    app.start()

    assert _app_settings(app) == ("custom", 9.0, True, 600)
    assert small_mac.state()[3] == []
    out = capsys.readouterr().out
    assert "9 GB" in out and "10 min" in out


def test_the_omlx_app_is_fitted_once(omlx_app):
    app = omlx_app(16)

    app.start()
    app.start()

    assert len(app.changes) == 1


def test_the_omlx_app_keeps_a_guard_and_an_idle_timeout_the_admin_chose(omlx_app):
    app = omlx_app(16, settings={
        "memory": {"memory_guard_tier": "aggressive", "memory_guard_custom_ceiling_gb": 0.0},
        "scheduler": {"chunked_prefill": False},
        "idle_timeout": {"idle_timeout_seconds": 3600}})

    app.start()

    assert app.changes == [{"chunked_prefill": True}]


def test_a_setting_the_app_applies_at_its_start_is_saved_and_said_so(omlx_app, small_mac,
                                                                      capsys):
    """Given an oMLX that saves the memory guard but applies it only at its
    own start, then the stack still sends it, restarts nothing, and says
    in one line which setting waits for the app's next start."""
    app = omlx_app(16, at_start=("memory_guard_tier", "memory_guard_custom_ceiling_gb"))

    app.start()

    assert _app_settings(app) == ("custom", 9.0, True, 600)
    assert small_mac.state()[3] == []
    waiting = [line for line in capsys.readouterr().out.splitlines() if "starts next" in line]
    assert len(waiting) == 1
    assert "9 GB" in waiting[0] and "prefill" not in waiting[0]


def test_an_omlx_on_another_machine_is_never_changed(omlx_app, monkeypatch):
    """The stand-in plays a server elsewhere: the unit lane reaches only
    this Mac, so its address is said not to be this Mac's, in the
    framework's module every loader of the AI stacklet shares."""
    monkeypatch.setattr("stack.ai.probe.on_this_mac", lambda _url: False)
    app = omlx_app(16)

    app.start()

    assert app.changes == []
    assert _app_settings(app) == ("balanced", 0.0, False, None)


def test_the_omlx_app_on_a_larger_mac_keeps_its_settings(omlx_app):
    app = omlx_app(32)

    app.start()

    assert app.changes == []
