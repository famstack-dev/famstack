"""Language names for prompts, and the language a message is written in.

The stack's prompts are written in English, and a model answers an
English prompt in English unless the prompt names the language to write
in. Prompts that write for the family name it with `language_name`.
"""

import re


# The codes a room may choose with `!config language`, and the English
# name a prompt uses for each.
LANGUAGES = {
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "nl": "Dutch",
    "pt": "Portuguese",
}


def language_code(value: str) -> str:
    """`DE` and `pt-BR` as the two-letter code the table uses."""
    return (value or "").strip().lower()[:2]


def language_name(value: str) -> str:
    """The English name of a language code, or the code itself when the
    table has no name for it, so a prompt always states a target."""
    code = language_code(value)
    return LANGUAGES.get(code, code)


# ── Which language a message is written in ───────────────────────────────
#
# Counted the way language identifiers start: by the short words every
# sentence of a language carries (articles, pronouns, question words,
# "tomorrow"). Names and list words count for nothing, so "the
# Einkaufsliste" stays English. Too few of them, or a tie, is no answer:
# the caller then states no language rather than a wrong one.

_COMMON_WORDS = {
    "en": "the a an and or is are was were what when where who how on in at of to for with "
          "this that it i you we my our me please put have has do does can there today "
          "tomorrow week need s",
    "de": "der die das und oder ist sind war was wann wo wer wie auf im in an am zu für mit "
          "ein eine einen ich du wir mein unser mir bitte hab habe hat nicht noch schon steht "
          "morgen heute woche gibt es den dem des aus",
    "fr": "le la les un une des et ou est sont qu que quoi quand où qui comment sur dans à au "
          "de du pour avec ce il y a je tu nous mon notre demain aujourd hui",
    "es": "el la los las un una y o es son qué que cuándo dónde quién cómo en a al de del para "
          "con hay mañana hoy semana mi nuestro",
    "it": "il lo la gli le un una e o è sono che cosa quando dove chi come su in a al di del "
          "per con c domani oggi settimana",
    "nl": "de het een en of is zijn wat wanneer waar wie hoe op in aan van voor met er ik jij "
          "we mijn ons morgen vandaag week staat",
    "pt": "o a os as um uma e ou é são que quando onde quem como em no na de do da para com há "
          "amanhã hoje semana",
}
_WORDS = {code: frozenset(words.split()) for code, words in _COMMON_WORDS.items()}

# Fewer common words than this, and a message ("ok", "Stacky?") says nothing.
_ENOUGH = 2


def written_in(text: str) -> str:
    """The code of the language `text` is written in, or "" when it cannot tell."""
    words = re.findall(r"[^\W\d_]+", (text or "").lower())
    scores = sorted(((sum(w in common for w in words), code) for code, common in _WORDS.items()),
                    reverse=True)
    (best, code), (second, _) = scores[0], scores[1]
    if best < _ENOUGH or best == second:
        return ""
    return code
