"""Language names for the stack's prompts.

The stack's prompts are written in English. A model answers an English
prompt in English unless the prompt names the language it must write
in, so every prompt that writes for the family names that language.
These tests pin how a language code becomes that name.
"""

from stack.ai.language import LANGUAGES, language_name


def test_a_known_code_is_named_in_english():
    assert language_name("de") == "German"
    assert language_name("es") == "Spanish"


def test_case_and_region_do_not_change_the_language():
    """`[core] language` and a room setting are typed by people."""
    assert language_name("DE") == "German"
    assert language_name("pt-BR") == "Portuguese"


def test_an_unknown_code_is_named_by_the_code_itself():
    """A household language outside the table still gives the prompt a
    target. "Write in sv" is better than no target at all, which is how
    a Swedish household would get English."""
    assert language_name("sv") == "sv"


def test_the_household_languages_famstack_ships_are_listed():
    assert {"de", "en"} <= set(LANGUAGES)


# ── Which language a message is written in ───────────────────────────────
#
# The agent states the answer language in code, as it states weekdays: a
# rule in the prompt lost to a room whose history was German, and every
# English question got a German answer.

from stack.ai.language import written_in  # noqa: E402


def test_an_english_message_is_english():
    assert written_in("Stacky, what's on this week?") == "en"
    assert written_in("Put Grandpa's birthday dinner on Friday at 7pm in the calendar.") == "en"


def test_a_german_message_is_german():
    assert written_in("Stacky, was steht morgen an?") == "de"
    assert written_in("Die Bananen hab ich gekauft und das Training fällt aus.") == "de"


def test_the_other_household_languages_are_told_apart():
    assert written_in("Qu'est-ce qu'il y a sur la liste des courses ?") == "fr"
    assert written_in("¿Qué hay en la lista de la compra para mañana?") == "es"
    assert written_in("Wat staat er op het boodschappenlijstje voor morgen?") == "nl"


def test_a_message_too_short_to_tell_is_no_language():
    """A name or an "ok" says nothing; guessing would flip the answer."""
    assert written_in("Stacky?") == ""
    assert written_in("ok") == ""


def test_names_and_list_words_do_not_decide_it():
    """German list names in an English question stay an English question."""
    assert written_in("Stacky, what is on the Einkaufsliste and the Wochenmarkt list?") == "en"
