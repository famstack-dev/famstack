"""`--audience` on `history`, `list`, `topic` and `write`: only what the listeners may read.

The agent runs each memory read for the people in its room (`--audience`,
added by the host API). A person's own folder (`marge/...`) is written for
that person alone (`stack.cards.audience.readable`). So in a room with
anyone else in it, these commands show nothing from it: no change to it in
`history` (a commit's subject names the card), no list in it, and no topic
page in it, also not by name in a refusal; no list verb names its items,
and `write` refuses a page in it without reading it back. Without `--audience` the
commands read everything, as the admin does on the Mac.

Each command runs as `./stack memory <cmd>` loads it, against memory's test
instance (`running_memory`) holding Marge's own list and a shared one.
Marge's own change is made in Forgejo and pulled, as a person makes it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stack.stacklet_code import load
from tests.memory.unit.instance import running_memory

_MEMORY = Path(__file__).resolve().parents[3] / "stacklets" / "memory"


def _command(name: str):
    return load(_MEMORY, _MEMORY / "cli" / f"{name}.py")


def _list(title: str, item: str) -> str:
    return f"---\ntype: list\ncard_id: list-{title.lower()}\ntitle: {title}\n---\n\n- [ ] {item}\n"


SEED = {"marge/lists/geschenke.md": _list("Geschenke", "Bowling ball for Homer"),
        "family/lists/einkauf.md": _list("Einkauf", "Milk")}

# A topic page in Marge's own folder and one in the shared folder, as the
# curator writes the brain projection.
TOPIC_PAGES = {"marge/surprise-party/about.md": "# Surprise party\n\nAt Moe's on Friday.\n",
               "family/camping/about.md": "# Camping\n\nThe tent is in the garage.\n"}


@pytest.fixture
def store(tmp_path):
    with running_memory(tmp_path, SEED) as memory:
        brain = Path(memory.config["data_dir"]) / "memory" / "brain"
        for rel, text in TOPIC_PAGES.items():
            (brain / rel).parent.mkdir(parents=True, exist_ok=True)
            (brain / rel).write_text(text, encoding="utf-8")
        memory.edited_in_forgejo({"marge/lists/geschenke.md": _list("Geschenke", "Bowling pins")},
                                 by="marge", subject="Geschenke: pins for the bowling night")
        yield memory


@pytest.fixture
def run(store, capsys):
    """`stack memory <cmd> <args>`: what it printed and returned, as one text."""
    def _go(cmd: str, *args: str) -> str:
        try:
            answer = _command(cmd).run(list(args), None, store.config) or {}
        except SystemExit:   # `search` exits 1 when it finds nothing
            answer = {}
        return capsys.readouterr().out + str(answer.get("error", ""))
    return _go


IN_BARTS_CHAT = ("--audience", "bart")
IN_MARGES_CHAT = ("--audience", "marge")
AS_THE_ADMIN = ()


# ── history ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("scope", [(), ("marge",)])
def test_in_barts_chat_the_history_shows_no_change_to_marges_folder(run, scope):
    assert "bowling night" not in run("history", *scope, *IN_BARTS_CHAT)


def test_in_barts_chat_a_private_path_answers_as_a_missing_one(run):
    """A refusal for a folder that exists says the same as for one that does not."""
    private = run("history", "marge/lists", *IN_BARTS_CHAT)
    missing = run("history", "nobody/lists", *IN_BARTS_CHAT)

    assert private.replace("marge", "nobody") == missing


def test_in_barts_chat_a_path_through_the_shared_folder_answers_as_a_missing_one(run):
    through = run("history", "family/../marge", *IN_BARTS_CHAT)
    missing = run("history", "family/../nobody", *IN_BARTS_CHAT)

    assert through.replace("marge", "nobody") == missing


@pytest.mark.parametrize("audience", [IN_MARGES_CHAT, AS_THE_ADMIN])
def test_marge_and_the_admin_see_her_change(run, audience):
    assert "bowling night" in run("history", "marge", *audience)


def test_in_barts_chat_an_item_of_marges_list_has_no_history(run):
    assert "first appeared" not in run("history", "--item", "Bowling pins", *IN_BARTS_CHAT)


# ── list show ────────────────────────────────────────────────────────

def test_in_barts_chat_the_lists_are_the_shared_ones(run):
    shown = run("list", "show", *IN_BARTS_CHAT)

    assert "Einkauf" in shown and "Geschenke" not in shown


def test_in_barts_chat_marges_list_is_not_found_by_its_name(run):
    shown = run("list", "show", "geschenke", *IN_BARTS_CHAT)

    assert "no list 'geschenke'" in shown and "Bowling" not in shown


@pytest.mark.parametrize("audience", [IN_MARGES_CHAT, AS_THE_ADMIN])
def test_marge_and_the_admin_read_her_list(run, audience):
    assert "Bowling pins" in run("list", "show", "geschenke", *audience)


LIST_CHANGES = {
    "tick on her list": ("tick", "geschenke", "zzz"),
    "tick with no list named": ("tick", "Bowling"),
    "remove on her list": ("remove", "geschenke", "zzz"),
    "set on her list": ("set", "geschenke", "--title", "Mine"),
    "move from her list": ("move", "geschenke", "zzz", "--to", "einkauf"),
    "delete her list": ("delete", "geschenke"),
}


@pytest.mark.parametrize("change", LIST_CHANGES)
def test_in_barts_chat_no_list_change_reaches_marges_list(run, store, change):
    before = list(store.commits)
    shown = run("list", *LIST_CHANGES[change], "--as", "bart", *IN_BARTS_CHAT)

    assert "for Homer" not in shown and "pins" not in shown and "Geschenke" not in shown
    assert store.commits == before


@pytest.mark.parametrize("taken", ["geschenke", "einkauf"], ids=["hers", "the shared one"])
def test_in_barts_chat_a_taken_name_is_refused_alike_for_a_private_and_a_shared_list(
        run, store, taken):
    """A list's name is unique over every folder, so a new list cannot take
    Marge's; the refusal does not say whose it is."""
    before = list(store.commits)
    shown = run("list", "create", taken, "--title", "Gifts", "--as", "bart", *IN_BARTS_CHAT)

    assert shown == f"the list name {taken!r} is taken; choose another name"
    assert store.commits == before


