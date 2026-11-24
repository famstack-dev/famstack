"""clean_words: a note's clean version, the writer's words tidied and checked in code.

A **clean version** is a note's own words, tidied: paragraphs, list items
where the text is a list, and correct spelling and punctuation. It is in
the note's own language. It adds nothing and drops nothing. The card
shows the words below it, as a quote.

People write a note as they type in a chat: one long line, shorthand, no
capitals. A summary of such a note says the same in other words. A note
in its writer's own words therefore gets a clean version instead of a
summary.

The model writes the clean version. This module checks it before the
card is written, because a tidy text without the "200" of "budget max
200" looks complete. The rule is in `problems`:

- Every number and every capitalised name of the note is in the clean
  version.
- The clean version holds no number that the note does not hold.
- A correction can change both. A value that a correction mentions or
  gives counts as the writer's.

A clean version that fails the check is not written. The card then
shows the words alone, and the log names what was lost.

A note with corrections or edits is rebuilt from its events, in the card
conversation (`stack.filing.correction.conversation`). The rebuilt text
gets a shorter form of the same rule (`kept_rebuilt`). Every number and
name of the words is in it, unless a correction or an edit replaced it.
A rebuild is not refused for its wording (ADR-018 §7).

    >>> kept("ok budget max 200, ask Homer", "Budget: at most 200. Ask Homer.")
    ('Budget: at most 200. Ask Homer.', '')
    >>> kept("ok budget max 200, ask Homer", "Budget: at most 250. Ask Homer.")
    ('', 'lost 200; added 250')
    >>> kept("ok budget max 200, ask Homer", "Budget: at most 250. Ask Homer.",
    ...      corrections="Marge: budget is 250 now")
    ('Budget: at most 250. Ask Homer.', '')

The module holds pure functions and has no I/O.
"""

from __future__ import annotations

import re

# How a note's words are tidied, as the prompts say it. The capture prompt
# writes a note's first clean version, and the card conversation rebuilds it.
TIDY = (
    "Fix spelling, punctuation and capitalisation, write chat shorthand and "
    "abbreviations out, split run-on text into short paragraphs, and write a "
    "list as a Markdown list (one item per line, starting with '- ') under a "
    "short intro line when the note has one."
)

# The longest note that gets a clean version, in characters. A typed or
# spoken note is rarely longer. A longer text is usually pasted, and a
# summary serves its reader better than the whole text again.
CLEAN_MAX_CHARS = 2000

# A number on its own, or at the start of a word ("4pm", "2nd"). A number
# after a letter is part of a word ("b4", "mp3"), and the model can write
# that word out.
_NUMBER = re.compile(r"(?<![^\W\d_])\d+")
_WORD = re.compile(r"[^\W\d_]+")
# What may stand between a sentence's end and its first word: spaces,
# list markers, opening quotes and brackets.
_LEAD = " \t-*•\"'(["
_SENTENCE_END = ".!?:\n"
# The card file ends with the words as a quote and the replies under a
# heading. Both are read back from those lines. A clean version with a
# heading or a quote line is refused, because it would be read as one.
_STRUCTURE = re.compile(r"^\s*[#>]", re.MULTILINE)


def is_cleanable(words: str) -> bool:
    """Whether a note's words get a clean version: they are not empty and not too long.

        >>> is_cleanable("buy milk b4 sat")
        True
        >>> is_cleanable("x" * 5000)
        False
    """
    return bool(words.strip()) and len(words.strip()) <= CLEAN_MAX_CHARS


def kept(words: str, clean: str, *, corrections: str = "") -> tuple[str, str]:
    """The clean version to file for `words`, and why there is none.

    ``("", "")`` when the model answered none, or when the words need no
    tidying. The card then shows the words once. ``("", reason)`` when
    the clean version fails the check (`problems`). It also fails when it
    holds a heading or a quote line. ``corrections`` is the text of the
    corrections of the note.
    """
    clean = (clean or "").strip()
    if not clean or same_words(words, clean):
        return "", ""
    if _STRUCTURE.search(clean):
        return "", "has a heading or a quote, which the card's own would take it for"
    found = problems(words, clean, corrections=corrections)
    return ("", "; ".join(found)) if found else (clean, "")


def starting_point(words: str, answered: str) -> str:
    """The clean version a rebuild starts from, for a card that shows none.

    ``answered`` is what the capture prompt wrote for the words. A card
    shows no clean version when it only repeats the words with other
    capitals and punctuation (`kept`). A rebuild still starts from that
    tidy text, because the model keeps the shape of the text it gets. A
    text that fails the check is not used. The rebuild then starts from
    the words.

        >>> starting_point("packing:\\ntowels", "Packing:\\n- Towels")
        'Packing:\\n- Towels'
        >>> starting_point("budget max 200", "Budget: at most 250.")
        'budget max 200'
    """
    clean, why = kept(words, answered)
    if clean:
        return clean
    return answered.strip() if answered.strip() and not why else words.strip()


