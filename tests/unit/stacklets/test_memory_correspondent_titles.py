"""A correspondent written by hand gets its title: migration memory/0009.

Before correspondents were cards, the memory stacklet documented a shape
for a correspondent's file that named it in `canonical`. A card's name is
its `title` (ADR-017 §5). The pass is read over the curator's clone of
memory's test instance (`running_memory`);
`tests/unit/surface/test_memory_card_edit.py` runs it as
`stack up memory` does, after every migration before it, and edits the
card afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from stacklets.memory.lib import correspondent_titles_for
from tests.memory.unit.instance import RunningMemory, running_memory

PLANT = "family/correspondents/springfield-nuclear-power-plant.md"
HEAD = "---\ntype: correspondent\ncard_id: correspondent-springfield-nuclear-power-plant\n"
TAIL = "address: \"100 Industrial Way, Springfield\"\n---\n\n# Springfield Nuclear Power Plant\n"


@contextmanager
def _holding(tmp_path: Path, text: str) -> Iterator[RunningMemory]:
    """The memory, holding the plant's file as `text`."""
    with running_memory(tmp_path, {PLANT: text}) as memory:
        yield memory


def _changes(tmp_path: Path, text: str):
    with _holding(tmp_path, text) as memory:
        return correspondent_titles_for(memory.vault)


def test_its_name_becomes_its_title_where_it_stands(tmp_path):
    found = _changes(tmp_path, HEAD + "canonical: \"Springfield Nuclear Power Plant\"\n" + TAIL)

    assert found.changes == {PLANT: HEAD + "title: \"Springfield Nuclear Power Plant\"\n" + TAIL}


def test_a_card_with_a_title_loses_the_name_it_repeats(tmp_path):
    found = _changes(tmp_path, HEAD + "title: Springfield Nuclear Power Plant\n"
                     "canonical: Springfield Nuclear Power Plant\n" + TAIL)

    assert found.changes == {PLANT: HEAD + "title: Springfield Nuclear Power Plant\n" + TAIL}
    assert found.left == []


def test_a_name_that_is_not_its_title_is_kept_as_an_alias_and_said(tmp_path):
    """A name the household wrote is not dropped for being in the old field."""
    found = _changes(tmp_path, HEAD + "title: Springfield Nuclear Power Plant\n"
                     "canonical: Burns' Plant\n" + TAIL)

    assert found.changes[PLANT].count("Burns' Plant") == 1
    assert "aliases:\n  - Burns' Plant\n" in found.changes[PLANT]
    assert found.left == [f"{PLANT}: its name \"Burns' Plant\" is kept as an alias of "
                          "'Springfield Nuclear Power Plant'"]


def test_run_again_it_finds_nothing(tmp_path):
    with _holding(tmp_path, HEAD + "canonical: Springfield Nuclear Power Plant\n" + TAIL) as memory:
        found = correspondent_titles_for(memory.vault)
        memory.edited_in_forgejo({PLANT: found.changes[PLANT]}, subject="titled")

        assert correspondent_titles_for(memory.vault).changes == {}
