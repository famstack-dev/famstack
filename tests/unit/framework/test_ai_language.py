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
