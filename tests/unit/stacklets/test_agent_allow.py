"""`[agent] allow`: the stacklet commands the admin lets the agent run.

The agent reaches the CLI only through the core API, which runs a fixed
set of memory and docs commands. An admin who wants the agent to operate
something more, the kitchen kettle say, lists its commands in stack.toml:

    [agent]
    allow = ["kettle"]            # every command of the kettle stacklet
    allow = ["kettle status"]     # only this one

Two promises are pinned here:

  1. The API runs what is allowed and refuses the rest, and no entry can
     hand the agent a framework command such as `up` or `destroy`.
  2. The agent is told what it may run, with each command's own HELP
     line, so it can call one without asking `--help` first.
  3. A command of the agent's waits while an installed stacklet has a
     migration left to run, so it never acts with a release's code on
     data still in the previous release's shape.

The tests run the real API module and the real `./stack` against a
throwaway instance (STACK_DIR) that has one extension stacklet.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

from stack.agent_access import write_command_skill
from stack.stack import Stack

REPO_ROOT = Path(__file__).resolve().parents[3]
EXIT_MARKER = "stack-exit:"

KETTLE_STATUS = '''\
"""stack kettle status: whether the kettle is on."""
HELP = "Show whether the kettle is on"


def run(args, stacklet, config):
    print("The kettle is cold.")
    return {"on": False}
'''

KETTLE_BOIL = '''\
"""stack kettle boil: boil the water."""
HELP = "Boil the water: boil <minutes>"


def run(args, stacklet, config):
    print("Boiling.")
    return {"ok": True}
'''


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def instance(tmp_path, monkeypatch):
    """A throwaway instance with an extension stacklet `kettle`.

    Returns a function that writes `[agent] allow` into stack.toml, so a
    test can change it between two requests the way an admin would.
    """
    kettle = tmp_path / "ext" / "kettle"
    (kettle / "cli").mkdir(parents=True)
    (kettle / "stacklet.toml").write_text(
        'id = "kettle"\nname = "Kettle"\ndescription = "The kitchen kettle"\n'
        'version = "0.1.0"\ncategory = "automation"\ntype = "host"\n')
    (kettle / "cli" / "status.py").write_text(KETTLE_STATUS)
    (kettle / "cli" / "boil.py").write_text(KETTLE_BOIL)
    (tmp_path / ".stack").mkdir()
    monkeypatch.setenv("STACK_DIR", str(tmp_path))

    def allow(*entries: str):
        listed = ", ".join(f'"{e}"' for e in entries)
        (tmp_path / "stack.toml").write_text(
            f'[core]\ndata_dir = "{tmp_path / "data"}"\n'
            f'extension_dirs = ["{tmp_path / "ext"}"]\n\n'
            f'[agent]\nallow = [{listed}]\n')
    allow()
    return allow


@pytest.fixture
def api():
    """The core API module, loaded from its file (its name has a hyphen)."""
    path = REPO_ROOT / "stacklets" / "core" / "famstack-api.py"
    loader = importlib.machinery.SourceFileLoader("famstack_api", str(path))
    spec = importlib.util.spec_from_file_location("famstack_api", path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def ask(api, line: str) -> tuple[str, int]:
    """Send one plaintext line as the agent's client does: (text, exit code)."""
    reply = api.handle_plaintext(line).rstrip("\n")
    body, _, last = reply.rpartition("\n")
    if last.startswith(EXIT_MARKER):
        return body, int(last.removeprefix(EXIT_MARKER))
    return reply, 0


def skill_for(tmp_path: Path) -> Path:
    return tmp_path / "workspace" / "skills" / "stack-commands" / "SKILL.md"


# ── the API runs what the admin allowed ────────────────────────────────────

def test_an_allowed_stacklet_runs_every_one_of_its_commands(instance, api):
    instance("kettle")

    text, code = ask(api, "kettle status")

    assert code == 0
    assert "The kettle is cold." in text


def test_an_allowed_command_does_not_open_the_rest_of_its_stacklet(instance, api):
    instance("kettle status")

    assert ask(api, "kettle status")[1] == 0
    assert ask(api, "kettle boil 3")[1] == 126


def test_nothing_is_allowed_beyond_the_built_in_commands_by_default(instance, api):
    assert ask(api, "kettle status")[1] == 126


