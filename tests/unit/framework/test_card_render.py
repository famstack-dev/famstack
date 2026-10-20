"""Every card type has one renderer, and it writes markdown (ADR-016 §4).

The chat, the card browser and the agent all show a card from the same
markdown, so a note reads the same in each. These tests pin that markdown
for each card type, from a file written the way ADR-017 lays one out: the
title once, one line of facts, the type's own sections, the person's own
words last. A field appears once: the meta block under the heading
repeats the frontmatter, so it is not shown; people are shown by name;
`Person:` tags are not shown at all.
"""

from __future__ import annotations

from stack.card_render import for_chat, render, render_file

NAMES = {"homer": "Homer", "marge": "Marge", "bart": "Bart", "lisa": "Lisa"}


def _shown(text: str, language: str = "en") -> str:
    return render_file(text, names=NAMES, language=language).markdown


# ── Records ───────────────────────────────────────────────────────────

DOCUMENT = """\
---
type: document
card_id: document-0a1b2c3d4e
title: Duff car insurance renewal
date: "2026-03-01"
correspondent: Duff Insurance
document_type: Invoice
persons:
  - homer
tags:
  - insurance
  - "Person: Homer"
paperless_id: 10
resource: https://paperless.example/documents/10/details
---

# Duff car insurance renewal

> **From:** [Duff Insurance](../../../correspondents/duff-insurance.md) · **About:** [Homer](../../../people/homer.md)

> [!summary]
> The yearly renewal of the car insurance.
>
> [Show Document](https://paperless.example/documents/10/details)
>
> **Facts**
> - EUR 340.00 a year
>
> **Action items**
> - Pay the invoice 📅 2026-03-15

Dear Mr Simpson,
your policy renews on 1 April.
"""


def test_a_document_shows_its_facts_once_and_its_text_after_the_summary():
    """Date, people, correspondent and kind on one line, with the link to
    the document in Paperless; the meta block and the briefing's own link
    to it would only say the same again."""
    assert _shown(DOCUMENT) == """\
# Duff car insurance renewal

2026-03-01 · Homer · Duff Insurance · Invoice · [#10](https://paperless.example/documents/10/details)

The yearly renewal of the car insurance.

- EUR 340.00 a year

**To do**

- Pay the invoice 📅 2026-03-15

Dear Mr Simpson,
your policy renews on 1 April.
"""


NOTE = """\
---
type: note
card_id: note-7f3a9c21e4
title: Art supplies for next time
date: "2026-10-13"
persons:
  - marge
tags:
  - watercolour
capture_id: $r-PlkCYBsNEjgZDfzCpcAoDaY66uCHNc7tJjDWrY8P4
resource: https://matrix.to/#/!art:home/$r-PlkCYBsNEjgZDfzCpcAoDaY66uCHNc7tJjDWrY8P4
---

# Art supplies for next time

> **About** [Marge](../../../../family/people/marge.md)
> **Captured** 2026-10-13 · **Kind** note
> **Source** <https://matrix.to/#/!art:home/$r-PlkCYBsNEjgZDfzCpcAoDaY66uCHNc7tJjDWrY8P4>
> **File** [brushes.png](/media/2026/10/brushes.png)

> [!summary]
> Brushes and paper Marge wants for the next trip.
>
> **Facts**
> - Brand: Springfield Art Supply

![[/media/2026/10/brushes.png]]

> [!quote]- Original paste
> two round brushes
> cold-pressed paper

## Caption

for next time

## Replies

**homer**
> mmm, paper
"""


def test_a_note_shows_the_summary_then_the_photo_then_the_persons_own_words():
    """The paste and the caption are Marge's words and close the card, as
    quotes, with the replies after them under their author's name."""
    assert _shown(NOTE) == """\
# Art supplies for next time

2026-10-13 · Marge

Brushes and paper Marge wants for the next trip.

- Brand: Springfield Art Supply

![[/media/2026/10/brushes.png]]

> two round brushes
> cold-pressed paper

> for next time

**Homer**
> mmm, paper
"""


