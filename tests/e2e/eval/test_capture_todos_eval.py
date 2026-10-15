"""Which short notes become todos, asked of the real model.

A note reaches this question only when it is in the writer's own words
(`own_words`): a voice message, or a short typed message without a link.
What is left for the model is whether the writer records something to
do or keeps a list. A short pasted tip passes the gate, so these cases
pin that the model keeps it as information, and that a list still
yields one item per thing.

Opt-in like the rest of the eval: it needs `AI_DEFAULT_MODEL` and a
reachable AI endpoint.
"""

from __future__ import annotations

import pytest

# (note, how many items it should yield)
CASES = {
    "a reminder with a deadline": ("wir sollten bis Freitag den Müll rausbringen", 1),
    "a reminder": ("Nicht vergessen: Lisa zum Zahnarzt bringen", 1),
    "a list the writer keeps": ("Einkaufsliste: Milch, Eier, Brot", 3),
    "a list said in passing": ("Wir brauchen noch Milch, Eier und Brot.", 3),
    "a pasted tip": ("Check out Bun, a fast all-in-one JavaScript runtime.", 0),
    "a piece of advice": ("Tipp: Tomaten nie im Kühlschrank lagern, sonst verlieren sie Aroma.", 0),
    "something that happened": ("Der Elternabend war gut, Frau Krabappel hat viel erzählt.", 0),
}


@pytest.mark.parametrize("case", CASES)
async def test_only_what_the_writer_commits_to_becomes_a_todo(ai_classifier, case):
    note, count = CASES[case]
    answer = await ai_classifier.classify_capture(
        text=note, person_names=["Homer", "Marge", "Lisa"], extract_action_items=True)
    items = answer.get("action_items") or []
    assert len(items) == count, f"{case}: {items}"