def test_a_change_to_stack_toml_applies_without_restarting_the_api(instance, api):
    """The API reads stack.toml on every request, as the CLI does."""
    assert ask(api, "kettle status")[1] == 126

    instance("kettle")

    assert ask(api, "kettle status")[1] == 0


@pytest.mark.parametrize("entry, line", [
    ("up", "up kettle"),
    ("destroy kettle", "destroy kettle"),
    ("config", "config --secrets"),
])
def test_a_framework_command_is_refused_even_when_listed(instance, api, entry, line):
    """Only stacklet commands can be allowed. A config mistake must not
    hand an LLM the lifecycle of the instance."""
    instance(entry)

    assert ask(api, line)[1] == 126


# ── the agent writes only as itself ───────────────────────────────────────

def test_allowing_the_agent_to_send_does_not_let_it_write_as_a_person(instance, api):
    """Refused before anything runs: a person's name on a message stays
    something only someone at the terminal can put there."""
    instance("messages")

    text, code = ask(api, 'messages send chat "Pick up the kids" --as homer')

    assert code == 126
    assert "never as a person" in text


@pytest.mark.parametrize("line", [
    ["messages", "upload", "chat", "letter.pdf", "--as", "marge"],
    ["messages", "send", "chat", "hi", "--as", "agent", "--as", "homer"],
    ["messages", "send", "chat", "hi", "--as"],
])
def test_every_way_of_naming_another_sender_is_refused(api, line):
    assert api.sender_refusal(line)


def test_the_agent_sends_as_itself_or_as_the_system_account(api):
    assert api.sender_refusal(["messages", "send", "--to", "homer", "hi", "--as", "agent"]) is None
    assert api.sender_refusal(["messages", "send", "chat", "Backup done"]) is None


def test_as_on_other_commands_names_who_acted_and_is_left_alone(api):
    assert api.sender_refusal(["memory", "list", "tick", "shopping", "milk", "--as", "homer"]) is None


# ── the agent is told what it may run ──────────────────────────────────────

def test_the_agent_sees_each_allowed_command_with_its_help(instance, tmp_path):
    instance("kettle")

    write_command_skill(Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path),
                        tmp_path / "workspace")

    skill = skill_for(tmp_path).read_text()
    assert "stack kettle status" in skill and "Show whether the kettle is on" in skill
    assert "stack kettle boil" in skill and "Boil the water: boil <minutes>" in skill


def test_an_allowed_command_is_the_only_one_the_agent_sees(instance, tmp_path):
    instance("kettle status")

    write_command_skill(Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path),
                        tmp_path / "workspace")

    skill = skill_for(tmp_path).read_text()
    assert "stack kettle status" in skill
    assert "boil" not in skill


def test_with_nothing_allowed_the_agent_gets_no_command_list(instance, tmp_path):
    """No list at all, rather than an empty one: the agent's prompt stays
    exactly as it is on an instance that never used `[agent] allow`."""
    stack = Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path)
    instance("kettle")
    write_command_skill(stack, tmp_path / "workspace")

    instance()
    write_command_skill(stack, tmp_path / "workspace")

    assert not skill_for(tmp_path).parent.exists()


def test_entries_that_name_no_stacklet_command_are_reported(instance, tmp_path):
    """The agent hook warns about these at `stack up agent`."""
    instance("kettle", "up", "kettle grind", "toaster")

    refused = write_command_skill(Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path),
                                  tmp_path / "workspace")

    assert refused == ["up", "kettle grind", "toaster"]
    assert "grind" not in skill_for(tmp_path).read_text()


def test_the_agent_hook_writes_the_list_before_it_checks_the_ai_server(instance, tmp_path):
    """`stack up agent` stops without an AI server, and returns early when
    one does not answer. The command list must not depend on either."""
    path = REPO_ROOT / "stacklets" / "agent" / "hooks" / "on_start.py"
    spec = importlib.util.spec_from_file_location("agent_on_start", path)
    on_start = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(on_start)

    class Ctx:
        stack = Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path)
        env = {"AGENT_DATA_DIR": str(tmp_path), "AGENT_OPENAI_URL": ""}

    instance("kettle")

    with pytest.raises(RuntimeError):
        on_start.run(Ctx())

    assert "stack kettle status" in skill_for(tmp_path).read_text()


