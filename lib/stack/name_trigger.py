"""Does this message address a bot by name?

Matrix answers "is the bot mentioned?" with the `m.mentions` payload,
which only exists when the sender picked the bot out of an autocomplete
list. That is a fine rule for Slack and a poor one for a family room,
where people type what they would say out loud:

    Stacky, what's on our list?

No pill, no `m.mentions`, no reply. This module supplies the missing
half of the question, and nothing else -- it is pure text in, bool out,
so the rule can be argued with in tests rather than in a running
container. It is stdlib-only and imports nothing from the framework:
the bot-runner uses it as `stack.name_trigger`, and the agent container
mounts this one file as `name_trigger`, so both agree on what counts.

WHAT COUNTS AS BEING ADDRESSED

Only the vocative: the name at the start of the message, or tacked on
at the end. Both are how someone speaks to the agent.

    Stacky, what's on our list?          addressed
    stacky whats on our list             addressed (case, punctuation)
    hey Stacky can you strike item 3     addressed (greeting first)
    what's on our list, Stacky?          addressed (trailing vocative)

A name in the middle of a sentence is someone *talking about* the
agent, not to it, and is deliberately ignored:

    I asked Stacky and it said no        not addressed
    we should get Stacky to do this      not addressed

THE ONE CASE THIS GETS WRONG, KNOWINGLY

A sentence that opens with the name in the third person reads as an
address by this rule:

    Stacky got that wrong yesterday      addressed (false positive)

The alternative is to demand punctuation after the name, which would
drop "Stacky whats on our list" -- the single most likely thing anyone
types. One unwanted reply is a smaller failure than a bot that ignores
the plainest way of asking it something, so the tradeoff is taken
deliberately in that direction.

THE NAME IS CONFIGURED, NOT ASSUMED

`AGENT_NAME` is a family's choice (`{agent_name}` in stack.toml, see
the agent stacklet's manifest), so the name is a parameter here and the
comparison is case-insensitive. Renaming the agent to "Kit" must make
"kit, whats on the list" work with no code change. An empty or
whitespace name matches nothing at all, which matters: a blank pattern
would otherwise make every message in every room an address.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Openers people put before a name. Kept short on purpose: each one is a
# word that could begin a sentence, and the name still has to follow it.
_GREETINGS = r"(?:hey|hi|hello|yo|ok|okay)"


@lru_cache(maxsize=8)
def _pattern(name: str) -> re.Pattern[str]:
    """Compile the address pattern for one name.

    Cached because this runs on every group-room message that was not
    already a pill mention, and the name changes about once ever.
    """
    n = re.escape(name)
    # `(?!\w)` rather than `\b` for the end of the name: `\b` needs a
    # word character on one side, so a name ending in punctuation (an
    # emoji-ish handle, "Mr. Bot") would never satisfy it and the agent
    # would answer to nothing at all.
    return re.compile(
        # Leading address, optionally after a greeting: "Stacky, ..."
        rf"^\W*(?:{_GREETINGS}\W+)?{n}(?!\w)"
        # Trailing vocative, set off by a comma: "..., Stacky?"
        rf"|[,;]\s*{n}(?!\w)\W*$",
        re.IGNORECASE,
    )


def strip_reply_fallback(body: str) -> str:
    """Drop the quoted block Matrix prepends to a plain-text reply.

    A reply's `body` carries the message it answers as `> ` lines before
    the actual text. Without stripping them the sender's own words are
    never at the start of the body, so "Stacky, do X" sent as a reply
    would not read as an address -- which is exactly how someone follows
    up in a busy room.
    """
    lines = (body or "").splitlines()
    index = 0
    while index < len(lines) and lines[index].startswith(">"):
        index += 1
    if not index:
        return body or ""
    while index < len(lines) and not lines[index].strip():
        index += 1
    return "\n".join(lines[index:])


def addressed_by_name(body: str, name: str) -> bool:
    """True when `body` speaks to `name` rather than merely mentioning it."""
    name = (name or "").strip()
    if not name:
        return False
    text = strip_reply_fallback(body).strip()
    if not text:
        return False
    return bool(_pattern(name).search(text))


# Spellings that sound alike, folded to one. Whisper writes a name the way
# it heard it ("Staki", "Stachy", "Stacey" for "Stacky"), and these are
# the differences between such spellings in English and German.
_SOUNDS = (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss"), ("ck", "k"), ("ch", "k"), ("c", "k"),
           ("ey", "i"), ("ie", "i"), ("y", "i"))


def _sound(word: str) -> str:
    """`word` as it sounds: "Stacky", "Staki" and "Stäcki" are all "staki"."""
    word = word.casefold()
    for spelled, said in _SOUNDS:
        word = word.replace(spelled, said)
    return re.sub(r"(.)\1+", r"\1", word)


# Whisper hears these vowels in a name loosely: "Stacky" came back as
# "Stocky" and "Stucky" on the test instance.
_LOOSE_VOWELS = "aou"

# Below this many letters a changed vowel makes another word, not the name
# misheard: an agent called "Rob" would answer "Rub the pan first".
_SHORTEST_LOOSE_NAME = 4


def _sounds_like(word: str, name: str) -> bool:
    """True when `word` is `name` as whisper may have written it.

    Spellings of one sound (`_SOUNDS`) are always the name. A name of
    four letters or more may also differ in one place: a vowel among a,
    o and u ("Stocky" for "Stacky"), or a final e for a name that ends in
    i or y ("Stacke", "Lotte" for "Lotti"). One place only, so "Latte"
    is not "Lotti".
    """
    heard, said = _sound(word), _sound(name)
    if heard == said:
        return True
    if len(name) < _SHORTEST_LOOSE_NAME or len(heard) != len(said):
        return False
    changed = [i for i, (h, s) in enumerate(zip(heard, said)) if h != s]
    if len(changed) != 1:
        return False
    i = changed[0]
    vowel = heard[i] in _LOOSE_VOWELS and said[i] in _LOOSE_VOWELS
    final_e = i == len(said) - 1 and heard[i] == "e" and said[i] == "i"
    return vowel or final_e


def addressed_by_voice(transcript: str, name: str) -> bool:
    """True when a transcript speaks to `name`, the name matched by its sound.

    The same places count as in typed text, the start and a trailing
    vocative, but the word there only has to sound like the name
    (`_sounds_like`). A name of several words is matched exactly.
    """
    name = (name or "").strip()
    text = strip_reply_fallback(transcript).strip()
    if not name or not text or " " in name:
        return addressed_by_name(transcript, name)
    lead = re.match(rf"^\W*(?:{_GREETINGS}\W+)?(\w+)", text, re.IGNORECASE)
    trail = re.search(r"[,;]\s*(\w+)\W*$", text)
    return any(m is not None and _sounds_like(m[1], name) for m in (lead, trail))


def strip_address(body: str, name: str) -> str:
    """The message without the words that address `name`.

    "hey Archivist, when does the insurance renew?" becomes "when does
    the insurance renew?", so the bot hears the question, not its own
    name. Unchanged when the message does not address `name`.
    """
    text = strip_reply_fallback(body).strip()
    if not addressed_by_name(text, name):
        return body or ""
    n = re.escape(name.strip())
    text = re.sub(rf"^\W*(?:{_GREETINGS}\W+)?{n}(?!\w)[\s,:;.!-]*", "", text, count=1, flags=re.IGNORECASE)
    text = re.sub(rf"[,;]\s*{n}(?!\w)(\W*)$", r"\1", text, count=1, flags=re.IGNORECASE)
    return text.strip()
