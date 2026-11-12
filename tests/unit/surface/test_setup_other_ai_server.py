"""The AI step of /setup with an AI server the family already runs elsewhere.

Step 4 offers the oMLX app first and "Install oMLX for me instead" below
it. A third link, "Use another AI server", opens a small form: an address,
an optional API key, Connect. Connect does what `stack ai switch <address>
--key <key>` does in the terminal: it checks that the server answers,
picks its chat model and writes `[ai]` in `stack.toml`. The form exists
only on the Mac that runs famstack, as the oMLX app's key field does,
because the key would otherwise cross the Wi-Fi in the clear.

Driven through the tools server's FastAPI app as a browser opens it, with
the host API in the dashboard stand-in's "services" state
(`tests/dashboard_host.py`). The host API's `ai_switch` runs `stack ai
switch` for real on the stand-in's instance, against a stand-in for an
OpenAI-compatible server on localhost (`pytest-httpserver`), so nothing
reaches a real AI server.
"""

from __future__ import annotations

import re
import tomllib

import pytest

from .dsl import Page
from .setup_stand_in import on_this_mac, phone, stack_in_state, the_ai_answered

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

KEY = "ollama-key-of-the-simpsons"
CHAT = "qwen3:8b"
OTHER_CHAT = "llama3.2:3b"
SPEECH = "whisper-large-v3"
# An address on another machine of the home network, written into
# `stack.toml` for the ready step only; nothing ever connects to it.
REMOTE = "http://192.168.1.30:11434/v1"


# ── The instance ─────────────────────────────────────────────────────────

class Mac:
    """The Simpsons' Mac at the AI step, and the AI server elsewhere,
    stood in on localhost."""

    def __init__(self, host, root, server):
        self.host, self.root, self.server = host, root, server

    def toml(self) -> dict:
        return tomllib.loads((self.root / "stack.toml").read_text())

    def connects(self, *, key: str = KEY, address: str = "", model: str = "",
                 browser=None) -> tuple[int, dict]:
        """Connect as the form's Connect does."""
        body = {"address": address or self.server, "key": key}
        if model:
            body["model"] = model
        return (browser or on_this_mac()).posts("/dashboard/api/ai/switch", body)


@pytest.fixture
def mac(tmp_path, monkeypatch, httpserver):
    """A function that sets the Mac up: `mac(models=[...], ai="...")`.
    The stand-in server lists `models` only with `KEY`, and answers a
    short question, or with `computes` False fails it with a 500. An `ai`
    given here is one `stack ai switch` wrote, so its server answered the
    switch's question."""
    def enter(*, models=(CHAT,), ai: str = "", state: str = "services",
              computes: bool = True) -> Mac:
        root = tmp_path / "instance"
        root.mkdir(parents=True, exist_ok=True)
        (root / "stack.toml").write_text(
            f'[core]\ndata_dir = "{root / "data"}"\nhost = "localhost"\nprofile = "small"\n'
            f'extension_dirs = ["{root / "extensions"}"]\n\n[messages]\nserver_name = "simpson"\n'
            + ai)
        listed = {"data": [{"id": m} for m in models]}
        httpserver.expect_request(
            "/v1/models", headers={"Authorization": f"Bearer {KEY}"}).respond_with_json(listed)
        httpserver.expect_request("/v1/models").respond_with_data("unauthorized", status=401)
        if computes:
            httpserver.expect_request("/v1/chat/completions", method="POST").respond_with_json(
                {"choices": [{"message": {"role": "assistant", "content": "OK"}}]})
        else:
            httpserver.expect_request("/v1/chat/completions", method="POST").respond_with_json(
                {"detail": "CUDA error: no kernel image is available"}, status=500)
        host = stack_in_state(tmp_path, monkeypatch, httpserver)(state)
        if "openai_url" in ai:
            # `stack ai switch` asked the server when it wrote this `[ai]`.
            the_ai_answered(host)
        return Mac(host, root, httpserver.url_for("/v1"))
    return enter


def other_form(page: Page) -> bool:
    return 'class="setup-box ai-other"' in page.body


# ── The choice ───────────────────────────────────────────────────────────

