"""`stack list` says which stacklets ask questions on their first `up`.

Some stacklets ask the admin something before they install (`backup`
which disk, `ai` which model): their first `up` needs a terminal. A
screen that offers to add a stacklet cannot answer those questions yet,
so it offers the terminal command for these instead of a button, and it
learns which they are from `stack list --json` (`asks_on_first_up`),
since it cannot read the stacklets' directories.

A stacklet whose questions are forms (ADR-021) says itself whether any
are left: `questions(ctx, {})` returns None once its configuration holds
the answers. Messages asks one thing, the chat server's name; once the
family has named it (`stack family set`), there is nothing left to ask,
and a screen can offer Add. A hook that asks for itself, a shell hook or
`run(ctx)`, always needs the terminal, and so does one whose `questions`
fails: the terminal shows why.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import stack.docker
from stack import Stack

REPO_ROOT = Path(__file__).resolve().parents[3]


# A small extension stacklet's questions, as forms: one, until its
# configuration holds the answer.
KETTLE_FORMS = '''
from stack.forms import Form

LITRES = Form(id="kettle.litres", title="Kettle", text="How much it holds.",
              schema={"type": "object", "properties": {"litres": {"type": "integer"}},
                      "required": ["litres"]})


def questions(ctx, answers):
    return None if ctx.cfg("litres") else LITRES.pending(answers)


def apply(ctx, answers):
    ctx.cfg("litres", str(answers["litres"]))
'''
KETTLE_ASKS_ITSELF = '''
def run(ctx):
    input("How much does it hold? ")
'''
KETTLE_FAILS = '''
def questions(ctx, answers):
    raise RuntimeError("the kettle's scale is missing")
'''


def listed(tmp_path, monkeypatch, server_name: str, kettle: str = "", toml: str = "") -> dict:
    """`stack list` by stacklet id. `kettle` is the `on_configure.py` of an
    extension stacklet `kettle`, and `toml` more of stack.toml."""
    if kettle:
        hooks = tmp_path / "extensions" / "kettle" / "hooks"
        hooks.mkdir(parents=True)
        (hooks.parent / "stacklet.toml").write_text('id = "kettle"\nname = "Kettle"\ntype = "host"\n')
        (hooks / "on_configure.py").write_text(kettle)
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\nhost = "server.lan"\n'
        f'extension_dirs = ["{tmp_path / "extensions"}"]\n'
        f'\n[messages]\nserver_name = "{server_name}"\n{toml}')
    (tmp_path / ".stack").mkdir(exist_ok=True)
    # No Docker in the unit lane: every stacklet reads as not running.
    monkeypatch.setattr(stack.docker, "project_states", lambda: {})
    answer = Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path).list()
    return {s["id"]: s for s in answer["stacklets"]}


@pytest.fixture
def rows(tmp_path, monkeypatch) -> dict:
    """A stack whose chat server has no name yet."""
    return listed(tmp_path, monkeypatch, "")


@pytest.mark.parametrize("sid", ["ai", "backup", "messages", "infra"])
def test_a_stacklet_that_asks_on_its_first_up_says_so(rows, sid):
    assert rows[sid]["asks_on_first_up"] is True


@pytest.mark.parametrize("sid", ["photos", "docs", "code", "memory"])
def test_a_stacklet_that_just_installs_says_it_does_not_ask(rows, sid):
    assert rows[sid]["asks_on_first_up"] is False


def test_once_the_chat_server_is_named_messages_has_nothing_left_to_ask(tmp_path, monkeypatch):
    assert listed(tmp_path, monkeypatch, "simpson")["messages"]["asks_on_first_up"] is False


def test_what_a_stacklet_needs_first_is_listed(rows):
    """`stack up` refuses a stacklet whose requirement is not set up, so a
    screen says what it comes after instead of offering Add."""
    assert rows["agent"]["requires"] == ["messages"]
    assert rows["memory"]["requires"] == ["code"]
    assert rows["photos"]["requires"] == []


# ── The general rule, for any stacklet ───────────────────────────────────

@pytest.mark.parametrize(("toml", "asks"), [("", True), ('\n[kettle]\nlitres = "2"\n', False)])
def test_a_stacklet_with_forms_asks_until_its_configuration_holds_the_answers(
        tmp_path, monkeypatch, toml, asks):
    rows = listed(tmp_path, monkeypatch, "", kettle=KETTLE_FORMS, toml=toml)

    assert rows["kettle"]["asks_on_first_up"] is asks


def test_a_stacklet_that_asks_for_itself_needs_the_terminal(tmp_path, monkeypatch):
    rows = listed(tmp_path, monkeypatch, "", kettle=KETTLE_ASKS_ITSELF)

    assert rows["kettle"]["asks_on_first_up"] is True


def test_a_stacklet_whose_questions_fail_needs_the_terminal_and_the_list_still_answers(
        tmp_path, monkeypatch, capsys):
    rows = listed(tmp_path, monkeypatch, "", kettle=KETTLE_FAILS)

    assert rows["kettle"]["asks_on_first_up"] is True
    assert rows["photos"]["asks_on_first_up"] is False
    assert "scale" not in capsys.readouterr().out


def test_listing_what_is_asked_writes_nothing(tmp_path, monkeypatch):
    """Rendering a stacklet's env generates its secrets; reading its
    questions must not, or `stack list` would write a secret for every
    stacklet it lists."""
    listed(tmp_path, monkeypatch, "", kettle=KETTLE_FORMS)

    assert sorted(p.name for p in (tmp_path / ".stack").iterdir()) == []
    assert "[kettle]" not in (tmp_path / "stack.toml").read_text()


# ── Answered in a form, or only in a terminal ────────────────────────────
# A screen can answer forms, so it offers Add for a stacklet whose
# questions are forms (`asks_in_forms`), and the terminal command for one
# that asks for itself or whose questions fail.

def test_questions_in_forms_can_be_answered_on_a_screen(tmp_path, monkeypatch):
    rows = listed(tmp_path, monkeypatch, "", kettle=KETTLE_FORMS)

    assert (rows["kettle"]["asks_on_first_up"], rows["kettle"]["asks_in_forms"]) == (True, True)


@pytest.mark.parametrize("hook", [KETTLE_ASKS_ITSELF, KETTLE_FAILS])
def test_a_hook_that_asks_for_itself_or_fails_is_not_a_form(tmp_path, monkeypatch, hook):
    rows = listed(tmp_path, monkeypatch, "", kettle=hook)

    assert (rows["kettle"]["asks_on_first_up"], rows["kettle"]["asks_in_forms"]) == (True, False)


def test_nothing_left_to_ask_is_no_form_either(tmp_path, monkeypatch):
    rows = listed(tmp_path, monkeypatch, "", kettle=KETTLE_FORMS, toml='\n[kettle]\nlitres = "2"\n')

    assert rows["kettle"]["asks_in_forms"] is False


@pytest.mark.parametrize("sid", ["ai", "backup", "infra"])
def test_the_shipped_questions_are_forms(rows, sid):
    assert rows[sid]["asks_in_forms"] is True


# ── A setup that needs a person at the Mac ───────────────────────────────
# Some setups stop for macOS: a permission dialog, a grant in System
# Settings. Nobody at a screen elsewhere can answer those, whatever the
# stacklet's questions are, so `stack list` says so (`attended_setup`).

def test_a_setup_that_needs_a_person_at_the_mac_says_so(rows):
    assert rows["backup"]["attended_setup"] is True


@pytest.mark.parametrize("sid", ["ai", "photos", "messages"])
def test_the_others_need_nobody_at_the_mac(rows, sid):
    assert rows[sid]["attended_setup"] is False
