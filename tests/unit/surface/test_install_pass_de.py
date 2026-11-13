"""The install with the stack in German: the setup's steps, labels and
the bots' welcomes as the admin meets them.

Each test pins one behaviour an install with the stack's language German
showed: the step "Chat on this Mac" that goes on by itself after the
sign-in, the bots' welcomes in the stack's language, the command the
admin types, and what Copy copies.

The setup page is driven through the tools server's FastAPI app as a
browser on the Mac opens it (the Browser DSL), with the host API stood in
at the client's edge (`setup_stand_in.py`). How the open page acts on the
watch in a real browser is in `tests/integration/test_setup_in_the_browser.py`.
"""

from __future__ import annotations

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


# ── 1. The phone step goes on by itself ──────────────────────────────────

def test_the_waiting_phone_step_learns_of_a_sign_in_on_the_mac_without_a_reload(instance):
    """Given the step "Chat auf dem Handy" waits for the admin's sign-in,
    when the admin signs in through its fallback, in the browser on the
    Mac, then the address the open page watches answers done, and the page
    read again shows the step done."""
    host = instance("chat")
    mac = on_this_mac("de")
    page = mac.opens("/setup")
    watch = watch_of(page)

    assert "Dein Handy erreicht famstack nicht?" in step_part(page).text
    assert watch, "the waiting step names no address to watch"
    assert mac.reads(watch) == (200, {"done": False})

    host.signed_in.add("homer")

    assert mac.reads(watch) == (200, {"done": True})
    again = mac.opens("/setup?step=phone")
    assert watch_of(again) == ""
    assert "ist im Familienchat" in step_part(again).text


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


@pytest.mark.parametrize("setting, no_voice, speech", [
    ("speech = false", False, False),
    ("speech = true", False, True),
    ("", False, True),
    ("speech = true", True, False),
], ids=["off", "on", "no key: on", "on, but --no-voice"])
def test_the_ais_welcome_says_what_is_set_up(tmp_path, monkeypatch, setting, no_voice, speech):
    """Given the AI started for the first time, then its welcome in the
    Server room names voice messages and reading aloud only when speech is
    on for that run: `[ai] speech` missing or true, and no `--no-voice`.
    It names no speech engine the stack no longer runs."""
    stck = stack_in(tmp_path, "de")
    toml = stck.instance_dir / "stack.toml"
    toml.write_text(toml.read_text() + f"\n[ai]\n{setting}\n")
    if no_voice:
        monkeypatch.setenv("STACK_AI_NO_VOICE", "1")
    first = {"stacklet": "ai", "name": "ai", "first_run": True, "port": 42060}

    [said] = server_room_after(make_fresh(stck), monkeypatch, first)

    assert "Die KI läuft!" in said
    assert "Piper" not in said and "Whisper" not in said
    assert ("macht aus Sprachnachrichten Text" in said) is speech
    assert ("liest Antworten vor" in said) is speech


@pytest.mark.parametrize("stacklet", ["photos", "docs", "chatai"])
@pytest.mark.parametrize("language, password, where", [
    ("de", "famstack-Passwort", "/go/app/sign-in"),
    ("en", "famstack password", "/go/app/sign-in"),
])
def test_a_welcome_names_the_password_and_where_a_phone_shows_it(tmp_path, monkeypatch, stacklet,
                                                                language, password, where):
    """Given a service started for the first time, then its welcome says to
    sign in with the famstack password, the same as in the chat, and links
    the sign-in page a paired phone opens on the home address. It sends
    nobody to the setup page, which only the Mac opens."""
    first = {"stacklet": stacklet, "name": stacklet, "first_run": True, "port": 42010}

    [said] = server_room_after(stack_in(tmp_path, language), monkeypatch, first)

    assert password in said and where in said
    assert "Einrichtungsseite" not in said and "setup page" not in said


def make_fresh(stck):
    """The same instance read again, so its `stack.toml` is the one on disk."""
    from tests.springfield import make_stack
    return make_stack(stck.instance_dir)


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

# ── 5. The command as typed in the famstack folder ───────────────────────

def test_the_ai_steps_terminal_command_is_typed_in_the_famstack_folder(instance):
    """Given the admin lets famstack install oMLX, then the AI step names
    `./stack ai switch managed`, as typed in the famstack folder."""
    instance("signed")
    step = step_part(on_this_mac("de").opens("/setup?step=ai&ai=install"))

    assert step.texts("setup-terminal-command") == ["./stack ai switch managed"]


# ── 6. What Copy copies ──────────────────────────────────────────────────

# Characters that do not show and would be pasted with a value: Unicode's
# format characters (soft hyphen, zero-width space, direction marks) and
# spaces that are no space to a shell.
INVISIBLE = re.compile(r"[­᠎​-‏‪-‮⁠-⁤﻿   ]")


def copied_values(page) -> list[str]:
    """Each value a Copy button sits beside, as the page holds it."""
    return re.findall(r'<code class="setup-value[^"]*">([^<]*)</code><button class="setup-mini setup-copy"',
                      page.body)


@pytest.mark.parametrize("step", ["phone", "ai&ai=install"])
def test_a_value_beside_copy_is_plain_characters(instance, step):
    """Given a step with Copy beside a value, in German, then the value is
    plain characters: no `~`, no invisible character, no odd space."""
    instance("signed" if step != "phone" else "chat")
    values = copied_values(on_this_mac("de").opens(f"/setup?step={step}"))

    assert values
    for value in values:
        assert value == value.strip() and "~" not in value and not INVISIBLE.search(value), repr(value)
