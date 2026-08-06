"""Behavior tests for how the household is named back to itself.

stack.toml's [core] stack_owner holds whatever surname the installer
asked for. Two surfaces render it: the installer's closing line ("The
Simpsons are online") and the family wiki's title. Both go through these
helpers so the household is spelled the same way in both places.

The awkward part is that "Family name" is a question people answer two
different ways. One person types "Simpson", the next types "Simpsons",
and both mean the same household. Getting that wrong prints "The
Simpsonss" on the first screen a family ever sees.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))

from stack import family_display_name, family_plural


class TestPluralisation:
    """A surname becomes the way you address the whole household."""

    def test_singular_surname_gains_an_s(self):
        assert family_plural("Simpson") == "Simpsons"

    def test_surname_already_plural_is_left_alone(self):
        assert family_plural("Simpsons") == "Simpsons"

    def test_plural_check_ignores_case(self):
        # "SIMPSONS" already ends in an s; shouting is not a new surname.
        assert family_plural("SIMPSONS") == "SIMPSONS"

    def test_case_of_the_name_is_never_changed(self):
        # The family's own capitalisation is theirs, not ours to correct.
        assert family_plural("van Houten") == "van Houtens"


class TestDisplayName:
    """The full phrase, as it appears on screen."""

    def test_reads_as_the_family(self):
        assert family_display_name("Simpson") == "The Simpsons"

    def test_does_not_double_the_plural(self):
        assert family_display_name("Simpsons") == "The Simpsons"


class TestUnset:
    """An instance with no stack_owner must not print a half-formed name.

    Instances created before stack_owner existed still run, so this is a
    live path, not a hypothetical. Returning empty lets each caller pick
    its own fallback instead of showing the family "The s".
    """

    def test_missing_owner_is_empty(self):
        assert family_display_name("") == ""
        assert family_plural("") == ""

    def test_none_is_empty(self):
        assert family_display_name(None) == ""

    def test_whitespace_only_is_empty(self):
        assert family_display_name("   ") == ""

    def test_surrounding_whitespace_is_trimmed(self):
        assert family_display_name("  Simpson  ") == "The Simpsons"
