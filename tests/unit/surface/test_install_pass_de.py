"""The install with the stack in German: the setup's steps, labels and
the bots' welcomes as the admin meets them.

Each test pins one behaviour an install with the stack's language German
showed: the step "Chat on this Mac" that goes on by itself after the
sign-in, the bots' welcomes in the stack's language, the oMLX download and
labels of the AI step, the command the admin types, what Copy copies, and
an oMLX installed with Homebrew.

The setup page is driven through the tools server's FastAPI app as a
browser on the Mac opens it (the Browser DSL), with the host API stood in
at the client's edge (`setup_stand_in.py`). How the open page acts on the
watch in a real browser is in `tests/integration/test_setup_in_the_browser.py`.
"""

from __future__ import annotations

import importlib
import re

import pytest

from .dsl import Page
from .setup_stand_in import on_this_mac, stack_in_state

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")


@pytest.fixture
def instance(tmp_path, monkeypatch, httpserver):
    """Put the Simpsons' stack in a named state: `instance("chat")`."""
    return stack_in_state(tmp_path, monkeypatch, httpserver)


def step_part(page) -> Page:
    """The main column's step, read on its own."""
    found = re.search(r'<section class="setup-step".*?</section>', page.body, re.S)
    return Page(200, "text/html", found[0] if found else "")


def watch_of(page) -> str:
    """The address the waiting step asks whether the admin signed in."""
    found = re.search(r'data-watch="([^"]+)"', step_part(page).body)
    return found[1] if found else ""


# ── 1. The chat on this Mac goes on by itself ────────────────────────────

def test_the_waiting_mac_step_learns_of_the_sign_in_without_a_reload(instance):
    """Given the step "Chat on this Mac" waits for the admin's sign-in,
    when the admin signs in to the chat, then the address the open page
    watches answers that the admin signed in, and the page read again
    shows the step done."""
    host = instance("chat")
    mac = on_this_mac("de")
    page = mac.opens("/setup")
    watch = watch_of(page)

    assert "Warten auf deine Anmeldung" in step_part(page).text
    assert watch, "the waiting step names no address to watch"
    assert mac.reads(watch) == (200, {"signed_in": False})

    host.signed_in.add("homer")

    assert mac.reads(watch) == (200, {"signed_in": True})
    again = mac.opens("/setup?step=mac")
    assert watch_of(again) == ""
    assert "ist an diesem Mac angemeldet" in step_part(again).text


def test_the_waiting_mac_step_offers_to_go_on_at_once(instance):
    """Given the step waits for the sign-in, then it has a button that
    leads to the phone's step at once, in the stack's language."""
    instance("chat")
    german = step_part(on_this_mac("de").opens("/setup"))
    english = step_part(on_this_mac("en").opens("/setup"))

    assert ("Ich bin angemeldet. Weiter", "/setup?step=phone") in german.links()
    assert ("I'm signed in. Continue", "/setup?step=phone") in english.links()



# ── 2. The bots' welcomes in the stack's language ────────────────────────

def stack_in(tmp_path, language: str):
    """The Simpsons' stack with `[core] language` set to `language`."""
    from tests.springfield import make_stack, write_instance
    instance = write_instance(tmp_path)
    toml = instance / "stack.toml"
    toml.write_text(toml.read_text().replace('language = "en"', f'language = "{language}"'))
    return make_stack(instance)


def server_room_after(stck, monkeypatch, result: dict) -> list[str]:
    """What the Server room gets after `stack up` reports `result`. The
    welcome is posted by `./stack messages send`, a process of its own;
    the test reads what would have been sent at that boundary."""
    from stack import cli
    sent: list[str] = []
    monkeypatch.setattr(cli, "_notify", lambda _stck, message: sent.append(message))
    cli._notify_up(stck, result)
    return sent


@pytest.mark.parametrize("stacklet, german, english", [
    ("photos", "Fotos läuft!", "Photos is live!"),
    ("docs", "Dokumente läuft!", "Documents is live!"),
    ("ai", "Die KI läuft!", "AI is live!"),
    ("chatai", "ChatAI läuft!", "ChatAI is live!"),
])
def test_a_stacklets_first_start_is_welcomed_in_the_stacks_language(tmp_path, monkeypatch,
                                                                    stacklet, german, english):
    """Given a stacklet started for the first time, then its welcome in
    the Server room is German on a German stack and English on an
    English one."""
    first = {"stacklet": stacklet, "name": stacklet, "first_run": True, "port": 42010}

    [de] = server_room_after(stack_in(tmp_path / "de", "de"), monkeypatch, first)
    [en] = server_room_after(stack_in(tmp_path / "en", "en"), monkeypatch, first)

    assert german in de and english not in de
    assert english in en


def test_a_stacklet_back_online_is_said_in_the_stacks_language(tmp_path, monkeypatch):
    """Given a stacklet started again, then the Server room's line is in
    the stack's language."""
    again = {"stacklet": "photos", "name": "Fotos", "first_run": False}

    assert server_room_after(stack_in(tmp_path, "de"), monkeypatch, again) == ["Fotos läuft wieder."]


