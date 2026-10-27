"""`stack family`: the people an instance is for, read and written as one file.

The installer from a file, the dashboard's first run and a test all write
the family through `stack family set --from <file>`; `stack family` shows
what is there. What a caller can rely on:

- A file becomes users.toml and stack.toml: each person's username, email,
  relation, words and whether they look after the server, the household's
  name, its web address, and the chat's server name while the chat does
  not exist. Each new person gets a password, as the installer gives one.
- A file with problems is refused with every problem named, and nothing
  is written.
- The same file twice writes nothing the second time.
- Once the chat exists, the people cannot change through this command.
- `stack family` writes nothing, and reads the files an older install or
  the e2e lane wrote, which have no relation and no words.

Driven through the real entry point, `stack.cli.main` with the command
line in `sys.argv`, in a checkout in `tmp_path` with no stacklets: the
command is the framework's, and no stacklet is needed to keep the family.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

from stack import cli

E2E_INSTANCE = Path(__file__).resolve().parents[2] / "e2e" / "instance"

SIMPSONS = """\
name = "Simpson"
domain = "simpsons.com"

[[people]]
name = "Homer Simpson"
words = "he"

[[people]]
name = "Marge Simpson"
words = "she"

[[people]]
name = "Bart Simpson"

[[people]]
name = "Lisa Simpson"
words = "she"
"""

# What the installer writes before anyone is named, cut to the keys that
# matter here: an empty household name and an empty chat server name.
FRESH_STACK_TOML = """\
[core]
name = "famstack"
stack_owner = ""
domain = ""
timezone = "Europe/Berlin"
language = "en"

[messages]
server_name = ""
"""


@pytest.fixture
def instance(tmp_path, monkeypatch):
    """A checkout with stack.toml as a fresh install leaves it, and no one in it."""
    (tmp_path / "stacklets").mkdir()
    (tmp_path / "stack.toml").write_text(
        FRESH_STACK_TOML.replace('[core]\n', f'[core]\ndata_dir = "{tmp_path / "data"}"\n'))
    (tmp_path / ".stack").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STACK_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def stack(capfd, monkeypatch, *argv: str, stdin: str = "") -> tuple[int, str, str]:
    """Run `stack <argv>` as the shell does: (exit code, stdout, stderr)."""
    monkeypatch.setattr(sys, "argv", ["stack", *argv])
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    try:
        cli.main()
        code = 0
    except SystemExit as exit_:
        code = exit_.code or 0
    out, err = capfd.readouterr()
    return code, out, err


def family_file(root: Path, text: str, name: str = "family.toml") -> str:
    (root / name).write_text(text)
    return str(root / name)


def users(root: Path) -> dict[str, dict]:
    with open(root / "users.toml", "rb") as f:
        return {u["id"]: u for u in tomllib.load(f)["users"]}


def config(root: Path) -> dict:
    with open(root / "stack.toml", "rb") as f:
        return tomllib.load(f)


def secrets(root: Path) -> dict:
    path = root / ".stack" / "secrets.toml"
    return tomllib.loads(path.read_text()) if path.exists() else {}


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file the command could write, as it is now."""
    names = ["stack.toml", "users.toml", ".stack/secrets.toml"]
    return {n: (root / n).read_bytes() for n in names if (root / n).exists()}


# ── A family file becomes the configuration ─────────────────────────────

def test_a_family_file_becomes_the_people_and_the_settings(instance, capfd, monkeypatch):
    code, out, _ = stack(capfd, monkeypatch, "family", "set", "--from",
                         family_file(instance, SIMPSONS), "--json")

    assert code == 0, out
    people = users(instance)
    assert list(people) == ["homer", "marge", "bart", "lisa"]
    assert people["homer"]["name"] == "Homer Simpson"
    assert people["homer"]["email"] == "homer@simpsons.com"
    # The first two are parents, the rest children, unless the file says.
    assert [p["relation"] for p in people.values()] == ["parent", "parent", "child", "child"]
    assert people["marge"]["words"] == "she"
    assert "words" not in people["bart"]
    # Nobody was named, so the first person looks after the server.
    assert [p["role"] for p in people.values()] == ["admin", "member", "member", "member"]
    # A member without messages in their services gets no chat account.
    assert "messages" in people["bart"]["stacklets"]
    cfg = config(instance)
    assert cfg["core"]["stack_owner"] == "Simpson"
    assert cfg["core"]["family_domain"] == "simpsons.com"
    assert cfg["messages"]["server_name"] == "simpsons.com"
    # Each person's first password is their username, as the installer sets it.
    assert secrets(instance)["global__USER_BART_PASSWORD"] == "bart"
    assert json.loads(out)["changed"] == ["users.toml", "stack.toml", ".stack/secrets.toml"]