def test_the_step_links_another_ai_server_below_the_terminal_route(mac):
    """Resting state: the step opens as before, with one more link."""
    mac()

    page = on_this_mac().opens("/setup?step=ai")

    hrefs = [href for _, href in page.links()]
    assert hrefs.index("/setup?step=ai&ai=install") < hrefs.index("/setup?step=ai&ai=other")
    assert not other_form(page)


def test_on_this_mac_the_choice_has_an_address_and_a_key(mac):
    mac()

    page = on_this_mac().opens("/setup?step=ai&ai=other")

    assert other_form(page)
    assert 'name="address"' in page.body
    assert 'type="password" name="api_key"' in page.body


def test_on_another_device_the_choice_has_no_fields(mac):
    """A key typed on a phone would cross the Wi-Fi in the clear, so the
    setup page answers a phone with the page that says where to go."""
    mac()

    page = phone().opens("/setup?step=ai&ai=other")

    assert page.status == 403 and "This happens on the Mac that runs famstack" in page.body
    assert 'name="address"' not in page.body and 'name="api_key"' not in page.body


def test_the_german_choice_is_worded_in_german(mac):
    mac()

    page = on_this_mac("de").opens("/setup?step=ai&ai=other")

    assert "Adresse" in page.body and "Verbinden" in page.body


# ── Connect ──────────────────────────────────────────────────────────────

def test_connect_writes_the_server_and_its_chat_model(mac):
    """Given a server that answers with the key, when Connect posts its
    address and key from this Mac, then `stack.toml` names that server,
    the key and the chat model it lists, as `stack ai switch` writes them."""
    m = mac()

    status, answer = m.connects()

    assert status == 200, answer
    assert answer["model"] == CHAT
    ai = m.toml()["ai"]
    assert ai["provider"] == "external"
    assert ai["openai_url"] == m.server
    assert ai["openai_key"] == KEY
    assert ai["default"] == CHAT


def test_a_refused_key_says_so_and_changes_nothing(mac):
    m = mac()
    before = m.toml()

    status, answer = m.connects(key="wrong")

    assert status == 422 and answer["reason"] == "key", answer
    assert m.toml() == before


def test_a_server_that_does_not_answer_says_so_and_changes_nothing(mac):
    m = mac()
    before = m.toml()

    status, answer = m.connects(address="http://127.0.0.1:9/v1")

    assert status == 422 and answer["reason"] == "unreachable", answer
    assert m.toml() == before


def test_several_models_are_listed_to_choose_from(mac):
    """A server with several chat models: the answer lists them, and the
    one chosen is written."""
    m = mac(models=(CHAT, OTHER_CHAT))

    status, answer = m.connects()
    assert status == 422 and answer["reason"] == "model", answer
    assert answer["models"] == [CHAT, OTHER_CHAT]
    assert "ai" not in m.toml()

    status, answer = m.connects(model=OTHER_CHAT)
    assert status == 200, answer
    assert m.toml()["ai"]["default"] == OTHER_CHAT


def test_a_speech_model_on_the_server_takes_the_voice_messages(mac):
    m = mac(models=(CHAT, SPEECH))

    status, answer = m.connects()

    assert status == 200, answer
    assert m.toml()["ai"]["whisper_model"] == SPEECH
    assert answer["whisper_model"] == SPEECH


def test_from_another_device_connect_is_refused(mac):
    m = mac()

    status, _ = m.connects(browser=phone())

    assert status == 403
    assert "ai" not in m.toml()


def test_the_key_never_goes_on_the_command_line(mac):
    """A command line is visible to every process on the Mac: the host API
    gets the key in its own field, never inside the arguments."""
    m = mac()

    m.connects()

    asked = [r for r in m.host.asked if r.get("cmd") == "ai_switch"]
    assert asked and asked[0]["key"] == KEY
    assert all(KEY not in str(v) for k, v in asked[0].items() if k != "key")


# ── Ready ────────────────────────────────────────────────────────────────

def _remote(*, speech: bool) -> str:
    return (f'\n[ai]\nprovider = "external"\nopenai_url = "{REMOTE}"\nopenai_key = "{KEY}"\n'
            f'default = "{CHAT}"\n' + (f'whisper_model = "{SPEECH}"\n' if speech else ""))


