"""`stack memory history`, whose changes it lists as the family's.

The command reads the memory's clone on this Mac and leaves the machine's
work out unless asked (`--all`). Which work is the machine's is the
memory's one rule (`activity.is_machine`), the one the dashboard words
its activity by: a change by a bot or the admin, or a rebuild a bot
committed on its own with a person as author because the records are
theirs. A command and a page that disagreed on it would tell the family
two stories about the same night.

Driven as `./stack memory history` runs, through `Stack.run_cli_command`,
against a real git repository at the clone's place in the data dir. The
data is the Simpsons'.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from .conftest import make_stack, write_instance
from .dsl import Memory, Person

NOW = datetime.now().astimezone().replace(microsecond=0)


def diary_entries(sender: str, n: int, *, read_again: bool = False) -> dict[str, str]:
    return {f"family/diary/entries/2026/03/{sender}-{i}.md":
            f"---\ntype: diary\ntitle: {sender.capitalize()}'s memo {i}\n---\n"
            + ("\nRead again.\n" if read_again else "")
            for i in range(n)}


@pytest.fixture
def marge(tmp_path, as_stack_runs_it) -> Person:
    """Marge, on an instance whose memory clone holds a night of the diary:
    Homer's memos updated before bots committed as themselves, Marge's note
    filed by the archivist, and the diary compile writing her 30 entries
    again as the memory bot."""
    instance = write_instance(tmp_path)
    stack = make_stack(instance)
    vault = Memory.new(stack.data / "memory" / "vault")
    vault.changes("update: 3 diary entries", by="homer", at=NOW - timedelta(days=3),
                  files=diary_entries("homer", 3))
    vault.changes("capture: Tent is torn", by="marge", committed_by="archivist-bot",
                  at=NOW - timedelta(days=1),
                  files={"family/camping/tent.md": "---\ntype: note\ntitle: Tent is torn\n---\n"})
    vault.changes("learn: 30 diary entries", by="marge", committed_by="memory-bot",
                  at=NOW - timedelta(hours=2), files=diary_entries("marge", 30))
    vault.changes("update: 30 diary entries", by="marge", committed_by="memory-bot",
                  at=NOW - timedelta(hours=1), files=diary_entries("marge", 30, read_again=True))
    return Person("marge", stack, tmp_path)


def test_the_diary_compiles_rebuild_is_not_listed_as_a_persons_change(marge):
    """Given the diary compile writing Marge's entries again, when she asks
    what changed, then the rebuild is not listed under her name; what a
    bot wrote for her is (her note, her new memos), and so is Homer's
    update from before bots committed as themselves. With `--all` the
    rebuild is there."""
    listed = marge.runs("memory", "history", [])["changes"]

    assert [(c["by"], c["what"]) for c in listed] == [
        ("marge", "learn: 30 diary entries"),
        ("marge", "capture: Tent is torn"),
        ("homer", "update: 3 diary entries"),
    ]
    everything = marge.runs("memory", "history", ["--all"])["changes"]
    assert ("marge", "update: 30 diary entries") in [(c["by"], c["what"]) for c in everything]