class _Bot:
    """The stacker bot at the chat's edge: notes what it would send."""

    def __init__(self):
        self.sent: dict[str, str] = {}

    def send(self, room, plain, html=None):
        self.sent[room] = plain
        return True, ""


@pytest.mark.parametrize("language, server_room, memories", [
    ("de", "Der Server der Familie Simpson läuft.", "Das ist die Erinnerungssammlung eurer Familie."),
    ("en", "The Simpsons' server is live.", "This is your family's memory collection."),
])
def test_the_first_messages_of_the_server_and_memories_rooms_are_in_the_stacks_language(
        language, server_room, memories):
    """Given the chat is set up, when the stacker bot posts its welcomes
    in the Server room and the Memories room, then both are in the stack's
    language."""
    from stacklets.messages.cli import setup
    bot = _Bot()

    setup._post_welcome_messages(bot, {"famstack": "!server", "memories": "!memories"}, "simpson",
                                 {"stack": {"core": {"stack_owner": "Simpson", "language": language}}})

    assert bot.sent["famstack"].startswith(server_room)
    assert "./stack up docs" in bot.sent["famstack"]
    assert bot.sent["memories"].startswith(memories)

# ── 3. The AI step's link to oMLX ──────────────

def test_the_ai_steps_link_to_get_omlx_downloads_the_tested_disk_image(instance, monkeypatch):
    """Given no oMLX answers on a Mac on macOS 15, when the admin opens the
    AI step, then "oMLX herunterladen" downloads the disk image of the
    tested oMLX release built for macOS 15, from oMLX's GitHub releases."""
    instance("signed")
    omlx_app = importlib.import_module("stacklets.ai.omlx_app")
    monkeypatch.setattr(omlx_app, "this_macos", lambda: "15.5")
    v = ".".join(map(str, omlx_app.TESTED_OMLX))
    step = step_part(on_this_mac("de").opens("/setup?step=ai"))

    assert ("oMLX herunterladen", f"https://github.com/jundot/omlx/releases/download/v{v}/"
            f"oMLX-{v}-macos15-sequoia.dmg") in step.links()


# ── 4. The button after the download ─────────────────────────────────────

def test_the_button_after_the_download_says_omlx_is_installed(instance):
    """Given the AI step offers the download, then the button beside it
    says that oMLX is installed, and reads the step again."""
    instance("signed")
    german = step_part(on_this_mac("de").opens("/setup?step=ai"))
    english = step_part(on_this_mac("en").opens("/setup?step=ai"))

    assert ("Ich habe oMLX installiert", "/setup?step=ai") in german.links()
    assert ("I installed oMLX", "/setup?step=ai") in english.links()
    assert "Erneut prüfen" not in german.text


# ── 5. The command as typed in the famstack folder ───────────────────────

def test_the_ai_steps_terminal_command_is_typed_in_the_famstack_folder(instance):
    """Given the admin installs the AI from the terminal, then the AI step
    names `./stack up ai`, as typed in the famstack folder."""
    instance("signed")
    step = step_part(on_this_mac("de").opens("/setup?step=ai&ai=install"))

    assert step.texts("setup-terminal-command") == ["./stack up ai"]


# ── 6. What Copy copies ──────────────────────────────────────────────────

# Characters that do not show and would be pasted with a value: Unicode's
# format characters (soft hyphen, zero-width space, direction marks) and
# spaces that are no space to a shell.
INVISIBLE = re.compile(r"[­᠎​-‏‪-‮⁠-⁤﻿   ]")


def copied_values(page) -> list[str]:
    """Each value a Copy button sits beside, as the page holds it."""
    return re.findall(r'<code class="setup-value[^"]*">([^<]*)</code><button class="setup-mini setup-copy"',
                      page.body)


@pytest.mark.parametrize("step", ["mac", "ai&ai=install"])
def test_a_value_beside_copy_is_plain_characters(instance, step):
    """Given a step with Copy beside a value, in German, then the value is
    plain characters: no `~`, no invisible character, no odd space."""
    instance("signed" if step != "mac" else "chat")
    values = copied_values(on_this_mac("de").opens(f"/setup?step={step}"))

    assert values
    for value in values:
        assert value == value.strip() and "~" not in value and not INVISIBLE.search(value), repr(value)


# ── 7. An oMLX installed with Homebrew ───────────────────────────────────

def test_the_ai_step_finds_an_omlx_installed_with_homebrew(instance, monkeypatch):
    """Given oMLX is installed with Homebrew and nothing answers on this
    Mac, when the admin opens the AI step, then it says oMLX is installed
    and offers `./stack up ai`, which sets it up, in place of the download.
    It asks Homebrew by the check `stack up ai` uses (`homebrew_omlx`)."""
    instance("signed")
    monkeypatch.setattr(importlib.import_module("stacklets.ai.homebrew_omlx"),
                        "LIST_COMMAND", ("true",))
    step = step_part(on_this_mac("de").opens("/setup?step=ai"))

    assert "oMLX ist auf diesem Mac mit Homebrew installiert" in step.text
    assert step.texts("setup-terminal-command") == ["./stack up ai"]
    assert "oMLX herunterladen" not in [text for text, _ in step.links()]
