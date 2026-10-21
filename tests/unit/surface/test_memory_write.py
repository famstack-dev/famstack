"""`stack memory write`, described by what a caller sees it do.

This file is the pattern for testing a command's behaviour. Copy it.

The real entry point. Marge runs the command as `./stack memory write`
does, through `Stack.run_cli_command("memory", "write", [...])`, against a
fabricated instance and in an interpreter shaped like `./stack`'s
(`as_stack_runs_it`). So the test passes only if the command loads, reads
its arguments and its configuration the way it does for a person, and
reaches Forgejo the way it does on an instance. Importing `write.py` and
calling `run` with a hand-made config skips all of that, and a test like
that once stayed green while the command could not even load.

A real HTTP server, not a mock. Forgejo is the stand-in from
`test_memory_card_links` (`forgejo`, `contents`): a `pytest-httpserver` in
this process that serves the contents API over a real git repository and
refuses an update whose blob changed since it was read, as Forgejo does.
The command talks to it over HTTP with the client it uses in production.
Nothing in the command is patched, and no Docker is needed.

What to assert: only what a caller can observe, in the words of `dsl`.
What the person was told (`marge.was_told(...)`, `marge.answer`), and the
memory repo afterwards: `memory.new_commits()` against
`commit(by=..., saying=...)`, and `memory.page(path)`. Those are the
promises: they hold whatever happens to the code underneath. They are
plain values, so a plain `assert` and pytest's diff explain a failure.

What not to assert: private helpers, which function was called, the
request the client sent, config values. A test that pins those breaks on
every refactor and never on a bug.

Each test names the behaviour, says Given/When/Then, and says in one
sentence why the case matters. The data is the Simpsons'.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from .conftest import REPO, make_stack, write_instance
from .dsl import Memory, Person, commit

sys.path.insert(0, str(REPO / "tests" / "unit" / "stacklets"))
from test_memory_card_links import _git, contents, forgejo  # noqa: E402, F401

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
def memory(tmp_path) -> Memory:
    """family/memory on Forgejo, holding Homer's camping list."""
    repo = tmp_path / "forgejo" / "memory"
    (repo / LIST).parent.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / LIST).write_text(CAMPING, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--author=homer <homer@springfield.example>",
         "-m", "chore(memory): homer added the camping list")
    _git(repo, "update-server-info")
    return Memory(repo)


@pytest.fixture
def springfield(tmp_path, httpserver, forgejo, contents, as_stack_runs_it):  # noqa: F811
    """The instance, with the code stacklet at the Forgejo stand-in and the
    memory bot's token, as `stack up memory` leaves it."""
    instance = write_instance(tmp_path, extra=f"\n[code]\nport = {httpserver.port}\n")
    (instance / ".stack" / "secrets.toml").write_text(
        'global__ADMIN_PASSWORD = "donuts"\nmemory__MEMORY_BOT_TOKEN = "bottoken"\n')
    return make_stack(instance)


@pytest.fixture
def marge(springfield, tmp_path: Path) -> Person:
    return Person("marge", springfield, tmp_path)


# ── Writing a page ───────────────────────────────────────────────────────

def test_a_new_version_is_one_commit_by_the_person_saying_what_changed(memory, marge):
    """Given Homer's camping list, when Marge writes a version with one
    item reworded, then the memory has exactly one new commit, authored by
    Marge, whose subject says what changed, and the page has her text.

    The memory's history is read by the family and by the agent; a commit
    that names its author and its change is what makes it answerable.
    """
    marge.writes(LIST, TENT_FOR_FOUR)

    assert memory.new_commits() == [commit(by="marge", saying="Tent -> Tent for four")]
    assert memory.page(LIST) == TENT_FOR_FOUR
    assert marge.was_told(f"Wrote {LIST} (by marge)", "Tent -> Tent for four")


def test_writing_the_same_text_again_commits_nothing(memory, marge):
    """Given the camping list, when Marge writes exactly what is there,
    then nothing is committed and she is told nothing changed.

    The agent retries a write it is not sure of; a retry must not fill the
    history with empty commits.
    """
    marge.writes(LIST, CAMPING)

    assert memory.new_commits() == []
    assert marge.was_told("nothing to commit")


def test_a_page_whose_frontmatter_does_not_parse_is_refused(memory, marge):
    """Given the camping list, when Marge writes a version whose
    frontmatter does not parse, then nothing is committed and she is told
    which page and what is wrong.

    The wiki drops a page it cannot parse, so a bad write would make the
    list vanish for the family without anyone being told.
    """
    marge.writes(LIST, CAMPING.replace("title: Camping\n", "title: Camping\nSpringfield trip\n"))

    assert memory.new_commits() == []
    assert memory.page(LIST) == CAMPING
    assert marge.was_told(LIST, "frontmatter does not parse")


def test_a_dry_run_says_what_would_change_and_commits_nothing(memory, marge):
    """Given the camping list, when Marge writes a version with `--dry-run`,
    then nothing is committed and she is told what the write would do.

    A preview is how a person or the agent checks a rewrite before it
    lands; one that wrote anyway would make checking pointless.
    """
    marge.writes(LIST, TENT_FOR_FOUR, dry_run=True)

    assert memory.new_commits() == []
    assert memory.page(LIST) == CAMPING
    assert marge.was_told("nothing committed", "Tent -> Tent for four")