def test_a_caption_that_only_repeats_the_title_is_shown_once():
    """A capture whose message was only a caption files it as the title too;
    the title is a field, so it is not repeated at the end of the card."""
    note = NOTE.replace("## Caption\n\nfor next time", "## Caption\n\nArt supplies for next time")
    shown = _shown(note)
    assert shown.count("Art supplies for next time") == 1
    assert "> two round brushes" in shown


def test_an_upload_not_shown_in_the_page_stays_a_link_to_the_file():
    """A PDF is not embedded; the meta block's file link is then the only
    way to it, and it is no field, so it stays."""
    text = NOTE.replace("![[/media/2026/10/brushes.png]]\n", "").replace("brushes.png", "list.pdf")
    shown = _shown(text)
    assert "[list.pdf](/media/2026/10/list.pdf)" in shown
    assert "**File**" not in shown


BOOKMARK = """\
---
type: bookmark
card_id: bookmark-2b3c4d5e6f
title: Itchy and Scratchy, the episode guide
date: "2026-10-11"
persons:
  - bart
resource: https://www.springfield-wiki.example/itchy
---

# Itchy and Scratchy, the episode guide

> **About** [Bart](../../../../family/people/bart.md)
> **Captured** 2026-10-11 · **Kind** bookmark
> **Source** <https://www.springfield-wiki.example/itchy>

> [!summary]
> Every episode, with the year it first aired.
"""


def test_a_bookmark_names_the_site_it_points_at_in_its_facts():
    assert _shown(BOOKMARK) == """\
# Itchy and Scratchy, the episode guide

2026-10-11 · Bart · [springfield-wiki.example](https://www.springfield-wiki.example/itchy)

Every episode, with the year it first aired.
"""


EMAIL = """\
---
type: email
card_id: email-3c4d5e6f70
title: School trip to the planetarium
date: "2026-10-02"
persons:
  - lisa
---

# School trip to the planetarium

> **About** [Lisa](../../../../family/people/lisa.md)
> **Started** 2026-10-02 · **Kind** email

<!-- mid: <trip-1@springfield-elementary.example> -->
## 2026-10-02 · skinner@springfield-elementary.example

> [!summary]
> The class visits the planetarium on Friday.
>
> **Action items**
> - Sign the consent form 📅 2026-10-08

> [!quote]- Message
> Dear parents,
> the class goes to the planetarium.
"""


def test_an_email_keeps_each_message_under_its_own_heading():
    """A thread is many messages; each keeps its date and sender, its summary
    and its words in the order they came."""
    assert _shown(EMAIL) == """\
# School trip to the planetarium

2026-10-02 · Lisa

## 2026-10-02 · skinner@springfield-elementary.example

The class visits the planetarium on Friday.

**To do**

- Sign the consent form 📅 2026-10-08

> Dear parents,
> the class goes to the planetarium.
"""


DIARY = """\
---
type: diary
title: Bart's report card
date: "2026-03-16"
date_basis: spoken
persons:
  - bart
  - marge
tags:
  - "Topic:School"
  - "Person:Bart"
filed_by: marge
medium: voice
entry_id: "00c674191f"
---

# Bart's report card

> **From** marge · **Date** 2026-03-16 (spoken)

> [!summary]
> Marge records a message for Bart about his report card.
>
> **Facts**
> - Bart: helped the new student

![[/media/2026/03/memo-bart.m4a]]

## Quotes

- "I won't forget that."

## Replies

**homer**
> He gets that from me.

## Transcript

Hi Bart, I wanted to tell you how proud I am.
You stood up for the new girl.
"""


def test_a_diary_entry_ends_with_what_was_said_and_then_the_replies():
    """The transcript is the entry itself: it comes after the summary and
    the recording, and the replies to it come after it."""
    assert _shown(DIARY) == """\
# Bart's report card

2026-03-16 · Bart, Marge · 🎤

Marge records a message for Bart about his report card.

- Bart: helped the new student

![[/media/2026/03/memo-bart.m4a]]

- "I won't forget that."

> Hi Bart, I wanted to tell you how proud I am.
> You stood up for the new girl.

**Homer**
> He gets that from me.
"""


# ── Lists and declared cards ──────────────────────────────────────────

LIST = """\
---
type: list
card_id: list-wochenmarkt
title: Wochenmarkt
description: Was wir am Samstag kaufen.
---

## Gemüse
- [ ] Kohlrabi [Marge](../people/marge.md) 📅 2026-10-17
- [x] Eier ✅ 2026-10-10
"""


