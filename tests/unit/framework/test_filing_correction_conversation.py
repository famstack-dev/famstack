"""The card conversation: what the model is told when any card is corrected.

ADR-018 §7. What the model answers cannot be tested here, only what it is
told: the task, what was sent, the card as filed as the model's own answer,
one message per event in time order, the request for what they change. A
note's conversation carries its clean version; a document's does not.
"""

from __future__ import annotations

import json

from stack.filing.correction.conversation import build_conversation, conversation_template

WORDS = "saturday:\ndinner at patty and selmas at 7\nbring dessert"
NOTE = {"title": "Dinner at Patty and Selma's", "tags": ["dinner"], "persons": ["marge"],
        "date": None, "clean": "Saturday:\n\n- Dinner at Patty and Selma's at 7.\n- Bring dessert."}
EVENTS = ["Marge edited the card: changed 'Dinner at Patty and Selma's at 7.' to "
          "'Dinner at Patty and Selma's at 6.'", "Homer: dinner is at 8 now"]


def _note() -> list[dict]:
    return build_conversation(writer="Marge", kind="note", original=WORDS, filed=NOTE, events=EVENTS)


def test_the_card_as_filed_is_the_models_own_answer_and_each_event_follows_in_order():
    roles = [m["role"] for m in _note()]
    contents = [m["content"] for m in _note()]
    assert roles == ["system", "user", "assistant", "user", "user", "user"]
    assert "Marge sent this note" in contents[1] and WORDS in contents[1]
    assert json.loads(contents[2]) == NOTE
    assert contents[3:5] == EVENTS


def test_the_task_asks_for_what_changes_and_that_unnamed_fields_stay():
    task = _note()[0]["content"]
    assert "the later one wins" in task
    assert "changes only what it names" in task
    assert '"clean"' in task


def test_a_documents_conversation_carries_no_text_of_its_own():
    """A document's text is Paperless's, not the card's: the model is not
    asked to rewrite it."""
    receipt = {"title": "Texxon Tankquittung", "tags": ["Fahrzeug"], "persons": ["Homer"]}
    messages = build_conversation(writer="Marge", kind="document", original="Texxon Summe 44,64",
                                  filed=receipt, events=["Marge: the tag is wrong"])
    assert '"clean"' not in messages[0]["content"]
    assert "clean" not in json.loads(messages[2]["content"])


def test_the_template_hash_names_the_wording_not_the_card():
    assert conversation_template() == conversation_template()
    assert conversation_template(text=True) != conversation_template(text=False)


def test_a_note_in_a_topic_with_a_list_is_shown_the_list_and_asked_for_new_todos():
    """The list holds what is to do already, open or done, so the model can
    tell a new to-do from one it would only repeat."""
    holds = "- [x] Buy dessert\n- [ ] Call Patty"
    messages = build_conversation(writer="Marge", kind="note", original=WORDS, filed=NOTE,
                                  events=EVENTS, current_list=holds)
    assert '"action_items"' in messages[0]["content"]
    assert holds in messages[-1]["content"]
    assert conversation_template(todos=True) != conversation_template()


def test_a_note_without_a_list_is_not_asked_for_todos():
    assert '"action_items"' not in _note()[0]["content"]
