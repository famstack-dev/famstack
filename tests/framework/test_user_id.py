"""Behavior tests for user identity resolution.

user_id() is the single source of truth for deriving a username from a
users.toml entry. Every place that needs a username (Matrix accounts,
Immich accounts, CLI commands) calls this function instead of accessing
user["id"] directly.

The function handles two cases:
  1. Explicit 'id' field — use it as-is
  2. No 'id' — derive from 'name' (first word, lowercased)
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))

from stack import user_id


class TestExplicitId:
    """When users.toml has an explicit 'id' field, use it unchanged."""

    def test_uses_explicit_id(self):
        assert user_id({"id": "artie", "name": "Homer Schmidt"}) == "artie"

    def test_explicit_id_takes_precedence_over_name(self):
        assert user_id({"id": "admin", "name": "Homer"}) == "admin"


class TestDerivedFromName:
    """When no 'id' is set, derive from the name: first word, lowercased."""

    def test_single_name(self):
        assert user_id({"name": "Homer"}) == "homer"

    def test_full_name_uses_first_word(self):
        assert user_id({"name": "Homer Schmidt"}) == "homer"

    def test_lowercased(self):
        assert user_id({"name": "HOMER"}) == "homer"

    def test_mixed_case(self):
        assert user_id({"name": "Sarah-Jane"}) == "sarah-jane"


class TestEdgeCases:

    def test_empty_id_falls_through_to_name(self):
        assert user_id({"id": "", "name": "Homer"}) == "homer"

    def test_missing_name_raises(self):
        with pytest.raises(KeyError):
            user_id({})
