"""The status a slow turn shows: when it appears, and what it says.

A turn answered quickly gets no status at all. A slow one gets one
status message, a line appended as the agent moves on (looking, looking
closer, writing down), never faster than one line every ten seconds, and
one "still on it" when nothing new happened for a while. The lines are
picked from a few per phase so the same turn never says the same thing
twice.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "stacklets" / "agent" / "runtime"))

from status_trace import (  # noqa: E402
    FIRST_AFTER, GAP, GIVE_UP_AFTER, PHRASES, STILL_AFTER, Trace, phase_of,
)

DE = PHRASES["de"]


def _first(options):
    return options[0]


def _trace() -> Trace:
    return Trace(started=0.0, lang="de", pick=_first)


def test_a_quick_answer_gets_no_status():
    trace = _trace()
    trace.tools(["memory_search"])
    assert trace.due(at=FIRST_AFTER - 0.5) is None


def test_a_slow_turn_says_what_the_agent_is_doing():
    trace = _trace()
    trace.tools(["memory_search"])
    assert trace.due(at=FIRST_AFTER) == [DE["look"][0]]


def test_looking_again_after_a_look_reads_as_checking_closer():
    trace = _trace()
    trace.tools(["memory_search"])
    trace.due(at=FIRST_AFTER)
    trace.tools(["read_file"])
    assert trace.due(at=FIRST_AFTER + 1) is None, "not faster than one line in ten seconds"
    assert trace.due(at=FIRST_AFTER + GAP) == [DE["look"][0], DE["check"][0]]


def test_writing_to_a_list_is_its_own_line():
    trace = _trace()
    trace.tools(["exec"])
    trace.due(at=FIRST_AFTER)
    trace.tools(["list_edit"])
    assert trace.due(at=FIRST_AFTER + GAP)[-1] == DE["write"][0]


def test_nothing_new_for_a_while_says_it_is_still_at_it_once():
    trace = _trace()
    trace.tools(["memory_search"])
    trace.due(at=FIRST_AFTER)
    assert trace.due(at=FIRST_AFTER + GAP) is None, "nothing new yet"
    assert trace.due(at=FIRST_AFTER + STILL_AFTER)[-1] == DE["still"][0]
    assert trace.due(at=FIRST_AFTER + STILL_AFTER + GAP * 3) is None


def test_a_turn_still_thinking_before_any_tool_says_so():
    assert _trace().due(at=FIRST_AFTER) == [DE["think"][0]]


def test_the_same_phase_twice_never_repeats_a_line():
    trace = _trace()
    trace.tools(["memory_search"])
    trace.due(at=FIRST_AFTER)
    trace.tools(["list_edit"])
    trace.due(at=FIRST_AFTER + GAP)
    trace.tools(["read_file"])
    trace.due(at=FIRST_AFTER + 2 * GAP)
    trace.tools(["list_edit"])
    lines = trace.due(at=FIRST_AFTER + 3 * GAP)
    assert lines[-1] == DE["write"][1] and len(set(lines)) == len(lines)


def test_an_unknown_household_language_reads_english():
    trace = Trace(started=0.0, lang="fr", pick=_first)
    assert trace.due(at=FIRST_AFTER) == [PHRASES["en"]["think"][0]]


def test_a_round_that_writes_is_writing_even_with_a_read_in_it():
    assert phase_of(["read_file", "list_edit"], looked_before=False) == "write"


def test_compacting_the_conversation_is_a_line_in_the_familys_words():
    trace = _trace()
    trace.tidying()
    assert trace.due(at=FIRST_AFTER) == [DE["tidy"][0]]



def test_a_turn_that_never_answers_ends_its_status_by_itself():
    """A cancelled turn sends nothing, so the status cannot wait for an answer forever."""
    trace = _trace()
    assert not trace.over(at=GIVE_UP_AFTER - 1)
    assert trace.over(at=GIVE_UP_AFTER)


def test_a_full_status_is_over_before_the_turn_gives_up():
    trace = _trace()
    at = FIRST_AFTER
    for tools in (["memory_search"], ["list_edit"], ["memory_search"], ["list_edit"],
                  ["memory_search"]):
        trace.tools(tools)
        assert trace.due(at=at)
        at += GAP
    assert trace.over(at=at)
