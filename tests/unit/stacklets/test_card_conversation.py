"""The card conversation: what the model is told when a note is rebuilt from its events.

ADR-018 §7. What the model answers cannot be tested here, only what it is
told, in the order measured on the instance's model
(`tests/e2e/eval/edit_replay.py`, 24 of 24): the task, the words, the
earlier clean version as the model's own answer, one message per event,
the request.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from card_conversation import build_conversation, conversation_template  # noqa: E402

WORDS = "saturday:\ndinner at patty and selmas at 7\nbring dessert"
PRIOR = "Saturday:\n\n- Dinner at Patty and Selma's at 7.\n- Bring dessert."
EVENTS = ["Marge edited the card: changed 'Dinner at Patty and Selma's at 7.' to "
          "'Dinner at Patty and Selma's at 6.'", "Homer: dinner is at 8 now"]


def _conversation() -> list[dict]:
    return build_conversation(writer="Marge", words=WORDS, prior=PRIOR, events=EVENTS)


def test_the_earlier_clean_version_is_the_models_own_answer_and_each_event_follows_in_order():
    roles = [m["role"] for m in _conversation()]
    contents = [m["content"] for m in _conversation()]
    assert roles == ["system", "user", "assistant", "user", "user", "user"]
    assert "Marge wrote this note" in contents[1] and WORDS in contents[1]
    assert json.loads(contents[2]) == {"clean": PRIOR}
    assert contents[3:5] == EVENTS
    assert '{"clean"' in contents[5]


def test_the_task_says_later_events_win_and_nothing_is_added_or_dropped():
    task = _conversation()[0]["content"]
    assert "the later one wins" in task
    assert "Add nothing, drop nothing" in task
    assert "note's own language" in task


def test_the_template_hash_names_the_wording_not_the_note():
    """The sidecar records it: the same for every note, until the wording changes."""
    assert conversation_template() == conversation_template()
    assert len(conversation_template()) == 12
