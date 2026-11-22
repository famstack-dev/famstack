"""The hint whisper decodes a recording against.

    spoken_vocabulary(["Homer", "Maggie"])     # "The people in this family are Homer and Maggie."

Whisper decodes against the household's vocabulary, so the names it would
otherwise hear as ordinary words come back right. The household's names
themselves (`household`, `card_people`) are read by `stack.household_people`.
"""

from __future__ import annotations

from .texts import and_list, phrase

# ── What this household says ──────────────────────────────────────────


def spoken_vocabulary(people, topics=()) -> str:
    """A hint for whisper about the words this family uses.

    Whisper decodes against it, so names it would otherwise hear as
    ordinary words come back right. This is the only place a name can be
    fixed: the polish pass is forbidden from changing words, and it is
    right to be -- the memories room holds what people said to their
    children, and a model quietly editing that is not a transcript any
    more. So the input is corrected instead of the output.

    Phrased as a sentence rather than a bare list because that is what
    the parameter is for: whisper treats it as preceding speech, and a
    list of nouns biases the decoder toward answering in lists.
    """
    names = [n.strip() for n in people if n and n.strip()]
    subjects = [t.strip() for t in topics if t and t.strip()]
    parts = []
    if names:
        parts.append(phrase("vocab_people").format(
            names=and_list(list(dict.fromkeys(names)))))
    if subjects:
        parts.append(phrase("vocab_topics").format(
            topics=and_list(list(dict.fromkeys(subjects)))))
    return " ".join(parts)