def test_without_a_web_address_the_chat_is_named_after_the_family(instance, capfd, monkeypatch):
    text = SIMPSONS.replace('domain = "simpsons.com"\n', "")

    code, _, err = stack(capfd, monkeypatch, "family", "set", "--from", family_file(instance, text))

    assert code == 0, err
    assert config(instance)["messages"]["server_name"] == "simpson"
    assert users(instance)["homer"]["email"] == "homer@home.local"
    assert "family_domain" not in config(instance)["core"]


def test_a_json_file_says_the_same(instance, capfd, monkeypatch):
    family = {"name": "Simpson", "people": [
        {"name": "Marge Simpson", "relation": "parent", "looks_after": True},
        {"name": "Homer Simpson", "relation": "parent"},
        {"name": "Abe Simpson", "relation": "grandparent", "words": "he"},
    ]}

    code, _, err = stack(capfd, monkeypatch, "family", "set", "--from",
                         family_file(instance, json.dumps(family), "family.json"))

    assert code == 0, err
    people = users(instance)
    assert people["abe"]["relation"] == "grandparent"
    # Someone was named, so only they look after the server.
    assert [p["role"] for p in people.values()] == ["admin", "member", "member"]


def test_the_file_can_come_on_stdin(instance, capfd, monkeypatch):
    """The host API passes the body this way, never on the command line."""
    family = {"name": "Simpson", "people": [{"name": "Homer Simpson"}]}

    code, out, _ = stack(capfd, monkeypatch, "family", "set", "--from", "-", "--json",
                         stdin=json.dumps(family))

    assert code == 0, out
    assert list(users(instance)) == ["homer"]


def test_a_first_name_outside_a_to_z_still_gives_a_username(instance, capfd, monkeypatch):
    """Usernames are chat ids and logins, which take a to z only."""
    text = 'name = "Müller"\n[[people]]\nname = "Jürgen Müller"\n[[people]]\nname = "Zoë Müller"\n'

    code, _, err = stack(capfd, monkeypatch, "family", "set", "--from", family_file(instance, text))

    assert code == 0, err
    assert list(users(instance)) == ["juergen", "zoe"]
    assert users(instance)["juergen"]["name"] == "Jürgen Müller"


# ── Refused: every problem named, nothing written ────────────────────────

def test_every_problem_is_named_and_nothing_is_written(instance, capfd, monkeypatch):
    text = """\
name = "Simpson"
domain = "https://simpsons"

[[people]]
name = "Homer Simpson"
looks_after = false

[[people]]
name = "Homer Simpson"

[[people]]
name = "Bart Simpson"
relation = "nephew"
"""
    before = snapshot(instance)

    code, out, _ = stack(capfd, monkeypatch, "family", "set", "--from",
                         family_file(instance, text), "--json")

    assert code == 1
    result = json.loads(out)
    fields = [p["field"] for p in result["problems"]]
    assert "domain" in fields                 # not a web address
    assert "people.1.name" in fields          # the same name twice
    assert "people.2.relation" in fields      # not a relation
    assert "people" in fields                 # nobody looks after the server
    assert all(p["message"] for p in result["problems"])
    assert snapshot(instance) == before


def test_a_refusal_names_each_person_in_the_terminal(instance, capfd, monkeypatch):
    text = 'name = "Simpson"\n[[people]]\nname = "Bart Simpson"\nrelation = "nephew"\n'

    code, _, err = stack(capfd, monkeypatch, "family", "set", "--from", family_file(instance, text))

    assert code == 1
    assert "Bart Simpson: The relation is parent, child, grandparent or other." in err


def test_a_file_that_does_not_parse_is_refused(instance, capfd, monkeypatch):
    before = snapshot(instance)

    code, out, _ = stack(capfd, monkeypatch, "family", "set", "--from",
                         family_file(instance, 'name = "Simpson\n'), "--json")

    assert code == 1
    assert "TOML" in json.loads(out)["problems"][0]["message"]
    assert snapshot(instance) == before


# ── Idempotent ───────────────────────────────────────────────────────────

def test_the_same_file_twice_changes_nothing(instance, capfd, monkeypatch):
    path = family_file(instance, SIMPSONS)
    stack(capfd, monkeypatch, "family", "set", "--from", path)
    before = snapshot(instance)

    code, out, _ = stack(capfd, monkeypatch, "family", "set", "--from", path, "--json")

    assert code == 0
    assert json.loads(out)["changed"] == []
    assert snapshot(instance) == before


# ── What stays ───────────────────────────────────────────────────────────

