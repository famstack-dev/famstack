"""archivist_texts: the archivist's words in the chat, in the household language.

    from .archivist_texts import t, HELP_COMMANDS

    t("de", "scan_started", sender="Marge")   # the German "scan started" line
    "hilfe" in HELP_COMMANDS                   # True

The archivist answers in the stack's language. Every sentence it posts is
a key in `messages/archivist.yml`, one table per language, so English and
German stay in step and a missing German key falls back to the English
one instead of failing a reply. The commands people type (`help`, `(`,
`fertig`) are words too, so they live here beside the replies.

`language_option` builds the `!config language` entry, because its
description names the household language in each language the bot can
speak.

Every collaborator of the archivist (rooms, captures, documents, ...)
renders through `t`, usually via the bot's `t` bound to its language.
"""

from pathlib import Path

import yaml

from stack.ai.language import LANGUAGES, language_name

from .pipeline import DEFAULT_LANGUAGE, SOURCE_LANGUAGE

__all__ = ["HELP_COMMANDS", "MESSAGES", "SCAN_BEGIN", "SCAN_END", "language_option", "t"]


# ── Commands people type ────────────────────────────────────────────────────

# Open and close a multi-page scan; the closer also takes a caption.
SCAN_BEGIN = {"scan", "("}
SCAN_END = {"done", "fertig", ")"}

HELP_COMMANDS = {"help", "hilfe", "?"}


# ── Translations ─────────────────────────────────────────────────────────────

MESSAGES = yaml.safe_load(
    (Path(__file__).parent / "messages" / "archivist.yml").read_text(encoding="utf-8"),
)


def t(lang: str, key: str, **kwargs) -> str:
    """The message `key` in `lang`, filled with `kwargs`.

    Falls back to English when the language or the key is missing, and to
    the key itself when English lacks it too, so a reply never fails on a
    missing translation.

        t("en", "no_active_scan")   # the English "no scan is open" line
    """
    lang_msgs = MESSAGES.get(lang, MESSAGES["en"])
    text = lang_msgs.get(key, MESSAGES["en"].get(key, key))
    if kwargs:
        text = text.format(**kwargs)
    return text.rstrip("\n")


def language_option(household: str) -> dict:
    """The `!config language` entry: the household language by default,
    any listed language, or the language of the source. Each meaning is
    a table by language (`stack.bot.microbot.in_language`), so the
    `!config` view speaks the stack's language.

        language_option("de")["default"]   # DEFAULT_LANGUAGE
    """
    def name(code: str, lang: str) -> str:
        # A language's own table may name the languages (`language_names`);
        # otherwise the English name.
        return MESSAGES[lang].get("language_names", {}).get(code, language_name(code))

    describe = {
        DEFAULT_LANGUAGE: {lang: t(lang, "language_household", name=name(household, lang))
                           for lang in MESSAGES},
        SOURCE_LANGUAGE: {lang: t(lang, "language_source") for lang in MESSAGES},
    }
    describe.update({code: {lang: name(code, lang) for lang in MESSAGES} for code in LANGUAGES})
    return {"default": DEFAULT_LANGUAGE, "describe": describe}