def test_a_server_elsewhere_is_ready_with_its_chat_model(mac):
    mac(ai=_remote(speech=True))

    page = on_this_mac().opens("/setup?step=ai")

    assert "Your AI is ready" in page.body
    assert CHAT in page.texts("ai-other-model")[0]
    assert re.findall(r'<li data-status="(\w+)">', page.body) == ["done", "done"]


def test_without_a_speech_model_the_step_says_voice_needs_a_speech_server(mac):
    mac(ai=_remote(speech=False))

    page = on_this_mac().opens("/setup?step=ai")

    assert re.findall(r'<li data-status="(\w+)">', page.body) == ["done", "off"]
    assert any("--whisper" in text or "admin-guide" in href
               for text, href in page.links("ai-can"))


def test_a_server_elsewhere_does_not_play_the_first_words(mac):
    """The first words speak in the house voice, which comes with the
    stack's own AI; a server elsewhere skips it."""
    mac(ai=_remote(speech=True) + "voice = true\n")

    page = on_this_mac().opens("/setup?step=ai")

    assert "Your AI is ready" in page.body
    assert "first-words" not in page.body


def test_a_server_outside_the_home_network_says_where_the_family_data_goes(mac):
    """example.com's public address, only written into `stack.toml`;
    the page reads it, nothing connects to it."""
    mac(ai=_remote(speech=True).replace(REMOTE, "https://93.184.215.14/v1"))

    page = on_this_mac().opens("/setup?step=ai")

    assert page.texts("ai-leaves-home")


# ── A server on this Mac ─────────────────────────────────────────────────

def _on_this_mac(url: str) -> str:
    return f'\n[ai]\nprovider = "external"\nopenai_url = "{url}"\ndefault = "{CHAT}"\n'


def test_a_server_on_this_mac_at_another_port_is_ready(mac, httpserver):
    """Ollama on this Mac reads as soon as `[ai]` names it: nothing of the
    AI stacklet has to be set up first. It answers, and has no oMLX admin
    API, so it is not the oMLX app."""
    httpserver.expect_request("/admin/api/models").respond_with_data("404 page not found",
                                                                      status=404)
    mac(ai=_on_this_mac(httpserver.url_for("/v1")))

    page = on_this_mac().opens("/setup?step=ai")

    assert "Your AI is ready" in page.body
    assert CHAT in page.texts("ai-other-model")[0]


def test_connecting_a_server_on_this_mac_ends_ready(mac):
    """The whole route: Connect with the stand-in on localhost, then the
    step reads again and shows "Your AI is ready"."""
    m = mac()

    status, _ = m.connects()
    page = on_this_mac().opens("/setup?step=ai")

    assert status == 200
    assert "Your AI is ready" in page.body


def test_connecting_a_server_that_cannot_answer_says_so(mac):
    """Given a server elsewhere lists its models and fails every answer,
    when Connect runs `stack ai switch`, then the switch asks it one short
    question, and the AI step says the AI does not answer, with the
    server's error. Its way to ask again is "Use another AI server": no
    ai stacklet is set up whose run could ask."""
    m = mac(computes=False)

    status, _ = m.connects()
    page = on_this_mac().opens("/setup?step=ai")

    assert status == 200
    assert "Your AI does not answer" in page.text
    assert "CUDA error" in " ".join(page.texts("ai-error"))
    assert 'data-target="ai"' not in page.body
    assert any(href == "/setup?step=ai&ai=other" for _, href in page.links("ai-no-answer"))


def test_the_omlx_app_still_waits_for_the_ai_stacklet(mac, httpserver):
    """The oMLX app reads only once the AI stacklet is set up, because its
    first start puts the models there. The app is an oMLX on this Mac,
    whatever its port: it answers as oMLX does."""
    httpserver.expect_request("/admin/api/models").respond_with_json(
        {"detail": "Admin authentication required"}, status=401)
    httpserver.expect_request("/openapi.json").respond_with_json(
        {"openapi": "3.1.0", "info": {"title": "oMLX API", "version": "0.7.3"}})
    mac(ai=_on_this_mac(httpserver.url_for("/v1")))

    page = on_this_mac().opens("/setup?step=ai")

    assert "Your AI is ready" not in page.body