def test_an_existing_server_name_and_domain_mode_are_left_alone(instance, capfd, monkeypatch):
    """The chat's server name is permanent once chosen, and `[core] domain`
    is domain mode, which has nothing to do with the family's address."""
    toml = (instance / "stack.toml").read_text()
    (instance / "stack.toml").write_text(
        toml.replace('domain = ""', 'domain = "home.example"')
            .replace('server_name = ""', 'server_name = "springfield"'))

    code, _, err = stack(capfd, monkeypatch, "family", "set", "--from", family_file(instance, SIMPSONS))

    assert code == 0, err
    cfg = config(instance)
    assert cfg["core"]["domain"] == "home.example"
    assert cfg["messages"]["server_name"] == "springfield"
    assert cfg["core"]["family_domain"] == "simpsons.com"


def test_people_already_there_keep_their_email_and_services(instance, capfd, monkeypatch):
    shutil.copy(E2E_INSTANCE / "users.toml", instance / "users.toml")

    code, _, err = stack(capfd, monkeypatch, "family", "set", "--from", family_file(instance, SIMPSONS))

    assert code == 0, err
    people = users(instance)
    assert people["bart"]["email"] == "bart@home.local"
    assert people["bart"]["stacklets"] == ["messages", "docs"]
    # Marge looked after the server in the old file; the new one names nobody,
    # so only Homer, the first, does now.
    assert people["marge"]["role"] == "member"


def test_once_the_chat_exists_a_new_person_is_refused(instance, capfd, monkeypatch):
    """Adding someone needs a chat account, which this command cannot make yet."""
    two = SIMPSONS.split('[[people]]\nname = "Bart Simpson"')[0]
    stack(capfd, monkeypatch, "family", "set", "--from", family_file(instance, two))
    (instance / ".stack" / "messages.setup-done").touch()
    before = snapshot(instance)

    code, out, _ = stack(capfd, monkeypatch, "family", "set", "--from",
                         family_file(instance, SIMPSONS), "--json")

    assert code == 1
    problems = json.loads(out)["problems"]
    assert {p["field"] for p in problems} == {"people.2.name", "people.3.name"}
    assert "stack messages setup" in problems[0]["message"]
    assert snapshot(instance) == before


def test_once_the_chat_exists_the_rest_can_still_change(instance, capfd, monkeypatch):
    path = family_file(instance, SIMPSONS.replace('domain = "simpsons.com"\n', ""))
    stack(capfd, monkeypatch, "family", "set", "--from", path)
    (instance / ".stack" / "messages.setup-done").touch()

    code, _, err = stack(capfd, monkeypatch, "family", "set", "--from", family_file(
        instance, SIMPSONS.replace('name = "Bart Simpson"', 'name = "Bart Simpson"\nwords = "he"')))

    assert code == 0, err
    assert users(instance)["bart"]["words"] == "he"
    # The chat was named before the web address came; that name stays.
    assert config(instance)["messages"]["server_name"] == "simpson"


# ── Reading ──────────────────────────────────────────────────────────────

def test_stack_family_shows_what_was_set(instance, capfd, monkeypatch):
    stack(capfd, monkeypatch, "family", "set", "--from", family_file(instance, SIMPSONS))

    code, out, _ = stack(capfd, monkeypatch, "family", "--json")

    assert code == 0
    family = json.loads(out)
    assert family["name"] == "Simpson"
    assert family["domain"] == "simpsons.com"
    assert family["people"][0] == {
        "id": "homer", "name": "Homer Simpson", "relation": "parent", "words": "he",
        "role": "admin", "email": "homer@simpsons.com",
    }


def test_files_written_before_the_family_read_as_they_are(instance, capfd, monkeypatch):
    """The e2e lane writes stack.toml and users.toml itself. `stack family`
    reads them without a relation or words, and changes nothing."""
    shutil.copy(E2E_INSTANCE / "stack.toml", instance / "stack.toml")
    shutil.copy(E2E_INSTANCE / "users.toml", instance / "users.toml")
    before = snapshot(instance)

    code, out, _ = stack(capfd, monkeypatch, "family", "--json")

    assert code == 0
    family = json.loads(out)
    assert family["name"] == ""
    assert [p["id"] for p in family["people"]] == ["homer", "marge", "bart", "lisa"]
    assert family["people"][2] == {
        "id": "bart", "name": "Bart Simpson", "relation": None, "words": None,
        "role": "member", "email": "bart@home.local",
    }
    assert snapshot(instance) == before


def test_stack_family_in_the_terminal_says_how_to_change_it(instance, capfd, monkeypatch):
    stack(capfd, monkeypatch, "family", "set", "--from", family_file(instance, SIMPSONS))

    code, out, _ = stack(capfd, monkeypatch, "family")

    assert code == 0
    assert "The Simpsons" in out
    assert "Homer Simpson" in out and "looks after the server" in out
    assert "stack family set --from" in out
