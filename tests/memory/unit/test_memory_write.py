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

# Homer's note as the archivist filed it: his message quoted under
# `## Sources`, its entry in the sidecar beside it (ADR-018).
NOTE = "family/notes/2026/10/trip-7903f2.md"
TRIP = """---
type: note
card_id: note-7903f2aa01
title: Camping trip
---

# Camping trip

We leave on Friday at 4.

## Sources

> we leave fri at 4
"""
TRIP_SIDECAR = """{
  "card_id": "note-7903f2aa01",
  "filed_by": "homer",
  "sources": [{"role": "original", "person": "homer", "time": "2026-10-20T08:00:00+00:00",
               "channel": "chat", "kind": "text", "id": "$trip:simpson"}],
  "made": {"at": "2026-10-20T08:00:05+00:00"}
}
"""


@pytest.fixture
def memory(tmp_path):
    """The memory, holding Homer's camping list and his note about the trip."""
    with running_memory(tmp_path, {LIST: CAMPING, NOTE: TRIP,
                                      NOTE.replace(".md", ".json"): TRIP_SIDECAR}, seeded_by="homer",
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


def test_a_version_that_changes_what_was_sent_is_refused_saying_how_to_correct_it(memory, marge):
    """Given Homer's note quoting the words he sent, when Marge writes a
    version that changes them, then nothing is committed and she is told
    the two ways to correct a note: in Forgejo, or with `stack memory
    reprocess --msg`.

    A quote keeps what a person sent (ADR-018 §4). Rewritten, the next
    rebuild reads her words as Homer's, and his are gone.
    """
    answer = marge.writes(NOTE, TRIP.replace("> we leave fri at 4", "> we leave fri at 5"))

    assert memory.new_commits() == []
    assert memory.page(NOTE) == TRIP
    assert NOTE in answer["error"]
    assert "Forgejo" in answer["error"] and "stack memory reprocess --msg" in answer["error"]


def test_a_version_that_keeps_what_was_sent_is_written(memory, marge):
    """Given Homer's note, when Marge rewrites what it says above its
    sources, then her version is written: the rule guards the sources only."""
    marge.writes(NOTE, TRIP.replace("Friday at 4.", "Friday at 4, after work."))

    assert memory.new_commits() == [commit(by="marge", saying="")]
