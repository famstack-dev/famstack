"""Every message the stack posts into the chat is in the stack's language.

A family whose `stack.toml` says `[core] language = "de"` meets no English
in the chat: the welcomes, the bots' introductions, the Server room's
notices, the device notices and the bots' own answers are German. Any
other language gets English, as before.

Each sender is driven through its surface: `./stack messages setup` as
`stack up messages` runs it (through `Stack.run_cli_command`, against a
stand-in Synapse on localhost, `tests/synapse.py`). Which language a text
is in is judged by its common words (`language_of`), not by its wording,
so a reworded message does not break a test.
"""

from __future__ import annotations

import re

import pytest

from tests.springfield import make_stack, write_instance
from tests.synapse import Synapse

SERVER = "springfield.example"

# ── Telling German from English ──────────────────────────────────────────

# Short words that nearly every sentence of the language has and the other
# language does not. A text is the language whose words it has more of.
_COMMON = {
    "de": {"der", "die", "das", "und", "ist", "ich", "du", "dich", "dir", "dein", "deine",
           "euer", "eure", "euch", "nicht", "mit", "für", "auf", "hier", "ein", "eine",
           "wird", "kann", "sind", "zu", "den", "dem", "im", "wir", "oder", "was", "wie"},
    "en": {"the", "and", "is", "you", "your", "to", "of", "in", "this", "it", "for",
           "with", "here", "can", "will", "are", "an", "not", "or", "what", "how", "be"},
}


def language_of(text: str) -> str:
    """"de" or "en": the language whose common words the text has more of."""
    words = re.findall(r"[a-zäöüß]+", text.lower())
    counts = {lang: sum(w in common for w in words) for lang, common in _COMMON.items()}
    return max(counts, key=counts.__getitem__)


def test_language_of_tells_the_two_apart():
    """The judge itself: a German and an English sentence are told apart."""
    assert language_of("Hier findet ihr eure Erinnerungen, und du kannst sie lesen.") == "de"
    assert language_of("Here you find your memories, and you can read them.") == "en"


# ── The welcomes of `stack messages setup` ───────────────────────────────

@pytest.fixture
def setup_run(tmp_path, monkeypatch, httpserver):
    """Run `stack messages setup` on a Simpsons instance in `language`,
    against a stand-in Synapse, from a directory that is not the
    instance's; returns the stand-in with what was posted, and the
    instance. `bot_password` is the stacker bot's stored password, None
    for a first setup that makes one."""
    def run(language: str, *, bot_password: str | None = "duff"):
        stored = {"core__STACKER_BOT_PASSWORD": bot_password} if bot_password else {}
        instance = write_instance(tmp_path, language=language, secrets=stored,
                                  core='stack_owner = "Simpson"')
        synapse = Synapse(httpserver, server=SERVER, admin_password="donuts")
        stack = make_stack(instance)
        # The command finds Synapse at the manifest's port; the stand-in
        # listens on the test server's port instead.
        discover = stack.discover

        def discover_with_stand_in():
            found = discover()
            for s in found:
                if s["id"] == "messages":
                    s["manifest"].setdefault("ports", {})["synapse"] = httpserver.port
            return found
        monkeypatch.setattr(stack, "discover", discover_with_stand_in)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        result = stack.run_cli_command("messages", "setup")
        assert result and result.get("ok"), result
        return synapse, instance
    return run


def test_setup_welcomes_a_german_family_in_german(setup_run):
    """Given `[core] language = "de"`, when `stack messages setup` runs as
    `stack up messages` runs it, then the welcomes in the Server room and
    in the Memories room are German. Both arrived in English on a German
    install."""
    synapse, _ = setup_run("de")

    for room in ("famstack", "memories"):
        posted = synapse.posted_to(room)
        assert posted, f"nothing was posted into #{room}"
        assert [language_of(text) for text in posted] == ["de"] * len(posted), posted
    assert "Simpson" in synapse.posted_to("famstack")[0], "the welcome names no family"


def test_setup_welcomes_any_other_language_in_english(setup_run):
    """Given `[core] language = "fr"`, the welcomes are the English ones."""
    synapse, _ = setup_run("fr")

    for room in ("famstack", "memories"):
        posted = synapse.posted_to(room)
        assert posted and {language_of(text) for text in posted} == {"en"}, posted


def test_setup_stores_the_bots_password_in_the_instance(setup_run, tmp_path):
    """Given no stored password for the stacker bot, when `stack messages
    setup` runs from another directory, then the password it makes is in
    the instance's secrets, so the bot runner finds it, and nothing is
    written where the command ran."""
    _, instance = setup_run("de", bot_password=None)

    stored = (instance / ".stack" / "secrets.toml").read_text()
    assert "core__STACKER_BOT_PASSWORD" in stored
    assert not (tmp_path / "elsewhere" / ".stack").exists()