# ── the agent waits for migrations ─────────────────────────────────────────

DESCALED = '''\
"""stack kettle status: whether the kettle is descaled."""
import json
from pathlib import Path

HELP = "Show whether the kettle is descaled"


def run(args, stacklet, config):
    record = Path(config["data_dir"]) / "kettle" / ".migrations.json"
    applied = json.loads(record.read_text())["applied"] if record.exists() else {}
    print("Descaled." if "0001" in applied else "Still scaly.")
    return {}
'''


@pytest.fixture
def kettle_migrates(instance, tmp_path):
    """The kettle is installed and ships a migration its data has not had.

    Returns a function that records it as run, as `stack up kettle` does.
    """
    instance("kettle")
    kettle = tmp_path / "ext" / "kettle"
    (kettle / "cli" / "status.py").write_text(DESCALED)
    (kettle / "migrations").mkdir()
    (kettle / "migrations" / "0001_descale.py").write_text(
        '"""Descale the kettle."""\ndef run(ctx):\n    pass\n')
    (tmp_path / ".stack" / "kettle.setup-done").touch()
    record = tmp_path / "data" / "kettle" / ".migrations.json"

    def migrated():
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text('{"applied": {"0001": {"name": "descale"}}, "failed": {}}\n')
    return migrated


def test_a_command_waits_for_a_pending_migration_and_then_runs(kettle_migrates, api,
                                                                 monkeypatch):
    """Asked while the stack migrates, the agent gets its answer once the
    migration has run, from the data in its new shape."""
    import threading
    monkeypatch.setattr(api, "MIGRATIONS_POLL_SECS", 0.02)
    threading.Timer(0.2, kettle_migrates).start()

    text, code = ask(api, "kettle status")

    assert code == 0
    assert "Descaled." in text


def test_a_migration_that_does_not_run_refuses_the_command_and_says_why(kettle_migrates, api,
                                                                         monkeypatch):
    """A failed migration holds until a `stack up` gets it through. The
    agent hears that, and when to ask again, instead of an answer from
    data in the wrong shape."""
    monkeypatch.setattr(api, "MIGRATIONS_POLL_SECS", 0.02)
    monkeypatch.setattr(api, "MIGRATIONS_WAIT_SECS", 0.1)

    text, code = ask(api, "kettle status")

    assert code == 75
    assert "kettle/0001" in text and "Still scaly." not in text


def test_with_nothing_pending_a_command_runs_without_waiting(kettle_migrates, api, monkeypatch):
    """The API waits by sleeping its poll interval. Other sleeps in the
    process, a subprocess finishing or another test's thread on the same
    worker, are not a wait for migrations."""
    kettle_migrates()
    slept: list[float] = []
    monkeypatch.setattr(api.time, "sleep", slept.append)

    text, code = ask(api, "kettle status")

    assert code == 0
    assert api.MIGRATIONS_POLL_SECS not in slept
    assert "Descaled." in text


# ── the agent sets reminders with the family's scheduler ──────────────────

REMINDER = ('core schedule add remind-marge-cake "in 10m" --title "Get the cake out" '
            '--by marge --for marge -- messages send chat "Get the cake out" '
            '--as agent --mention marge')


def test_the_agent_sets_a_reminder_that_messages_the_person(instance, api):
    """A reminder is a message the agent sends later, at a time the family's
    scheduler keeps. The entry is listed with the person it is for."""
    text, code = ask(api, REMINDER)

    assert code == 0, text
    listed, code = ask(api, "core schedule list --for marge")
    assert code == 0, listed
    assert "remind-marge-cake" in listed


@pytest.mark.parametrize("line, why", [
    ('core schedule add remind-x "in 10m" -- messages send chat "hi" --as homer',
     "never as a person"),
    ('core schedule add remind-x "in 10m" -- kettle boil 3', "not allowed"),
    ('core schedule add remind-x "in 10m" -- up kettle', "not allowed"),
    ('core schedule add dates "daily 07:00" -- memory list show dates', "remind-"),
    ("core schedule remove dates", "remind-"),
    ("core schedule run remind-x", "not allowed"),
])
def test_a_reminder_reaches_no_further_than_the_agent(instance, api, line, why):
    """The scheduler runs any stacklet command later, as the admin. Through
    the agent it runs only what the agent may run itself, sends only as the
    agent, and touches only reminders, never an entry the family made."""
    text, code = ask(api, line)

    assert code == 126, text
    assert why in text


