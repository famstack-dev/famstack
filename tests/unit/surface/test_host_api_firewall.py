"""The host API says whether the Mac's firewall keeps phones away from the chat.

`{"cmd": "firewall"}` runs `stack core firewall --json` on the Mac and
answers with `{"on": ..., "blocks": ...}`: `blocks` names what keeps
phones out ("block-all", "orbstack", "signed"), "" when nothing does. A
state that cannot be read answers with an error, which the setup page
shows nothing for.

Driven through the API's own connection handler (`handle_client`), one end
of a socket pair standing in for the container's connection. The API runs
`./stack` as a separate process: `STACK_BIN` is a wrapper that runs the
real CLI of this checkout on an instance in `tmp_path`. macOS's
`socketfilterfw` is a stand-in first on `PATH`, answering with the
sentences the real tool prints for a chosen state.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import socket
import sys
import textwrap
import threading

import pytest

from .conftest import LIB, STACKLETS

WRAPPER = textwrap.dedent('''\
    #!{python}
    """./stack as the API calls it: the real CLI on the test's instance."""
    import os, subprocess, sys

    env = {{**os.environ, "PYTHONPATH": {lib!r}, "STACK_DIR": {instance!r},
           "HOME": {home!r}, "PYTHONDONTWRITEBYTECODE": "1"}}
    run = subprocess.run([sys.executable, "-m", "stack", *sys.argv[1:]],
                         capture_output=True, text=True, env=env)
    sys.stdout.write(run.stdout)
    sys.stderr.write(run.stderr)
    sys.exit(run.returncode)
''')

# macOS's socketfilterfw: answers each --get question from `answers.json`
# beside it, and fails a question that has no answer there.
SOCKETFILTERFW = textwrap.dedent('''\
    #!{python}
    import json, sys
    from pathlib import Path

    answers = json.loads((Path(__file__).parent / "answers.json").read_text())
    if sys.argv[1] not in answers:
        sys.exit(1)
    print(answers[sys.argv[1]])
''')

ON = "Firewall is enabled. (State = 1)"
OFF = "Firewall is disabled. (State = 0)"
BLOCK_ALL_ON = "Firewall has block all state set to enabled."
BLOCK_ALL_OFF = "Firewall has block all state set to disabled."
SIGNED_ON = ("Automatically allow built-in signed software ENABLED.\n"
             "Automatically allow downloaded signed software ENABLED.")
SIGNED_OFF = ("Automatically allow built-in signed software ENABLED.\n"
              "Automatically allow downloaded signed software DISABLED.")
PERMITTED = "Incoming connection to /Applications/OrbStack.app is permitted."
BLOCKED = "Incoming connection to /Applications/OrbStack.app is blocked."


def firewall(state=ON, block_all=BLOCK_ALL_OFF, signed=SIGNED_ON, orbstack=PERMITTED) -> dict:
    """The tool's answers for one state; the defaults let the chat in."""
    return {"--getglobalstate": state, "--getblockall": block_all,
            "--getallowsigned": signed, "--getappblocked": orbstack}


@pytest.fixture
def instance(tmp_path):
    root = tmp_path / "instance"
    (root / ".stack").mkdir(parents=True)
    (root / "stack.toml").write_text(f'[core]\nname = "famstack"\ndata_dir = "{tmp_path / "data"}"\n')
    return root


@pytest.fixture
def mac(tmp_path, monkeypatch):
    """Sets what the stand-in socketfilterfw answers."""
    tools = tmp_path / "tools"
    tools.mkdir()
    tool = tools / "socketfilterfw"
    tool.write_text(SOCKETFILTERFW.format(python=sys.executable))
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}:/usr/bin:/bin")

    def answers(given: dict) -> None:
        (tools / "answers.json").write_text(json.dumps(given))
    return answers


@pytest.fixture
def api(tmp_path, instance, monkeypatch):
    """The host API module, loaded from its file, with `./stack` pointed at the wrapper."""
    path = STACKLETS / "core" / "famstack-api.py"
    loader = importlib.machinery.SourceFileLoader("famstack_api", str(path))
    spec = importlib.util.spec_from_file_location("famstack_api", path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    wrapper = tmp_path / "stack"
    wrapper.write_text(WRAPPER.format(python=sys.executable, lib=str(LIB), instance=str(instance),
                                      home=str(tmp_path / "home")))
    wrapper.chmod(0o755)
    monkeypatch.setattr(module, "STACK_BIN", wrapper)
    return module


def ask(api, request: dict) -> dict:
    """Send one request the way a container does and read the one-line reply."""
    ours, theirs = socket.socketpair()
    threading.Thread(target=api.handle_client, args=(theirs,), daemon=True).start()
    ours.sendall((json.dumps(request) + "\n").encode())
    with ours.makefile("r", encoding="utf-8") as reply:
        line = reply.readline()
    ours.close()
    return json.loads(line)


@pytest.mark.parametrize("answers, blocks", [
    (firewall(block_all=BLOCK_ALL_ON), "block-all"),
    (firewall(orbstack=BLOCKED), "orbstack"),
    (firewall(signed=SIGNED_OFF), "signed"),
    # Block all wins: allowing OrbStack does not help while it is on.
    (firewall(block_all=BLOCK_ALL_ON, orbstack=BLOCKED), "block-all"),
])
def test_a_firewall_that_keeps_phones_out_names_what_blocks(api, mac, answers, blocks):
    """Given the firewall is on and blocks all, blocks OrbStack, or does
    not allow downloaded signed apps, the answer names that block, so the
    setup page can give its fix."""
    mac(answers)

    assert ask(api, {"cmd": "firewall"}) == {"on": True, "blocks": blocks}


@pytest.mark.parametrize("answers, on", [
    (firewall(state=OFF, block_all=BLOCK_ALL_ON, orbstack=BLOCKED), False),
    (firewall(), True),
])
def test_a_firewall_that_is_off_or_lets_orbstack_in_blocks_nothing(api, mac, answers, on):
    """Given the firewall is off (whatever its other settings), or on and
    letting OrbStack in, nothing blocks."""
    mac(answers)

    assert ask(api, {"cmd": "firewall"}) == {"on": on, "blocks": ""}


@pytest.mark.parametrize("answers", [
    {},                                                     # the tool fails every question
    firewall(state="The firewall answered in Klingon."),    # words it does not know
])
def test_a_state_that_cannot_be_read_is_an_error(api, mac, answers):
    """Given the tool fails or answers in words the stack does not know,
    the answer is an error and names no block."""
    mac(answers)

    reply = ask(api, {"cmd": "firewall"})

    assert "error" in reply
    assert "blocks" not in reply


def test_discover_lists_the_firewall(api):
    assert "firewall" in ask(api, {"cmd": "discover"})["commands"]
