"""Who a commit to the memory repo is by, and who wrote it, whichever way
the change got there.

A person commits as `<person id>@<Matrix server name>`: Marge, on an
instance whose Matrix server is `simpson`, is `marge <marge@simpson>`.
The address mirrors her Matrix id, so `git log --author` and Forgejo
group her commits, and no real address goes into a repository that is
meant to outlive the stack. A bot keeps its Forgejo account's address.

Three ways a person's change reaches the memory, each from its own
door, and each must name her the same way. All three are committed by the
curator's card store, which knows the server name from its environment:

- the archivist files what she sends it (its capture pipeline and its
  mirror, in the bot runner, through the curator's API);
- `stack memory card edit`, through the memory stacklet's card endpoint
  in the curator;
- `stack memory reprocess --msg ... --by`, from the host through core's
  card door.

Author and committer say who (ADR-015): the author is the person a change
is by or for, the committer the account that wrote the commit. A bot
commits as its own account, so a filing the archivist made for Marge
never reads as an edit Marge made in Forgejo herself.

What is real: the memory (`running_memory`, memory's test instance), the
card endpoint as the curator builds it, core's card door, the `./stack`
commands through their loader, and the bot runner's commands, each in a
process of its own as `docker exec` starts it: the archivist's capture
pipeline and mirror, the model half of a refile, the list archive. What
stands in: the model, an HTTP server answering the chat completions API,
and Paperless, which knows no names; and the diary's reading of the room
and its model, so the compile's write starts from the cards it made. The
data is the Simpsons'.

A test that runs a command in the bot runner takes a second or more for
each: it starts an interpreter of its own, as `docker exec` does.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date

import pytest

from stack.bot_runner import call
from tests.memory.unit.instance import Model, running_memory
from tests.memory.unit.simpsons_cards import ONTOLOGY, VAULT

from tests.memory.unit.dsl import commit
from .test_memory_card_edit import HAND_WRITTEN, _memory_migrations_run

# A note of Marge's that was filed before the test began.
ART_SUPPLIES = "note-7f3a9c21e4"
MEMORY_BOT = "memory-bot <memory-bot@local>"
ARCHIVIST = "archivist-bot <archivist-bot@local>"
THE_ARCHIVIST = "/stacklets/docs/bot/cli_entrypoint.py"
THE_MEMORY_BOT = "/stacklets/memory/bot/cli_entrypoint.py"


# ── The instance ─────────────────────────────────────────────────────────

@pytest.fixture
def server_name() -> str | None:
    """The instance's Matrix server name; a test overrides it with
    `@pytest.mark.parametrize("server_name", [None])`."""
    return "simpson"


@pytest.fixture
def model(httpserver) -> Model:
    return Model(httpserver)


@pytest.fixture
def memory(tmp_path, request, server_name, httpserver, model):
    """The instance, its Matrix server named `server_name`, its memory
    holding the Simpsons' cards as a migration left them, or the files a
    test names with `@pytest.mark.parametrize("memory", [files], indirect=True)`."""
    httpserver.expect_request("/paperless/api/tags/").respond_with_json({"results": []})
    files = getattr(request, "param", None) or {**VAULT, "ontology.toml": ONTOLOGY}
    with running_memory(tmp_path, files, server_name=server_name, seeded_by="memory-bot",
                        seeded_as="cards: the Simpsons", model=model.url,
                        paperless=httpserver.url_for("/paperless")) as running:
        yield running


@pytest.fixture
def marge(memory):
    return memory.person("marge")


def the_archivist_files(words: str, *, by: str) -> str:
    """The archivist files `by`'s words through its capture pipeline and its
    mirror, in the bot runner, as `stack memory capture` asks it; its
    receipt, which `stack memory write` reads too."""
    return call(THE_ARCHIVIST, "capture", words, "--by", by)


def card_in(receipt: str) -> str:
    """The id of the card a receipt says was filed."""
    return re.search(r"^  card: (\S+)$", receipt, re.M).group(1)


# What the model makes of Marge's words, and of her correction of them.
TENT = {"title": "Tent for the camping trip", "tags": [], "persons": [],
        "clean": "Bring the big tent."}
AT_FLANDERS = {**TENT, "clean": "Bring the big tent. It is at Flanders'."}


# ── The diary compile ────────────────────────────────────────────────────

class DiaryCompile:
    """The diary compile in the curator, from the cards it made of the
    memories room on: it reads the diary's records from the curator, plans
    what to write, and commits it through the curator, as `stack memory
    diary` does after reading the room and asking the model. Its
    environment is the one the curator gives its children."""

    def __init__(self, command, environ: dict[str, str]):
        self.command, self.environ = command, environ

    def writes(self, cards) -> None:
        c = self.command
        memory = c.MemoryAPI.from_container(self.environ)
        tree = c._read_diary_tree(memory, "family")
        plan = c.diary_card.plan({p: t for p, (_, t) in tree.items()}, cards,
                                 bucket="family", complete=False)
        by_path = {c.diary_card.card_path(card, bucket="family"): card for card in cards}
        assert c._commit(memory, plan, tree, by_path) == set()


@pytest.fixture
def diary(memory):
    from stacklets.memory.bot.cli import diary
    return DiaryCompile(diary, memory.environ)


def marges_entry(n: int, summary: str):
    """One of Marge's voice memos in the memories room, as the compile makes its card."""
    from stacklets.memory.bot.diary_card import Card
    return Card(entry_id=f"c5e1a0f39{n}", on=date(2026, 3, n + 1), confidence="sent",
                kind="voice", sender="marge", at=1772400000000 + n, event_ids=[f"$memo{n}:simpson"],
                body="Bart played the saxophone at the school concert.",
                title=f"School concert {n}", summary=summary, persons=["bart"])


