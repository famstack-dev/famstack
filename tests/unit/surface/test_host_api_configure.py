"""The host API answers a stacklet's setup forms for the tools server.

`{"cmd": "configure", "stacklet": "<id>", "answers": {...}}` runs
`stack configure <id> --json --answers -` and answers with the next form,
the same form with an error under a field, or `{"ready": true}` once the
answers are applied. The answers reach the CLI on stdin, never on its
command line: one of them may be a secret, and a command line is visible
to every process on the Mac.

Driven through the API's own connection handler (`handle_client`), one end
of a socket pair standing in for the container's connection. `STACK_BIN`
points at a wrapper that notes what it was called with and what came on
stdin, then runs the real CLI of this checkout on an instance in
`tmp_path`, whose extension stacklet `quiz` asks two forms: a colour to
choose, then a code, which is a secret.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import socket
import sys
import textwrap
import threading
import tomllib

import pytest

from .conftest import LIB, STACKLETS

WRAPPER = textwrap.dedent('''\
    #!{python}
    """./stack as the API calls it: notes argv and stdin, then runs the real CLI."""
    import json, os, subprocess, sys
    from pathlib import Path

    here = Path(__file__).parent
    body = sys.stdin.read()
    (here / "called").write_text(json.dumps({{"argv": sys.argv[1:], "stdin": body}}))
    env = {{**os.environ, "PYTHONPATH": {lib!r}, "STACK_DIR": {instance!r},
           "HOME": {home!r}, "PYTHONDONTWRITEBYTECODE": "1"}}
    run = subprocess.run([sys.executable, "-m", "stack", *sys.argv[1:]], input=body,
                         capture_output=True, text=True, env=env)
    sys.stdout.write(run.stdout)
    sys.stderr.write(run.stderr)
    sys.exit(run.returncode)
''')

QUIZ_HOOK = textwrap.dedent('''\
    from stack.forms import Form

    COLOUR = Form(id="quiz.colour", title="Colour", schema={
        "type": "object", "required": ["colour"],
        "properties": {"colour": {"type": "string", "title": "Colour", "enum": ["red", "blue"]}}})
    CODE = Form(id="quiz.code", title="Code", schema={
        "type": "object", "required": ["code"],
        "properties": {"code": {"type": "string", "title": "Code", "writeOnly": True,
                                "minLength": 1}}})

    def questions(ctx, answers):
        if ctx.cfg("colour", default=""):
            return None
        return COLOUR.pending(answers) or CODE.pending(answers)

    def apply(ctx, answers):
        ctx.cfg("colour", answers["colour"])
        ctx.secret("CODE", answers["code"])
''')


@pytest.fixture
def instance(tmp_path):
    root = tmp_path / "instance"
    (root / ".stack").mkdir(parents=True)
    quiz = tmp_path / "extensions" / "quiz"
    (quiz / "hooks").mkdir(parents=True)
    (quiz / "stacklet.toml").write_text('id = "quiz"\nname = "Quiz"\n')
    (quiz / "hooks" / "on_configure.py").write_text(QUIZ_HOOK)
    (root / "stack.toml").write_text(
        f'[core]\nname = "famstack"\ndata_dir = "{tmp_path / "data"}"\n'
        f'extension_dirs = ["{tmp_path / "extensions"}"]\n')
    return root


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


def called(tmp_path) -> dict:
    return json.loads((tmp_path / "called").read_text())


def configure(api, **answers) -> dict:
    return ask(api, {"cmd": "configure", "stacklet": "quiz", "answers": answers})


def test_the_first_form_comes_back_for_no_answers(api, tmp_path):
    reply = configure(api)

    assert reply["form"]["id"] == "quiz.colour"
    assert called(tmp_path)["argv"] == ["configure", "quiz", "--json", "--answers", "-"]
    assert json.loads(called(tmp_path)["stdin"]) == {}


def test_a_wrong_answer_brings_the_same_form_back_with_the_reason(api):
    reply = configure(api, colour="green")

    assert reply["form"]["id"] == "quiz.colour"
    assert "colour" in reply["form"]["errors"]


def test_each_answer_leads_to_the_next_form(api):
    assert configure(api, colour="red")["form"]["id"] == "quiz.code"


def test_the_last_answer_applies_them_and_says_ready(api, tmp_path, instance):
    reply = configure(api, colour="red", code="s3cret")

    assert reply == {"ready": True}
    with open(instance / "stack.toml", "rb") as f:
        assert tomllib.load(f)["quiz"]["colour"] == "red"
    assert "s3cret" in (instance / ".stack" / "secrets.toml").read_text()
    # The secret went on stdin, never on the command line.
    assert "s3cret" not in " ".join(called(tmp_path)["argv"])


def test_a_stacklet_must_be_named(api, tmp_path):
    reply = ask(api, {"cmd": "configure", "answers": {}})

    assert "stacklet" in reply["error"]
    assert not (tmp_path / "called").exists()
