"""`stack memory write`, described by what a caller sees it do.

This file is the pattern for testing a command's behaviour. Copy it.

The real entry point. Marge runs the command as `./stack memory write`
does, through `Stack.run_cli_command("memory", "write", [...])`, against a
fabricated instance. So the test passes only if the command loads, reads
its arguments and its configuration the way it does for a person, and
reaches the curator the way it does on an instance. Importing `write.py`
and calling `run` with a hand-made config skips all of that, and a test
like that once stayed green while the command could not even load.
(`test_every_command_loads.py` loads every command in an interpreter
shaped like `./stack`'s.)

Real servers, not mocks, from memory's test instance (`running_memory`).
The command reaches core's card door on this Mac, which passes the write on
to the curator's API, whose card store commits to the curator's clone and
pushes to Forgejo, a bare git repository. Nothing in the command is
patched, and no Docker is needed.

What to assert: only what a caller can observe. The answer's fields
(`marge.answer`: whether it committed, by whom, the change it names), the
facts a person must be told where the answer has no field for them
(`marge.was_told(...)` with a path, an id, a command to run), and the
memory afterwards: `memory.new_commits()` against
`commit(by=..., saying=...)`, and `memory.page(path)`. Those are the
promises: they hold whatever happens to the code underneath. They are
plain values, so a plain `assert` and pytest's diff explain a failure.

What not to assert: a sentence's wording, private helpers, which function
was called, the request the client sent, config values. A test that pins
those breaks on every refactor and never on a bug.

Each test names the behaviour, says Given/When/Then, and says in one
sentence why the case matters. The data is the Simpsons'.
"""

from __future__ import annotations

import pytest

from tests.memory.unit.instance import running_memory

from tests.memory.unit.dsl import commit

LIST = "family/lists/camping.md"

CAMPING = """---
type: list
card_id: list-camping
title: Camping
---

# Camping

- [ ] Tent
- [x] Marshmallows
"""

TENT_FOR_FOUR = CAMPING.replace("- [ ] Tent", "- [ ] Tent for four")


@pytest.fixture
def memory(tmp_path):
    """The memory, holding Homer's camping list."""
    with running_memory(tmp_path, {LIST: CAMPING}, seeded_by="homer",
                        seeded_as="chore(memory): homer added the camping list") as running:
        yield running


@pytest.fixture
def marge(memory):
    return memory.person("marge")


# ── Writing a page ───────────────────────────────────────────────────────

def test_a_new_version_is_one_commit_by_the_person_saying_what_changed(memory, marge):
    """Given Homer's camping list, when Marge writes a version with one
    item reworded, then the memory has exactly one new commit, authored by
    Marge, whose subject says what changed, and the page has her text.

    The memory's history is read by the family and by the agent; a commit
    that names its author and its change is what makes it answerable.
    """
    answer = marge.writes(LIST, TENT_FOR_FOUR)

    assert memory.new_commits() == [commit(by="marge", saying="Tent -> Tent for four")]
    assert memory.page(LIST) == TENT_FOR_FOUR
    assert (answer["committed"], answer["by"]) == (True, "marge")
    assert "Tent for four" in answer["summary"]


def test_a_version_written_while_forgejo_is_down_is_written_and_says_it_waits(memory, marge):
    """Given Forgejo is down, when Marge writes a version of Homer's camping
    list, then she is told it is written and that Forgejo does not have it
    yet, and once Forgejo is back a pull brings it there as her commit.

    The curator keeps the change, so the answer must not read as a failure:
    a person or the agent who reads one writes the page again. It names
    what does wait, so whoever looks in Forgejo knows where the change is.
    """
    with memory.forgejo_down():
        answer = marge.writes(LIST, TENT_FOR_FOUR)

    assert (answer["committed"], answer["pending"]) == (True, True)
    assert marge.was_told("stack memory pull")
    marge.runs("memory", "pull", [])
    assert memory.new_commits() == [commit(by="marge", saying="Tent -> Tent for four")]


def test_writing_the_same_text_again_commits_nothing(memory, marge):
    """Given the camping list, when Marge writes exactly what is there,
    then nothing is committed and she is told nothing changed.

    The agent retries a write it is not sure of; a retry must not fill the
    history with empty commits.
    """
    answer = marge.writes(LIST, CAMPING)

    assert memory.new_commits() == []
    assert (answer["ok"], answer["committed"]) == (True, False)


def test_a_page_whose_frontmatter_does_not_parse_is_refused(memory, marge):
    """Given the camping list, when Marge writes a version whose
    frontmatter does not parse, then nothing is committed and she is told
    which page and what is wrong.

    The wiki drops a page it cannot parse, so a bad write would make the
    list vanish for the family without anyone being told.
    """
    answer = marge.writes(LIST, CAMPING.replace("title: Camping\n", "title: Camping\nSpringfield trip\n"))

    assert memory.new_commits() == []
    assert memory.page(LIST) == CAMPING
    assert LIST in answer["error"] and "frontmatter" in answer["error"]


def test_a_dry_run_says_what_would_change_and_commits_nothing(memory, marge):
    """Given the camping list, when Marge writes a version with `--dry-run`,
    then nothing is committed and she is told what the write would do.

    A preview is how a person or the agent checks a rewrite before it
    lands; one that wrote anyway would make checking pointless.
    """
    answer = marge.writes(LIST, TENT_FOR_FOUR, dry_run=True)

    assert memory.new_commits() == []
    assert memory.page(LIST) == CAMPING
    assert (answer["preview"], answer["committed"]) == (True, False)
    assert "Tent for four" in answer["summary"]