# ── One person, one address ──────────────────────────────────────────────

def test_a_person_commits_under_one_address_on_every_path_and_the_bot_as_itself(
        memory, model, marge):
    """Given Marge on an instance whose Matrix server is `simpson`, when the
    archivist files a note of hers, she changes Bart's card with `stack
    memory card edit`, and she refiles her note with a correction, then
    each of the three commits is authored by `marge <marge@simpson>` and
    committed by the bot whose account wrote it: the archivist's mirror by
    the archivist, the card endpoint and the host's refile by the memory bot.

    Each door reads the server name from where it runs: the bot runner's
    and the curator's environment, the host's stack.toml. One rule over
    three readings is what keeps her commits under one address; two
    copies of it once gave her two. A commit whose committer is a person
    is a person's own change (ADR-015): written as Marge, a filing would
    read as an edit she made in Forgejo herself.
    """
    model.answers(TENT)
    note = card_in(the_archivist_files("bring the big tent", by="marge"))
    marge.edits("person-bart", "--set", "description=Rides a skateboard.")
    model.answers(AT_FLANDERS)
    marge.refiles(note, correction="the big tent is at Flanders'")

    assert memory.new_commits() == [commit(by="marge", saying="capture: Tent for the camping trip"),
                                    commit(by="marge", saying="edit: Bart Simpson"),
                                    commit(by="marge", saying="reprocess: Tent for the camping trip")]
    assert memory.authors() == ["marge <marge@simpson>"] * 3
    assert memory.committers() == [ARCHIVIST, MEMORY_BOT, MEMORY_BOT]


@pytest.mark.parametrize("server_name", [None])
def test_without_a_server_name_a_persons_change_is_refused_and_says_where_it_comes_from(
        memory, model, marge):
    """Given an instance whose stack.toml names no Matrix server, when the
    archivist files Marge's note, she changes Bart's card and she refiles
    a note with a correction, then nothing is committed, and each command
    tells her the server name is missing and where it is set.

    An address made up in its place would put her commits under a second
    address for good: the memory's history is never rewritten.
    """
    model.answers(TENT)
    the_archivist_files("bring the big tent", by="marge")
    marge.edits("person-bart", "--set", "description=Rides a skateboard.")
    told_by_the_edit = marge.was_told("server_name", "[messages]", "stack.toml")
    model.answers({**TENT, "title": "Art supplies for next time", "clean": "Paper in A3."})
    marge.refiles(ART_SUPPLIES, correction="the paper is A3")

    assert memory.new_commits() == []
    assert told_by_the_edit
    assert marge.was_told("server_name", "[messages]", "stack.toml")


# ── The account that writes ──────────────────────────────────────────────

def test_the_diary_compile_commits_as_the_memory_bot_with_the_sender_as_author(memory, diary):
    """Given two of Marge's memos the diary compile filed, when it compiles
    them again after Homer answered both, then the update is authored by Marge,
    whose entries they are, and committed by the memory bot, which wrote
    them on its own.

    The compile runs with the admin's credentials; what it commits is the
    memory bot's work all the same, and the dashboard tells a rebuild
    from a person's change by that.
    """
    diary.writes([marges_entry(1, "Lisa plays."), marges_entry(2, "Lisa plays.")])
    answered = [("homer", "Proud of her.")]
    diary.writes([replace(marges_entry(1, "Lisa plays the saxophone."), replies=answered),
                  replace(marges_entry(2, "Lisa plays the saxophone."), replies=answered)])

    assert memory.new_commits() == [commit(by="marge", saying="learn: 2 diary entries"),
                                    commit(by="marge", saying="update: 2 diary entries")]
    assert memory.authors() == ["marge <marge@simpson>"] * 2
    assert memory.committers() == [MEMORY_BOT] * 2


# A shopping list with apples ticked off on a day that is over.
SHOPPING = {"ontology.toml": ONTOLOGY, "family/lists/einkaufsliste.md": (
    "---\ntype: list\ncard_id: list-einkaufsliste\ntitle: Einkaufsliste\nsections:\n  - Obst\n---\n\n"
    "## Obst\n- [x] Äpfel ✅ 2026-10-11\n- [ ] Birnen\n")}


@pytest.mark.parametrize("memory", [SHOPPING], indirect=True)
def test_the_list_archive_commits_as_the_memory_bot(memory):
    """Given apples ticked off a list on a day that is over, when the
    nightly list archive runs, then it moves them in one commit, authored
    and committed by the memory bot, whose work the archive is.

    The memory bot has no Forgejo account: the archive writes with the
    admin's credentials and names the memory bot as the committer.
    """
    call(THE_MEMORY_BOT, "lists")

    assert memory.new_commits() == [commit(by="memory-bot",
                                           saying="lists: archive 1 item(s) that are over")]
    assert memory.committers() == [MEMORY_BOT]


@pytest.mark.parametrize("memory", [HAND_WRITTEN], indirect=True)
def test_a_migration_is_the_memory_bots_as_author_and_as_committer(memory):
    """Given a correspondent's file written by hand in the shape from before
    cards, when the memory migrations run as `stack up memory` runs them, then every commit they make is authored and
    committed by the memory bot: nobody asked for it, and nobody but the
    memory bot is named.
    """
    _memory_migrations_run(memory.stack)

    assert memory.new_commits()
    assert set(memory.authors()) == set(memory.committers()) == {MEMORY_BOT}
