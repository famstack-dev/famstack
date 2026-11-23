"""The memory's audience rule: who may read the card at a path.

A person's own folder (`marge/...`) is read only when that person is the
whole audience; the shared folder and the top level are read by everyone
(`stack.cards.audience.readable`). The path often comes from a caller's
own words (a topic, a scope, a page), so a path that names a folder in
another way than plainly (`family/../marge`) is read by nobody: the rule
then cannot take the first folder of the text for the folder it means.
"""

from __future__ import annotations

import pytest

from stack.cards.audience import readable

BART = frozenset({"bart"})
MARGE = frozenset({"marge"})


@pytest.mark.parametrize("path, audience, expected", [
    ("marge/notes/gift.md", MARGE, True),
    ("marge/notes/gift.md", BART, False),
    ("family/notes/tent.md", BART, True),
    ("README.md", BART, True),
], ids=["her own folder", "another's folder", "the shared folder", "the top level"])
def test_a_plain_path_is_read_by_its_folders_rule(path, audience, expected):
    assert readable(path, audience) is expected


@pytest.mark.parametrize("path", [
    "family/../marge/x.md",
    "../marge/x.md",
    "family/./x.md",
    "./family/x.md",
    "family//x.md",
    "/marge/x.md",
    "family/notes/..",
])
@pytest.mark.parametrize("audience", [BART, MARGE], ids=["bart", "marge"])
def test_a_path_with_a_dot_or_empty_segment_is_read_by_nobody(path, audience):
    assert readable(path, audience) is False
