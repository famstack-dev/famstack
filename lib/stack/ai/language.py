"""Language names for prompts.

The stack's prompts are written in English, and a model answers an
English prompt in English unless the prompt names the language to write
in. Prompts that write for the family name it with `language_name`.
"""

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
