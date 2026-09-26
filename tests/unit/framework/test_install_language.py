"""The family's language, chosen once in the install wizard.

It names the document categories seeded into Paperless, the language the
bots answer in and the language voice messages are transcribed in. The
wizard proposes one from the Mac's time zone and asks; the answer is
`[core] language`, and also the first value of `[ai] language`, the
language the stack speaks to you, which can be changed on its own.
"""

import tomllib

from stack import installer


def test_the_proposal_follows_the_time_zone():
    assert installer.detect_language("Europe/Berlin") == "de"
    assert installer.detect_language("America/New_York") == "en"


def test_only_languages_with_a_seeded_taxonomy_are_accepted():
    assert installer.validate_language("de") is None
    assert installer.validate_language("en") is None
    assert installer.validate_language("fr") == "Choose en or de"


def test_the_chosen_language_is_the_family_language_and_the_first_voice(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "REPO_ROOT", tmp_path)

    installer.write_stack_toml("Simpson", "simpson", "Europe/Berlin", "de")

    cfg = tomllib.loads((tmp_path / "stack.toml").read_text())
    assert cfg["core"]["language"] == "de"
    # The voice starts in the same language and can be changed on its own.
    assert cfg["ai"]["language"] == "de"


def test_an_english_choice_in_a_german_time_zone_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "REPO_ROOT", tmp_path)

    installer.write_stack_toml("Simpson", "simpson", "Europe/Berlin", "en")

    cfg = tomllib.loads((tmp_path / "stack.toml").read_text())
    assert cfg["core"]["language"] == "en"