def kept_rebuilt(words: str, clean: str, *, events: str) -> tuple[str, str]:
    """The rebuilt clean version to file for `words`, and why there is none.

    ``clean`` is the text that the rebuild answered. ``events`` is the
    text of the corrections and edits, one per line
    (`stack.cards.events.text_of`). An empty text is refused, because the
    conversation always asks for the whole text. A text that fails
    `rebuild_problems` is refused too. Otherwise the result is as for
    `kept`.

        >>> kept_rebuilt("budget max 200", "Budget: at most 300.",
        ...              events="budget is 250\\nno, 300")
        ('Budget: at most 300.', '')
        >>> kept_rebuilt("budget max 200, ask Homer", "Budget: at most 250.",
        ...              events="budget is 250")
        ('', 'lost Homer')
    """
    clean = (clean or "").strip()
    if not clean:
        return "", "is empty"
    if _STRUCTURE.search(clean):
        return "", "has a heading or a quote, which the card's own would take it for"
    found = rebuild_problems(words, clean, events=events)
    if found:
        return "", "; ".join(found)
    return ("", "") if same_words(words, clean) else (clean, "")


def rebuild_problems(words: str, clean: str, *, events: str) -> list[str]:
    """What a rebuilt clean version lost of `words`, or added to them.

    The rule is the rule of the first filing (`problems`), without the
    values that the events replaced. A number or name of the words that
    is not in the clean version is lost, with two exceptions:

    - an event mentions it, for example "5, not 4";
    - the events give a new value of the same kind, and the clean version
      holds it ("the budget is 250 now").

    Several events can change one value in turn. A value that an event
    gives is therefore not required, and one new value excuses every lost
    value of its kind. A number of the clean version that neither the
    words nor the events hold is added.
    """
    gives_number = any(_has(clean, n) and not _has(words, n) for n in _numbers(events))
    gives_name = any(_has(clean, n) and not _has(words, n) for n in _names(events))
    lost = [*(n for n in dict.fromkeys(_numbers(words))
              if not gives_number and not _has(clean, n) and not _has(events, n)),
            *(n for n in dict.fromkeys(_names(words))
              if not gives_name and not _has(clean, n) and not _has(events, n))]
    added = [n for n in dict.fromkeys(_numbers(clean))
             if not _has(words, n) and not _has(events, n)]
    return [*(f"lost {t}" for t in lost), *(f"added {n}" for n in added)]


def same_words(words: str, clean: str) -> bool:
    """Whether two texts differ only in spacing, case and punctuation.

    A clean version that changed a comma and a capital is not worth a
    second copy of the same words on the card.

        >>> same_words("Bart's dentist moved to Thursday", "bart's dentist moved to thursday.")
        True
    """
    return _letters(words) == _letters(clean)


def problems(words: str, clean: str, *, corrections: str = "") -> list[str]:
    """What the clean version lost of `words`, or added to them.

    The rule uses the sources alone: the note's words and its
    corrections, from replies and refile notes.

    - Every number of the words is in the clean version.
    - Every capitalised word that does not start a sentence is in the
      clean version. In most languages that word is a name.
    - A correction can excuse a lost value. It excuses the value when it
      mentions the old value ("250, not 200").
    - A correction also excuses a lost value with a new value of that
      kind ("the budget is 250 now"). The clean version must hold it. Each new value excuses one lost value, because one new
      amount cannot replace two.
    - Every number of the clean version is in the words or in a
      correction, because the model must not add a number.

    Anything else is a problem, named as `lost <value>` or `added <value>`.
    """
    lost_numbers = [n for n in dict.fromkeys(_numbers(words))
                    if not _has(clean, n) and not _has(corrections, n)]
    lost_names = [n for n in dict.fromkeys(_names(words))
                  if not _has(clean, n) and not _has(corrections, n)]
    # What the corrections bring in, and the clean version carries.
    new_numbers = [n for n in dict.fromkeys(_numbers(corrections))
                   if _has(clean, n) and not _has(words, n)]
    new_names = [n for n in dict.fromkeys(_names(corrections))
                 if _has(clean, n) and not _has(words, n)]
    lost = lost_numbers[len(new_numbers):] + lost_names[len(new_names):]
    added = [n for n in dict.fromkeys(_numbers(clean))
             if not _has(words, n) and not _has(corrections, n)]
    return [*(f"lost {t}" for t in lost), *(f"added {n}" for n in added)]


# ── Reading the words ─────────────────────────────────────────────────


def _numbers(text: str) -> list[str]:
    return _NUMBER.findall(text)


def _names(text: str) -> list[str]:
    """The capitalised words of `text` that do not open a sentence or a line."""
    names = []
    for m in _WORD.finditer(text):
        word = m.group()
        if len(word) > 1 and word[0].isupper() and not _opens_sentence(text, m.start()):
            names.append(word)
    return names


def _opens_sentence(text: str, at: int) -> bool:
    before = text[:at].rstrip(_LEAD)
    return not before or before[-1] in _SENTENCE_END


def _has(text: str, token: str) -> bool:
    """Whether `token` stands in `text` as a word of its own, in any case."""
    if not text or not token:
        return False
    edge = r"(?<!\d)" if token.isdigit() else r"(?<!\w)"
    tail = r"(?!\d)" if token.isdigit() else r"(?!\w)"
    return re.search(edge + re.escape(token) + tail, text, re.IGNORECASE) is not None


def _letters(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold()))
