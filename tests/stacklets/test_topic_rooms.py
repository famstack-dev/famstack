"""Topic-room name parsing and slug derivation.

A Matrix room whose name begins with `Thema:` (German) or `Topic:`
(English) is a topic room. The archivist binds it to a folder in the
memory vault, routes every capture in that room to that folder, and
seeds every capture with the topic's tag.

This module is the parser layer: pure functions, no Matrix, no
filesystem. The full design lives at `docs/design/brain/topic-rooms.md`.

The parser is split into four concerns, each independently tested
below:

  - `parse_topic_name`  recognize the prefix, extract display name
  - `derive_slug`       NFD-fold + hyphenate the display name
  - `is_reserved`       refuse slugs that collide with built-ins
  - `scope_from_members` decide personal vs. shared at bootstrap time

Together they answer: "does this room name describe a topic, and if
so, what slug does it own, and is the topic personal or shared?"
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "stacklets" / "docs" / "bot"))

from topic_rooms import (  # noqa: E402
    binding_from_state,
    bucket_for_scope,
    derive_slug,
    is_reserved,
    make_room_state,
    parse_topic_name,
    scope_from_members,
)


# ── Prefix recognition ───────────────────────────────────────────────────

class TestPrefixRecognition:
    """Both `Thema:` (de) and `Topic:` (en) are always accepted, no
    matter what the household's `[core] language` is. A bilingual
    household should be able to name its rooms in either language
    without first reconfiguring the stack."""

    def test_thema_de_prefix(self):
        parsed = parse_topic_name("Thema: Camping")
        assert parsed is not None
        assert parsed.display_name == "Camping"
        assert parsed.prefix_lang == "de"

    def test_topic_en_prefix(self):
        parsed = parse_topic_name("Topic: Photography")
        assert parsed is not None
        assert parsed.display_name == "Photography"
        assert parsed.prefix_lang == "en"

    def test_prefix_is_case_insensitive(self):
        """Mobile keyboards capitalize the first letter; the parser
        must not punish that. `THEMA:` and `thema:` both work."""
        assert parse_topic_name("THEMA: Camping") is not None
        assert parse_topic_name("thema: camping") is not None
        assert parse_topic_name("Topic: Camping") is not None
        assert parse_topic_name("topic: camping") is not None

    def test_whitespace_tolerated_around_colon(self):
        """`Thema:Camping` (no space), `Thema:  Camping` (extra spaces),
        and leading whitespace all parse to the same display name."""
        assert parse_topic_name("Thema:Camping").display_name == "Camping"
        assert parse_topic_name("Thema:  Camping").display_name == "Camping"
        assert parse_topic_name("  Thema: Camping").display_name == "Camping"

    def test_display_name_preserves_casing(self):
        """The slug is normalized, but the display name as humans
        wrote it survives intact. `Van Life` stays `Van Life`."""
        assert parse_topic_name("Thema: Van Life").display_name == "Van Life"
        assert parse_topic_name("Topic: 3D Printing").display_name == "3D Printing"


# ── Non-topic room names ─────────────────────────────────────────────────

class TestNonTopicNames:
    """A room name without the prefix is not a topic room. The parser
    returns `None`, the archivist proceeds with normal capture routing."""

    def test_empty_string(self):
        assert parse_topic_name("") is None

    def test_plain_room_name(self):
        assert parse_topic_name("documents") is None
        assert parse_topic_name("Family Chat") is None

    def test_prefix_with_no_body(self):
        """`Thema:` alone is a malformed topic room name. We refuse
        to bootstrap rather than create a topic with no display name
        and no slug."""
        assert parse_topic_name("Thema:") is None
        assert parse_topic_name("Topic:   ") is None

    def test_prefix_substring_does_not_match(self):
        """`thema` inside a longer word must not trigger detection.
        Only the leading-prefix form counts."""
        assert parse_topic_name("Anti-Topic Discussion") is None
        assert parse_topic_name("Mein Thema heute") is None


# ── Slug derivation ──────────────────────────────────────────────────────

class TestSlugDerivation:
    """Slugs are the bucket path on disk AND the value of `topics:`
    in every file beneath that bucket. They must be stable, lowercase,
    ASCII, and filesystem-safe."""

    def test_simple_word_lowercased(self):
        assert derive_slug("Camping") == "camping"

    def test_multiple_words_hyphenated(self):
        assert derive_slug("Van Life") == "van-life"

    def test_multiple_spaces_collapse(self):
        """Two spaces between words collapse to one hyphen, not two."""
        assert derive_slug("Van    Life") == "van-life"

    def test_diacritics_ascii_folded(self):
        """A bilingual household writes `Café` or `Vélo`; the on-disk
        slug strips the diacritics so `grep` and tab-completion work
        on any terminal."""
        assert derive_slug("Café Hopping") == "cafe-hopping"
        assert derive_slug("Vélo") == "velo"
        assert derive_slug("Über-Sport") == "uber-sport"

    def test_numbers_preserved(self):
        """A topic about 3D printing keeps the `3`. Numbers are
        first-class slug characters."""
        assert derive_slug("3D Printing") == "3d-printing"
        assert derive_slug("2026 Italy Trip") == "2026-italy-trip"

    def test_special_characters_become_hyphens(self):
        """Apostrophes, slashes, parens, and other punctuation collapse
        to a single hyphen. The result stays slug-shaped."""
        assert derive_slug("Marge's Garden") == "marge-s-garden"
        assert derive_slug("Side/Projects") == "side-projects"
        assert derive_slug("Plans (2027)") == "plans-2027"

    def test_leading_trailing_hyphens_stripped(self):
        assert derive_slug("  Camping  ") == "camping"
        assert derive_slug("-Camping-") == "camping"
        assert derive_slug("!!!Camping!!!") == "camping"

    def test_empty_returns_empty(self):
        """The caller (`parse_topic_name`) handles empty display names
        before calling derive_slug. But if it ever does, an empty
        return is the contract."""
        assert derive_slug("") == ""
        assert derive_slug("   ") == ""
        assert derive_slug("!!!") == ""

    def test_long_slug_truncated_to_40_chars(self):
        """Filesystem-safe slugs are bounded. 40 chars is roomy for
        human topics, tight enough not to break tab completion."""
        long_name = "A Very Long Topic Name That Exceeds Forty Characters Easily"
        slug = derive_slug(long_name)
        assert len(slug) <= 40
        # Truncation snaps at a hyphen boundary, never mid-word.
        assert not slug.endswith("-")


# ── Reserved-name check ──────────────────────────────────────────────────

class TestReservedNames:
    """Reserved slugs collide with vault built-ins (`family`, `meta`,
    `wiki`, `archive`, `_unfiled`) or with the configured
    `[core] shared_bucket`. Bootstrap refuses them; the family is
    asked to rename the room."""

    RESERVED = ("family", "meta", "wiki", "archive", "_unfiled")

    def test_reserved_slug_refused(self):
        assert is_reserved("family", self.RESERVED) is True
        assert is_reserved("meta", self.RESERVED) is True
        assert is_reserved("wiki", self.RESERVED) is True

    def test_non_reserved_slug_accepted(self):
        assert is_reserved("camping", self.RESERVED) is False
        assert is_reserved("photography", self.RESERVED) is False

    def test_empty_slug_treated_as_reserved(self):
        """A defensive guard: an empty slug never reaches bootstrap
        through the normal path, but if it does, refuse it. Better
        to fail loudly than create a vault folder at `<vault>//`."""
        assert is_reserved("", self.RESERVED) is True

    def test_personal_localparts_can_be_passed_as_reserved(self):
        """Personal buckets (`homer/`, `marge/`) are reserved on a
        per-instance basis. The archivist passes them in alongside
        the built-ins. The function does not know the household's
        member list directly."""
        reserved = (*self.RESERVED, "homer", "marge")
        assert is_reserved("homer", reserved) is True
        assert is_reserved("marge", reserved) is True
        assert is_reserved("camping", reserved) is False


# ── Scope detection ──────────────────────────────────────────────────────

class TestScopeFromMembers:
    """A topic room with one human (the sender, plus the archivist
    itself) is personal — its bucket nests under the sender's personal
    bucket. Two or more humans makes it shared. The promotion handler
    later flips personal to shared when a second human joins."""

    def test_single_human_is_personal(self):
        assert scope_from_members(human_count=1) == "personal"

    def test_two_humans_is_shared(self):
        assert scope_from_members(human_count=2) == "shared"

    def test_many_humans_is_shared(self):
        assert scope_from_members(human_count=5) == "shared"

    def test_zero_humans_falls_back_to_personal(self):
        """A room with zero humans (only bots) should not exist in
        practice. If it does, default to personal — the safer scope.
        The next human to join triggers promotion."""
        assert scope_from_members(human_count=0) == "personal"


# ── Parsed-name shape ────────────────────────────────────────────────────

class TestParsedTopicNameShape:
    """The parser's return value carries everything the bootstrap
    handler needs: display name, slug, prefix language. The display
    name keeps original casing for use in `about.md` frontmatter;
    the slug is what goes on disk and into `topics:`."""

    def test_parsed_carries_display_name_and_slug(self):
        parsed = parse_topic_name("Thema: Van Life")
        assert parsed.display_name == "Van Life"
        assert parsed.slug == "van-life"

    def test_parsed_carries_prefix_language(self):
        """`prefix_lang` lets downstream code log which language the
        room was named in. Doesn't affect routing — slug is the
        identity."""
        de = parse_topic_name("Thema: Camping")
        en = parse_topic_name("Topic: Camping")
        assert de.prefix_lang == "de"
        assert en.prefix_lang == "en"
        # Same display + slug regardless of which prefix language.
        assert de.slug == en.slug == "camping"

    def test_diacritics_resolved_at_parse_time(self):
        """The parser handles slug derivation internally; the caller
        gets a ready-to-use slug without a second function call."""
        parsed = parse_topic_name("Thema: Café Hopping")
        assert parsed.display_name == "Café Hopping"
        assert parsed.slug == "cafe-hopping"


# ── Bucket composition ──────────────────────────────────────────────────

class TestBucketForScope:
    """Topics nest inside the bucket that owns them. Shared topics
    under `<shared_bucket>/`, personal topics under `<localpart>/`.
    The top level of the vault stays purely access-scope; topics
    never appear there."""

    def test_shared_topic_nested_under_shared_bucket(self):
        assert bucket_for_scope(
            "camping", "shared",
            sender_localpart="homer", shared_bucket="family",
        ) == "family/camping"

    def test_shared_bucket_slug_is_configurable(self):
        """Deskstack uses `office`; a surname household might use
        `simpson`. The composition reads the configured slug, not
        a hard-coded `family`."""
        assert bucket_for_scope(
            "camping", "shared",
            sender_localpart="homer", shared_bucket="office",
        ) == "office/camping"

    def test_personal_topic_nested_under_localpart(self):
        """Personal topics keep the same shape they always had:
        `<localpart>/<slug>`. The privacy boundary is unchanged."""
        assert bucket_for_scope(
            "gravel", "personal",
            sender_localpart="homer", shared_bucket="family",
        ) == "homer/gravel"

    def test_different_localparts_get_different_personal_paths(self):
        """A personal `camping` topic by Homer is a different bucket
        from a personal `camping` topic by Marge. They never collide."""
        assert bucket_for_scope(
            "camping", "personal",
            sender_localpart="homer", shared_bucket="family",
        ) == "homer/camping"
        assert bucket_for_scope(
            "camping", "personal",
            sender_localpart="marge", shared_bucket="family",
        ) == "marge/camping"

    def test_shared_and_personal_can_coexist_with_same_slug(self):
        """`family/camping/` (shared) and `homer/camping/` (personal)
        live in different bucket trees. Step 5 promotion is the
        bucket-to-bucket move between them."""
        shared = bucket_for_scope(
            "camping", "shared",
            sender_localpart="homer", shared_bucket="family",
        )
        personal = bucket_for_scope(
            "camping", "personal",
            sender_localpart="homer", shared_bucket="family",
        )
        assert shared == "family/camping"
        assert personal == "homer/camping"
        assert shared != personal


# ── Room state shape ────────────────────────────────────────────────────

class TestMakeRoomState:
    """The `dev.famstack.capture` event the archivist writes on bootstrap.
    Schema is pinned in docs/design/brain/topic-rooms.md §Room state
    schema; these tests pin the implementation matches."""

    PARSED = parse_topic_name("Thema: Camping")
    BOOTSTRAPPED_AT = "2026-06-09T12:00:00Z"

    def _state(self, scope="shared", shared_bucket="family"):
        return make_room_state(
            parsed=self.PARSED, scope=scope,
            bootstrapped_by="@homer:home", sender_localpart="homer",
            shared_bucket=shared_bucket,
            bootstrapped_at=self.BOOTSTRAPPED_AT,
        )

    def test_kind_is_topic(self):
        """`kind=topic` is the discriminator downstream consumers
        match on (vs. the existing `kind=capture` / `kind=document_drop`
        room types)."""
        assert self._state()["kind"] == "topic"

    def test_shared_topic_bucket_nested_under_shared_bucket(self):
        state = self._state(scope="shared")
        assert state["bucket"] == "family/camping"
        assert state["scope"] == "shared"

    def test_shared_topic_respects_configured_shared_bucket(self):
        """Deskstack households use `office` as their shared bucket
        slug; the topic state must follow."""
        state = self._state(scope="shared", shared_bucket="office")
        assert state["bucket"] == "office/camping"

    def test_personal_topic_bucket_nests_under_sender(self):
        state = self._state(scope="personal")
        assert state["bucket"] == "homer/camping"
        assert state["scope"] == "personal"

    def test_default_topics_is_single_slug(self):
        """Forward-compatible as a list, but v1 always has exactly
        the topic's own slug — the tag-seed invariant."""
        assert self._state()["default_topics"] == ["camping"]

    def test_display_name_preserved(self):
        assert self._state()["display_name"] == "Camping"

    def test_extract_knowledge_defaults_true(self):
        """Topic rooms inherit the existing `extract_knowledge: true`
        default from the capture room contract — the deriver should
        process their captures."""
        assert self._state()["extract_knowledge"] is True

    def test_bootstrap_provenance_recorded(self):
        """`bootstrapped_at` and `bootstrapped_by` make the bootstrap
        audit-able from room history. Useful for the future eager
        on-invite handler and for `stack memory topic list`."""
        state = self._state()
        assert state["bootstrapped_at"] == self.BOOTSTRAPPED_AT
        assert state["bootstrapped_by"] == "@homer:home"


