"""The release gate's version comparison, proved against the spellings we use.

Written after the gate failed on its own first run: it compared raw strings and
called `0.3.0-beta.2` (tag and CLI banner) different from `0.3.0b2` (pyproject,
PEP 440 canonical). Both name the same release. Unfixed, the gate would have
gone red on every beta tag - a check that cries wolf gets switched off, which
is worse than no check.
"""

from __future__ import annotations

import pytest

from tests.integration._check_versions import _normalise


@pytest.mark.parametrize(
    "spelling",
    [
        "0.3.0-beta.2",  # git tag / CLI banner form
        "0.3.0b2",  # PEP 440 canonical, as pyproject stores it
        "0.3.0.beta.2",
        "0.3.0-b2",
        "0.3.0BETA2",
    ],
)
def test_beta_spellings_all_agree(spelling):
    assert _normalise(spelling) == "0.3.0b2"


def test_release_versions_are_untouched():
    assert _normalise("0.3.0") == "0.3.0"
    assert _normalise("1.0.0") == "1.0.0"


def test_distinct_versions_stay_distinct():
    # The gate's whole job. Normalising must not collapse real differences.
    assert _normalise("0.3.0-beta.2") != _normalise("0.3.0-beta.3")
    assert _normalise("0.3.0-beta.2") != _normalise("0.3.0")
    assert _normalise("0.3.0-alpha.2") != _normalise("0.3.0-beta.2")
    assert _normalise("0.3.0-rc.2") != _normalise("0.3.0-beta.2")


def test_prerelease_markers_map_to_canonical_letters():
    assert _normalise("1.2.0-alpha.1") == "1.2.0a1"
    assert _normalise("1.2.0-beta.1") == "1.2.0b1"
    assert _normalise("1.2.0-rc.1") == "1.2.0rc1"
    # PEP 440 folds these spellings into rc.
    assert _normalise("1.2.0-c.1") == "1.2.0rc1"
    assert _normalise("1.2.0-preview.1") == "1.2.0rc1"


def test_bare_marker_implies_zero():
    # `1.2.0b` and `1.2.0b0` are the same release under PEP 440.
    assert _normalise("1.2.0b") == "1.2.0b0"