ADD = 'core schedule add remind-x "in 10m" '


@pytest.mark.parametrize("command", [
    "-- memory write family/notes.md --by marge --from /no/such/file",
    "-- memory write family/notes.md --by marge --fr=/no/such/file",
    "-- photos import --source /no/such/dir",
    "-- photos import --sou /no/such/dir",
    "-- messages upload family /no/such/file",
    "-- memory search tent --vault /no/such/dir",
    "-- memory search tent --vau /no/such/dir",
    "-- memory capture --file /no/such/file --by marge",
    # `--` is optional to `core schedule add`: without it the command starts
    # at the first word that is no option, and a later `--` is part of it.
    "memory write family/notes.md --by marge --from /no/such/file",
    "memory write family/notes.md --by marge --from /no/such/file -- memory search tent",
    "--title Tidy memory write family/notes.md --by marge --from /no/such/file -- memory search tent",
])
def test_a_reminder_names_no_path_on_the_host(instance, api, command):
    """Given a reminder whose command names a path on the host, even with the
    commands that take one allowed, when the agent adds it, then it is refused
    and nothing is stored. The scheduler runs the command on the host later,
    so a path in it reaches as far as one in a command run now."""
    instance("photos", "messages")

    text, code = ask(api, ADD + command)

    assert code == 126, text
    assert "a path on the host" in text
    listed, _ = ask(api, "core schedule list")
    assert "remind-x" not in listed


def test_a_reminder_runs_the_command_the_check_read(instance, api):
    """Given a command the agent may not run, with an allowed one after a later
    `--`, when the agent adds it, then it is refused: the entry would run the
    first command, so that is the one the check has to read."""
    text, code = ask(api, ADD + "kettle boil 3 -- memory search tent")

    assert code == 126, text
    listed, _ = ask(api, "core schedule list")
    assert "remind-x" not in listed


@pytest.mark.parametrize("line", [
    'core --x schedule add remind-x "in 10m" -- kettle boil 3',
    'core --x schedule add dates "daily 07:00" -- kettle boil 3',
    ADD + "-- photos --x import --source /no/such/dir",
    "photos --x import --source /no/such/dir",
])
def test_an_option_cannot_hide_a_command_from_the_checks(instance, api, line):
    """Given a stacklet the admin allowed whole, and an option between it and
    its command, when the agent sends the line, then it is refused and nothing
    is stored. The CLI ignores the option and runs the command, while the checks
    read the command by its position, and would not see it."""
    instance("core", "photos")

    text, code = ask(api, line)

    assert code == 126, text
    listed, _ = ask(api, "core schedule list")
    assert "remind-x" not in listed
    assert "dates" not in listed


def test_asking_an_allowed_stacklet_for_its_help_still_works(instance, api):
    """Given a stacklet the admin allowed whole, when the agent asks for its
    help, then it gets it: help is the one option in that place, and it runs
    nothing."""
    instance("kettle")

    text, code = ask(api, "kettle --help")

    assert code == 0, text
    assert "boil" in text


@pytest.mark.parametrize("command", [
    "-- memory list show dates",
    "-- messages send chat hi --as agent",
    # An album, not a path: `--from` is a path only to `memory write`.
    "-- photos album holiday --from summer",
])
def test_a_reminder_without_a_host_path_is_added(instance, api, command):
    """Given a reminder with an allowed command and no path on the host, when
    the agent adds it, then it is stored as the scheduler will run it."""
    instance("photos")

    text, code = ask(api, ADD + command)

    assert code == 0, text
    listed, _ = ask(api, "core schedule list")
    assert "remind-x" in listed


def test_the_admin_may_schedule_a_command_that_names_a_host_path(instance, tmp_path):
    """Given the admin's own `stack core schedule add`, when its command names
    a path on the host, then it is stored: the check guards the agent's API,
    and the admin can already read every file on the Mac."""
    stack = Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path)

    result = stack.run_cli_command("core", "schedule", [
        "add", "tidy", "in 10m", "--", "memory", "write", "family/notes.md", "--from", "/no/such/file"])

    assert result["ok"], result
    assert "--from" in result["run"]