def test_in_her_own_chat_marge_ticks_her_list(run):
    assert "Bowling pins" in run("list", "tick", "geschenke", "Bowling pins", "--as", "marge",
                                 *IN_MARGES_CHAT)


# ── write ────────────────────────────────────────────────────────────

def test_in_barts_chat_a_page_in_marges_folder_is_not_written(run, store, tmp_path):
    """The refusal reads nothing of the page back: no item it held is named."""
    page = tmp_path / "page.md"
    page.write_text("---\ntype: list\ntitle: Geschenke\n---\n\n- [ ] Socks\n", encoding="utf-8")
    before = list(store.commits)

    shown = run("write", "marge/lists/geschenke.md", "--by", "bart", "--from", str(page),
                *IN_BARTS_CHAT)

    assert "pins" not in shown and "Bowling" not in shown and "read only in" in shown
    assert store.commits == before


# ── topic ────────────────────────────────────────────────────────────

def test_in_barts_chat_the_topics_are_the_shared_ones(run):
    shown = run("topic", *IN_BARTS_CHAT)

    assert "camping" in shown and "surprise-party" not in shown


def test_in_barts_chat_marges_topic_is_not_read(run):
    """The refusal names the shared topics to choose from, not hers."""
    shown = run("topic", "surprise-party", *IN_BARTS_CHAT)

    assert "Moe's" not in shown and shown.rstrip().endswith("topics: camping")


@pytest.mark.parametrize("through", ["../marge/surprise-party", "family/../marge/surprise-party"])
def test_in_barts_chat_a_path_out_of_the_shared_folder_reads_no_topic_of_marges(run, through):
    """Given a topic named as a path that leaves another folder, then nothing
    of Marge's page is shown, and it answers as a topic that does not exist."""
    shown = run("topic", through, *IN_BARTS_CHAT)
    missing = run("topic", through.replace("marge", "nobody"), *IN_BARTS_CHAT)

    assert "Moe's" not in shown
    assert shown.replace("marge", "nobody") == missing


# ── search ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("scope", ["family/../marge", "family/../marge/", "../marge"])
def test_in_barts_chat_a_search_scope_out_of_the_shared_folder_finds_nothing_of_marges(
        run, scope):
    assert "Bowling" not in run("search", "Bowling", "--scope", scope, *IN_BARTS_CHAT)


@pytest.mark.parametrize("audience", [IN_MARGES_CHAT, AS_THE_ADMIN])
def test_marge_and_the_admin_read_her_topic(run, audience):
    assert "Moe's" in run("topic", "surprise-party", *audience)