# ── Routing binding ─────────────────────────────────────────────────────

class TestBindingFromState:
    """The archivist reads existing room state on every capture and
    extracts a routing binding. Malformed or non-topic state returns
    None — the archivist falls back to sender-based routing."""

    SHARED_STATE = {
        "kind": "topic",
        "bucket": "family/camping",
        "slug": "camping",
        "display_name": "Camping",
        "default_topics": ["camping"],
        "scope": "shared",
        "extract_knowledge": True,
    }

    def test_extracts_binding_from_valid_state(self):
        binding = binding_from_state(self.SHARED_STATE)
        assert binding is not None
        assert binding.bucket == "family/camping"
        assert binding.seed_topics == ["camping"]
        assert binding.display_name == "Camping"
        assert binding.scope == "shared"
        assert binding.slug == "camping"

    def test_personal_topic_carries_nested_bucket(self):
        state = {**self.SHARED_STATE,
                 "bucket": "homer/camping", "scope": "personal"}
        binding = binding_from_state(state)
        assert binding is not None
        assert binding.bucket == "homer/camping"
        assert binding.scope == "personal"

    def test_none_state_returns_none(self):
        assert binding_from_state(None) is None
        assert binding_from_state({}) is None

    def test_non_topic_kind_returns_none(self):
        """The existing capture-room and document-drop states ride
        the same event type. Routing must ignore them."""
        state = {**self.SHARED_STATE, "kind": "capture"}
        assert binding_from_state(state) is None

    def test_malformed_state_returns_none(self):
        """Missing bucket, slug, display_name, or invalid scope all
        return None. Safer to fall back than to route to a junk path."""
        for missing in ("bucket", "slug", "display_name"):
            state = {**self.SHARED_STATE}
            state[missing] = ""
            assert binding_from_state(state) is None
        assert binding_from_state(
            {**self.SHARED_STATE, "scope": "weird"},
        ) is None

    def test_filters_non_string_default_topics(self):
        """Defensive: a malformed default_topics list (mixed types)
        is filtered to clean strings rather than rejected outright.
        Bootstrap's intent (the seed tag) usually survives even when
        the state has been hand-edited."""
        state = {**self.SHARED_STATE,
                 "default_topics": ["camping", None, 42, "", "outdoor"]}
        binding = binding_from_state(state)
        assert binding is not None
        assert binding.seed_topics == ["camping", "outdoor"]
