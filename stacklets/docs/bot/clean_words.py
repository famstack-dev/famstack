"""A note's clean version: the writer's words, tidied, and checked in code.

People jot things down the way they type in a chat: one run-on line,
shorthand, no capitals, a list written as a sentence. A summary of such
a note says the same again in other words, and its facts restate it line
by line, so the card read long and awkward. A note written in a person's
own words is filed as a clean version of those words instead: paragraphs,
list items where the text is a list, spelling and punctuation fixed, in
the note's own language, nothing added, nothing dropped. The words
themselves follow on the card, as a quote.

The model is asked for that, and what it answers is checked here before
it is filed, because a tidy text that dropped "200" from "budget max 200"
reads as if it were complete. Every number and every capitalised name of
the note must be in the clean version, and the clean version may not
carry a number the note does not. A correction the family wrote (a reply,
or `--msg` on a reprocess) may change both: a value it mentions, or one it
gives in place of another, counts as the writer's (`problems` states the
rule). A clean version that fails is not filed; the card shows the words
alone, and the log says what was lost.

A note with corrections or a person's edits is rebuilt from its events
(`note_correction` for corrections alone, `card_conversation` when a
person edited it), and checked by the same rule, cut down to it
(`kept_rebuilt`): every number and name of the words is in the rebuilt
clean version, unless a correction or an edit replaced it. Beyond that a
rebuild is not refused for its wording (ADR-018 §7).

    >>> kept("ok budget max 200, ask Homer", "Budget: at most 200. Ask Homer.")
    ('Budget: at most 200. Ask Homer.', '')
    >>> kept("ok budget max 200, ask Homer", "Budget: at most 250. Ask Homer.")
    ('', 'lost 200; added 250')
    >>> kept("ok budget max 200, ask Homer", "Budget: at most 250. Ask Homer.",
    ...      corrections="Marge: budget is 250 now")
    ('Budget: at most 250. Ask Homer.', '')

Pure functions, no I/O.
"""

from __future__ import annotations

import re

# How a note's words are cleaned up, in the words of both prompts that
# write a clean version from them: the capture prompt (`pipeline`) and the
# correction prompt (`note_correction`), so the two write alike.
TIDY = (
    "Fix spelling, punctuation and capitalisation, write chat shorthand and "
    "abbreviations out, split run-on text into short paragraphs, and write a "
    "list as a Markdown list (one item per line, starting with '- ') under a "
    "short intro line when the note has one."
)

# Up to this many characters a note gets a clean version. Typed into a
# chat or said into a phone, a note is rarely longer; a longer text is
# usually pasted from somewhere else, and its reader is better served by
# the digest a summary gives than by the whole text again.
CLEAN_MAX_CHARS = 2000

# A number standing on its own, or leading a word ("4pm", "2nd"). One that
# follows a letter is part of a word ("b4", "mp3") and may be written out.
_NUMBER = re.compile(r"(?<![^\W\d_])\d+")
_WORD = re.compile(r"[^\W\d_]+")
# What may stand between a sentence's end and its first word: spaces,
# list markers, opening quotes and brackets.
_LEAD = " \t-*•\"'(["
_SENTENCE_END = ".!?:\n"
# The card file closes with the words as a quote and the replies under
# their own heading, and both are read back from those lines; a clean
# version with either would be read as one of them.
_STRUCTURE = re.compile(r"^\s*[#>]", re.MULTILINE)


def is_cleanable(words: str) -> bool:
    """Whether a note's own words get a clean version: some, and not too many.

        >>> is_cleanable("buy milk b4 sat")
        True
        >>> is_cleanable("x" * 5000)
        False
    """
    return bool(words.strip()) and len(words.strip()) <= CLEAN_MAX_CHARS


def kept(words: str, clean: str, *, corrections: str = "") -> tuple[str, str]:
    """The clean version to file for `words`, and why there is none.

    ``("", "")`` when the model answered none or the words needed no
    cleaning: the card shows them once. ``("", reason)`` when it fails
    the check (`problems`), or carries a heading or a quote.
    ``corrections`` is what the family wrote to correct the note.
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
    shows no clean version when it would only repeat the words with other
    capitals, punctuation and list markers (`kept`); a rebuild still starts
    from that tidy text, since the model keeps the shape of the version it
    is given. A text that fails the check is not started from: the words are.

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

    ``clean`` is what the rebuild answered; ``events`` what the
    corrections and the person's edits say, one per line
    (`stack.cards.events.text_of`). It is refused when it is empty, since the
    conversation has no "needs no change" to answer, or when it fails
    `rebuild_problems`; otherwise as `kept`.

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

    The rule of the first filing (`problems`), less what the events
    replaced: a number or name of the words that is not in the clean
    version is lost, unless an event mentions it (an edit names the line
    it changed; "5, not 4"), or the events give a new value of its kind,
    number for number and name for name, that the clean version carries
    ("the budget is 250 now"). Several events may change one value in
    turn, so no value an event gives is required, and none stands in for
    just one lost value. A number of the clean version that neither the
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

    The rule, decided from the sources alone (the note's words and the
    corrections the family gave it, replies and refile notes):

    - Every number of the words, and every capitalised word that does not
      open a sentence (a name, in most languages; every noun in some,
      which only makes the check stricter), is in the clean version,
      unless a correction says otherwise. A correction says otherwise
      when it mentions the old value ("250, not 200"), or when it gives a
      new value of the same kind, number for number and name for name,
      that the clean version carries and the words do not ("the budget
      is 250 now"). Each such new value stands in for one lost value, so
      a correction that gives one amount cannot excuse two lost ones.
    - Every number of the clean version is in the words or in a
      correction: the model may not add one.

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
