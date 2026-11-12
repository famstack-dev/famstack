"""The AI's first words on /setup (step 5f), as the admin meets them.

Once the AI runs with speech on, "Your AI is ready" is followed by "Your
Mac learned to speak" and "▶ Play". Play asks the tools server for the
welcome, Yes for the AI's introduction; each comes back as the words and
their audio in famstack's own voice, made by the stack's speech server,
so a browser on any device plays it. With speech off the step is not
there and "Your AI is ready" leads on by itself.

Driven through the tools server's FastAPI app as a browser opens it, with
the host API in the dashboard stand-in's "done" state
(`tests/dashboard_host.py`). The first words run for real on the
stand-in's instance, as `./stack ai hello --audio` and `./stack ai intro
--audio`, against one AI server stood in on localhost: the oMLX app's
admin API (`omlx_stand_in.py`), its speech endpoint answering fixed bytes
and its chat endpoint a fixed introduction. Nothing plays a sound: the
page's script does the playing, and no test runs it.
"""

from __future__ import annotations

import base64
import importlib
import json
import re

import pytest

from tests.unit.stacklets.omlx_stand_in import KEY, OMLX

from .setup_stand_in import on_this_mac, stack_in_state

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

AUDIO = b"ID3 famstack's own voice, not really an mp3"
INTRODUCTION = ("Good evening, Homer. It's Monday, 9 November, 20:58. I'm famstack's AI, "
                "on your Mac with 16 GB, and I'm ready to read your family's letters.")
NEXT = "/setup?step=services"


@pytest.fixture
def ai(tmp_path, monkeypatch, httpserver):
    """A function that sets the Simpsons' AI up as running, connected to
    the oMLX app: `ai(speech=True)`. With `speaks=False` the speech
    endpoint fails. Returns the AI server, to read what it was asked."""
    running_server = importlib.import_module("stacklets.ai.running_server")

    def enter(*, speech: bool, speaks: bool = True):
        root = tmp_path / "instance"
        root.mkdir(parents=True, exist_ok=True)
        (root / "stack.toml").write_text(
            f'[core]\ndata_dir = "{root / "data"}"\nhost = "localhost"\n'
            f'extension_dirs = ["{root / "extensions"}"]\n\n[messages]\nserver_name = "simpson"\n\n'
            f'[ai]\nprovider = "external"\nlanguage = "en"\n'
            f'openai_url = "{httpserver.url_for("/v1")}"\nopenai_key = "{KEY}"\n'
            f'speech = {"true" if speech else "false"}\n')
        stack_in_state(tmp_path, monkeypatch, httpserver)("done")
        OMLX(httpserver, wants_key=True)
        if speaks:
            httpserver.expect_request("/v1/audio/speech", method="POST").respond_with_data(
                AUDIO, content_type="audio/mpeg")
        else:
            httpserver.expect_request("/v1/audio/speech").respond_with_data("busy", status=500)
        httpserver.expect_request("/v1/chat/completions", method="POST").respond_with_json(
            {"choices": [{"message": {"content": INTRODUCTION}}]})
        monkeypatch.setattr(running_server, "RUNNING_SERVERS", (httpserver.url_for("/v1"),))
        return httpserver
    return enter


def spoken(server) -> list[str]:
    """The texts the speech server was asked to speak, in order."""
    return [json.loads(request.data)["input"] for request, _ in server.log
            if request.path == "/v1/audio/speech"]


def after_first_words(body: str) -> str:
    """The page after step 5f's section, "" when there is none."""
    _, found, rest = body.partition('<section class="first-words"')
    return rest.split("</section>", 1)[1] if found else ""


# ── The step ─────────────────────────────────────────────────────────────

def test_with_speech_on_the_ready_ai_offers_its_first_words(ai):
    """Given the AI runs with speech on, when the admin opens the AI step,
    then "Your AI is ready" is followed by the first words with Play, and
    the way on comes after them."""
    ai(speech=True)

    page = on_this_mac().opens("/setup?step=ai")

    assert 'class="ai-can"' in page.body
    # The ready step is the oMLX app's, not a server elsewhere's.
    assert "ai-other-model" not in page.body
    assert re.findall(r'data-act="(\w+)"', page.body) == ["play", "yes", "again"]
    assert NEXT in after_first_words(page.body)


def test_with_speech_off_there_are_no_first_words(ai):
    """Given speech is off, the AI has no voice: no first words, and the
    ready AI leads on by itself."""
    ai(speech=False)

    page = on_this_mac().opens("/setup?step=ai")

    assert 'class="ai-can"' in page.body
    assert 'class="first-words"' not in page.body
    assert f'href="{NEXT}"' in page.body


# ── The voice ────────────────────────────────────────────────────────────

def test_play_gets_the_welcome_in_famstacks_own_voice(ai):
    """When Play is clicked on the Mac, the tools server hands it the audio
    the stack's speech server made, and the words it spoke. Through the home
    address the first words run nothing (`test_changes_on_the_mac.py`)."""
    server = ai(speech=True)

    status, answer = on_this_mac().posts("/dashboard/api/ai/first-words/welcome", {})

    assert status == 200, answer
    assert base64.b64decode(answer["audio"]) == AUDIO
    assert spoken(server) == [answer["text"]]


def test_yes_gets_the_introduction_in_the_ais_own_words(ai):
    """After Yes, the AI introduces itself: the language model's words,
    which the page shows as a quote, and their audio in the same voice."""
    server = ai(speech=True)

    status, answer = on_this_mac().posts("/dashboard/api/ai/first-words/intro", {})

    assert status == 200, answer
    assert answer["text"] == INTRODUCTION
    assert spoken(server) == [INTRODUCTION]
    assert base64.b64decode(answer["audio"]) == AUDIO


def test_a_voice_that_does_not_start_says_why(ai):
    """A speech server that fails gives the page an error to show, and no
    audio, so the page never plays silence as if it were the voice."""
    ai(speech=True, speaks=False)

    status, answer = on_this_mac().posts("/dashboard/api/ai/first-words/welcome", {})

    assert status == 503
    assert answer["error"] and "audio" not in answer


def test_only_the_welcome_and_the_introduction_are_spoken(ai):
    server = ai(speech=True)

    status, _ = on_this_mac().posts("/dashboard/api/ai/first-words/anything", {})

    assert status == 404
    assert spoken(server) == []


def test_the_stack_that_does_not_answer_is_a_503(ai, monkeypatch):
    ai(speech=True)
    from stack.live import client
    monkeypatch.setattr(client, "call", lambda request, timeout: {"error": "no answer"})

    status, answer = on_this_mac().posts("/dashboard/api/ai/first-words/intro", {})

    assert status == 503 and answer == {"error": "no answer"}
