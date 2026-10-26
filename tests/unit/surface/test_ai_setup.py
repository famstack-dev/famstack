"""The ai stacklet's setup question, before its first `stack up ai`.

On a fresh instance it asks whether to set up oMLX, the engine the stack
runs on this Mac; a no installs nothing and names `stack ai switch` for a
server elsewhere. After `stack ai switch <url>`, installing the stacklet
can mean two setups, so it asks whether the engine here takes over: yes
moves chat and voice onto it, no keeps that server for chat and installs
only the voice services, the setup of a Mac that serves its own AI app.
An installed stacklet asks nothing; its engine is chosen with
`stack ai switch`.

These tests run `./stack configure ai` in this process. Homebrew is stood
in by an empty `brew` first on PATH, since a yes to oMLX needs it.
"""

from __future__ import annotations

import pytest

from .conftest import StackCLI, write_instance

REMOTE = ('[ai]\nprovider = "external"\nopenai_url = "https://ai.example.test/v1"\n'
          'openai_key = "sk-test"\nwhisper_url = "https://stt.example.test/v1"\n'
          'whisper_key = "stt-key"\ndefault = "gpt-4.1-mini"\n')


@pytest.fixture
def brew(tmp_path, monkeypatch):
    """A Mac with Homebrew: an empty `brew` first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "brew").write_text("#!/bin/sh\n")
    (bin_dir / "brew").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")


def _instance(tmp_path, monkeypatch, ai: str = "") -> StackCLI:
    """An instance with this `[ai]`. The home directory is the test's: a
    run that got as far as installing would write oMLX's settings and a
    Whisper service there."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return StackCLI(write_instance(tmp_path, extra=ai), monkeypatch)


@pytest.fixture
def switched(tmp_path, monkeypatch, as_stack_runs_it) -> StackCLI:
    """An instance pointed at a remote AI server with `stack ai switch`."""
    return _instance(tmp_path, monkeypatch, REMOTE)


@pytest.fixture
def fresh(tmp_path, monkeypatch, as_stack_runs_it, brew) -> StackCLI:
    """An instance with no `[ai]` yet, on a Mac with Homebrew."""
    return _instance(tmp_path, monkeypatch)


# ── After `stack ai switch` ──────────────────────────────────────────────

def test_the_question_names_a_remote_server(switched):
    form = switched("configure", "ai", "--json").answer["form"]

    assert form["id"] == "ai.engine"
    assert "a remote AI server (https://ai.example.test/v1)" in form["text"]


def test_a_server_on_this_mac_is_not_called_remote(tmp_path, monkeypatch, as_stack_runs_it):
    local = _instance(tmp_path, monkeypatch,
                      REMOTE.replace("https://ai.example.test/v1", "http://localhost:8888/v1"))

    text = local("configure", "ai", "--json").answer["form"]["text"]

    assert "on this Mac that the stack does not manage (http://localhost:8888/v1)" in text
    assert "remote" not in text


def test_no_keeps_the_server_and_goes_on_to_install_voice(switched):
    """The install that follows skips the engine for a server set with
    switch, and installs speech-to-text and text-to-speech."""
    before = switched.toml()

    ran = switched("configure", "ai", "--json", "--answer", "use_this_mac=no")

    assert ran.answer == {"ready": True}
    assert switched.toml() == before


def test_no_is_the_default(switched, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    before = switched.toml()

    ran = switched("configure", "ai", terminal=True)

    assert ran.code == 0, ran.err
    assert switched.toml() == before


def test_yes_switches_chat_and_voice_to_this_mac_together(switched):
    """Never one half remote and the other local."""
    switched("configure", "ai", "--json", "--answer", "use_this_mac=yes")

    toml = switched.toml()
    assert 'provider = "managed"' in toml
    assert 'openai_url = "http://localhost:42060/v1"' in toml
    assert 'whisper_url = "http://localhost:42062/v1"' in toml
    assert 'whisper_key = ""' in toml
    assert 'openai_key = "local"' in toml


def test_without_an_answer_nothing_is_installed_and_the_form_is_named(switched):
    """Nobody to answer: an agent or a script installing the stacklet must
    not change the family's AI setup."""
    before = switched.toml()

    ran = switched("up", "ai")

    assert ran.code == 1
    assert "ai.engine" in ran.err and "use_this_mac" in ran.err
    assert switched.toml() == before


def test_an_installed_stacklet_asks_nothing(switched):
    """Its engine is chosen with `stack ai switch` from then on."""
    (switched.instance / ".stack" / "ai.setup-done").write_text("")

    ran = switched("configure", "ai")

    assert ran.code == 0
    assert "Nothing to ask" in ran.out
    assert "sk-test" not in ran.out


# ── On a fresh instance ──────────────────────────────────────────────────

def test_a_fresh_instance_asks_whether_to_set_up_omlx(fresh):
    form = fresh("configure", "ai", "--json").answer["form"]

    assert form["id"] == "ai.omlx"
    assert form["schema"]["properties"]["setup_omlx"]["default"] is True


def test_yes_sets_up_the_engine_on_this_mac(fresh):
    assert fresh("configure", "ai", "--json", "--answer", "setup_omlx=yes").answer == {"ready": True}

    toml = fresh.toml()
    assert 'provider = "managed"' in toml
    assert 'openai_url = "http://localhost:42060/v1"' in toml


def test_no_cancels_and_names_the_command_for_a_server_elsewhere(fresh):
    ran = fresh("configure", "ai", "--answer", "setup_omlx=no")

    assert ran.code == 1
    assert "Cancelled" in ran.err and "stack ai switch <url>" in ran.err
    assert "provider" not in fresh.toml()


def test_yes_without_homebrew_says_how_to_install_it(tmp_path, monkeypatch, as_stack_runs_it):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    no_brew = _instance(tmp_path, monkeypatch)

    ran = no_brew("configure", "ai", "--answer", "setup_omlx=yes")

    assert ran.code == 1
    assert "Homebrew" in ran.err
    assert "provider" not in no_brew.toml()