def test_a_list_shows_what_it_is_for_and_its_items_with_one_form_of_due_date():
    """Items keep their box, their person and their due day as the file
    writes them: one form of a due date, the same in every language."""
    assert _shown(LIST, language="de") == """\
# Wochenmarkt

Was wir am Samstag kaufen.

## Gemüse
- [ ] Kohlrabi [Marge](../people/marge.md) 📅 2026-10-17
- [x] Eier ✅ 2026-10-10
"""


def test_a_person_shows_the_other_names_they_go_by_and_their_account():
    text = ("---\ntype: person\ncard_id: person-homer\ntitle: Homer Simpson\n"
            "aliases:\n  - Homie\naccount: homer\n---\n\n# Homer Simpson\n\nWorks at the plant.\n")
    assert _shown(text) == "# Homer Simpson\n\nHomie · @homer\n\nWorks at the plant.\n"


def test_a_topic_has_one_title_whatever_its_heading_says():
    """The title is the field; a heading written differently is not a second one (review C6)."""
    text = ("---\ntype: topic\ncard_id: topic-itchy-scratchy-land\ntitle: Itchy Scratchy Land\n---\n\n"
            "# Itchy & Scratchy Land\n\nThe trip in the summer.\n")
    assert _shown(text) == "# Itchy Scratchy Land\n\nThe trip in the summer.\n"


def test_a_correspondent_shows_its_other_names():
    text = ("---\ntype: correspondent\ncard_id: correspondent-moe-s-tavern\ntitle: Moe's Tavern\n"
            "aliases:\n  - Moe's\n---\n\n# Moe's Tavern\n")
    assert _shown(text) == "# Moe's Tavern\n\nMoe's\n"


# ── Any other card ────────────────────────────────────────────────────

def test_a_type_without_a_renderer_shows_its_fields_as_a_list_then_its_body():
    text = ("---\ntype: pet\ncard_id: pet-santas-little-helper\ntitle: Santa's Little Helper\n"
            "persons:\n  - bart\n---\n\n# Santa's Little Helper\n\nA greyhound.\n")
    assert _shown(text) == """\
# Santa's Little Helper

- **type**: pet
- **card_id**: pet-santas-little-helper
- **persons**: bart

A greyhound.
"""


def test_a_file_whose_frontmatter_does_not_parse_is_shown_as_its_text():
    text = "---\ntitle: [unclosed\n---\n\n# Moe's specials\n\nDuff on tap.\n"
    assert _shown(text) == "# Moe's specials\n\nDuff on tap.\n"


def test_a_person_nobody_declared_is_shown_as_written():
    """An id is shown with a capital, a name an older card carries as it is."""
    shown = render({"type": "note", "title": "Kwik-E-Mart", "persons": ["apu", "Ned Flanders"]},
                   "", names=NAMES)
    assert shown.facts == "Apu, Ned Flanders"


def test_the_to_do_label_is_in_the_instances_language():
    assert "**Zu erledigen**" in _shown(DOCUMENT, language="de")


def test_below_the_title_is_everything_but_the_heading():
    """For a surface that shows the title in a line of its own, such as a filing reply."""
    shown = render_file(BOOKMARK, names=NAMES)
    assert shown.below_title == shown.markdown.removeprefix(f"# {shown.title}\n\n").rstrip("\n")


# ── For the chat ──────────────────────────────────────────────────────

def test_in_the_chat_media_is_a_link_to_the_card_and_card_links_are_names():
    """The chat shows no images from outside the room, and a link into the
    memory repo opens nothing there."""
    chat = for_chat(_shown(NOTE).replace("**Homer**", "[Homer](../people/homer.md)"),
                    card_link="https://home.example/go/note-7f3a9c21e4")
    assert "📎 [brushes.png](https://home.example/go/note-7f3a9c21e4)" in chat
    assert "![[" not in chat
    assert "Homer\n> mmm, paper" in chat
    assert for_chat("[Duff](https://duff.example)") == "[Duff](https://duff.example)"
    assert for_chat("![[/media/2026/10/a.m4a]]") == "📎 a.m4a"
