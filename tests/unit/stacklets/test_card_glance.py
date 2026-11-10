"""A filed card at a glance: the limits that keep a chat answer short.

`card_glance` is what the archivist answers a filing with. These tests
drive it through its one function, as the reply presenter calls it, and
pin its limits: the summary's first
paragraph up to about 280 characters, three to-dos then "and N more",
four tags then "+N" (every tag for a correction's card), and the day in
the household's language.
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from stacklets.docs.bot.card_glance import card_glance  # noqa: E402

TODAY = _dt.date(2026, 11, 5)
WORDS = {"todos_more": "and {count} more"}


def _t(key, **kw):
    return WORDS[key].format(**kw)


def _glance(classification, **kw):
    kw.setdefault("title", "Ski week")
    kw.setdefault("link", "https://home.example/go/card/email-1")
    kw.setdefault("captured_at", "2026-11-05")
    return card_glance(_t, kw.pop("card_type", "email"), classification, today=TODAY, **kw)


def test_a_short_summary_is_quoted_whole():
    out = _glance({"summary": "Trip on Friday."})
    assert "> Trip on Friday.\n" in out + "\n" and "…" not in out


def test_sentences_are_kept_while_they_fit_then_an_ellipsis():
    sentence = "The trip costs EUR 385 per child and is paid to the school account. "  # 68
    out = _glance({"summary": sentence * 5})
    quote = next(b for b in out.split("\n\n") if b.startswith("> "))
    assert quote == "> " + (sentence * 4).strip() + " …"


def test_a_first_sentence_longer_than_the_limit_is_cut_at_a_word():
    out = _glance({"summary": "word " * 100})
    quote = next(b for b in out.split("\n\n") if b.startswith("> "))
    assert quote.endswith(" …") and len(quote) <= 2 + 280 + 2


def test_only_the_first_paragraph_is_shown():
    out = _glance({"summary": "First paragraph.\n\nSecond paragraph."})
    assert "> First paragraph. …" in out and "Second" not in out


def test_three_todos_in_the_classifiers_order_then_how_many_more():
    items = [{"action": "Pay", "due": "2026-11-30"}, {"action": "Sign", "due": "2026-11-20"},
             "Decide", "Copy the card", "Go", "Transfer the rest"]
    out = _glance({"action_items": items})
    assert ("- [ ] Pay 📅 Mon 30 Nov\n- [ ] Sign 📅 Fri 20 Nov\n- [ ] Decide\n"
            "- and 3 more") in out


def test_four_tags_then_how_many_more():
    tags = ["school", "ski-week", "class-4b", "forms", "payment", "health"]
    assert _glance({"tags": tags}).endswith("🏷️ school · ski-week · class-4b · forms +2")


def test_every_tag_when_asked():
    tags = ["school", "ski-week", "class-4b", "forms", "payment", "health"]
    assert _glance({"tags": tags}, all_tags=True).endswith("🏷️ " + " · ".join(tags))


def test_the_day_in_german_and_with_the_year_when_not_this_years():
    out = _glance({"persons": ["Bart"], "action_items": [{"action": "Zahlen",
                                                          "due": "2027-01-08"}]},
                  language="de", captured_at="2026-11-05")
    assert "👤 Bart · 📅 Do 5. Nov" in out
    assert "- [ ] Zahlen 📅 Fr 8. Jan 2027" in out


def test_no_facts_and_nothing_for_what_is_empty():
    out = _glance({"facts": ["Room 12"]}, captured_at=None)
    assert out == "📧 [Ski week](https://home.example/go/card/email-1)"
