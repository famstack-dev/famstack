"""`stack config --json` shows an instance's setup without its secrets.

Anything that reads the report (the menu bar app, a script, an agent) sees
the admin's choices, never a credential: stack.toml can hold an API key for
a remote AI server, and users.toml can hold passwords.
"""

import json

from stack.cli import config_report


STACK_TOML = """
[core]
name = "famstack"
domain = "home.example"
language = "de"

[ai]
provider = "remote"
openai_url = "https://ai.example/v1"
openai_key = "sk-live-4f9a"
whisper_key = ""

[backup.targets.attic]
disk = "Attic"
token = "tok-8812"
"""

USERS_TOML = """
[[users]]
id = "homer"
name = "Homer"
role = "admin"
password = "donuts-4ever"

[[users]]
id = "lisa"
name = "Lisa"
role = "member"
stacklets = ["photos", "docs"]
"""


def _instance(tmp_path, stack: str | None = STACK_TOML, users: str | None = USERS_TOML):
    if stack is not None:
        (tmp_path / "stack.toml").write_text(stack)
    if users is not None:
        (tmp_path / "users.toml").write_text(users)
    return tmp_path


def test_no_secret_value_appears_anywhere_in_the_report(tmp_path):
    text = json.dumps(config_report(_instance(tmp_path)))
    for secret in ("sk-live-4f9a", "tok-8812", "donuts-4ever"):
        assert secret not in text


def test_the_admins_choices_are_shown_as_written(tmp_path):
    report = config_report(_instance(tmp_path))
    assert report["config"]["core"] == {"name": "famstack", "domain": "home.example", "language": "de"}
    assert report["config"]["ai"]["openai_url"] == "https://ai.example/v1"
    assert [(u["name"], u["role"]) for u in report["users"]] == [("Homer", "admin"), ("Lisa", "member")]
    assert report["users"][1]["stacklets"] == ["photos", "docs"]


def test_a_hidden_secret_still_says_whether_it_is_set(tmp_path):
    ai = config_report(_instance(tmp_path))["config"]["ai"]
    assert ai["openai_key"] != "" and ai["openai_key"] != "sk-live-4f9a"
    assert ai["whisper_key"] == ""


def test_the_report_names_both_files_so_they_can_be_opened(tmp_path):
    report = config_report(_instance(tmp_path))
    assert report["stack_toml"] == str(tmp_path / "stack.toml")
    assert report["users_toml"] == str(tmp_path / "users.toml")


def test_an_instance_without_config_reports_nothing_rather_than_failing(tmp_path):
    report = config_report(_instance(tmp_path, stack=None, users=None))
    assert report["config"] == {}
    assert report["users"] == []
