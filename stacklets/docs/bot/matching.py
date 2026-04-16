"""Entity matching for Paperless-ngx.

Pure functions for matching LLM-returned entity names against existing
Paperless entries (correspondents, tags, document types). No dependencies
on aiohttp, Matrix, or any I/O -- these are unit-testable string operations.

The problem:
  LLM says "Kwik-E-Mart Inc."     -> Paperless has "Kwik-E-Mart"                  -> duplicate!
  LLM says "Springfield Nuclear"   -> Paperless has "Springfield Nuclear Power Plant" -> duplicate!
  LLM says "Burns Industries LLC"  -> Paperless has "Burns Industries"             -> duplicate!

fuzzy_match_entity catches these before the Archivist creates a new entry.

Two matching strategies:
  1. Case-insensitive exact match
  2. Word-boundary containment (token prefix matching)
"""

from __future__ import annotations

import re
from typing import Any

# Paperless limits titles to 128 characters.
MAX_TITLE_LENGTH = 128

# LLMs return "null", "None", "N/A" instead of actual null.
_EMPTY_STRINGS = frozenset(("null", "none", "n/a", ""))


def _is_empty(value: str | None) -> bool:
    """True if the value is None or an LLM-style empty string.

    >>> _is_empty(None)
    True
    >>> _is_empty("null")
    True
    >>> _is_empty("None")
    True
    >>> _is_empty("Insurance")
    False
    """
    if not value or not isinstance(value, str):
        return True
    return value.lower().strip() in _EMPTY_STRINGS

# Split on whitespace and common entity-name punctuation (hyphens, dots,
# apostrophes, commas) so "Kwik-E-Mart" becomes ["kwik", "e", "mart"] and
# "Moe's" becomes ["moe", "s"]. This lets word-boundary checks work on
# hyphenated and possessive names.
_WORD_SPLIT = re.compile(r"[\s\-.,/']+")


def _tokenize(name: str) -> list[str]:
    """Split a name into lowercase word tokens.

    >>> _tokenize("Kwik-E-Mart Inc.")
    ['kwik', 'e', 'mart', 'inc']
    >>> _tokenize("Moe's Tavern")
    ['moe', 's', 'tavern']
    >>> _tokenize("ADAC e.V.")
    ['adac', 'e', 'v']
    """
    return [t for t in _WORD_SPLIT.split(name.lower().strip()) if t]


def _is_word_boundary_match(shorter: str, longer: str) -> bool:
    """Check if `shorter` appears in `longer` at a word boundary.

    Safe:
      "Springfield" in "Springfield Nuclear Power Plant"  -> True
      "ADAC"        in "ADAC e.V."                        -> True

    Blocked:
      "Spring"      in "Springfield"     -> False (mid-word)
      "Bank"        in "Bundesbank"      -> False (mid-word)
      "Art"         in "Arthur"          -> False (mid-word)

    >>> _is_word_boundary_match("Springfield", "Springfield Nuclear Power Plant")
    True
    >>> _is_word_boundary_match("Spring", "Springfield")
    False
    """
    short_tokens = _tokenize(shorter)
    long_tokens = _tokenize(longer)

    if not short_tokens or not long_tokens:
        return False

    n = len(short_tokens)
    if n > len(long_tokens):
        return False

    return short_tokens == long_tokens[:n]


def build_person_lookup(tags: dict[str, Any]) -> dict[str, str]:
    """Build a clean-name -> prefixed-tag lookup from Paperless tags.

    Paperless stores person tags as "Person: Homer", "Person: Marge".
    The LLM returns just "Homer". This builds the bridge.

    >>> build_person_lookup({"Person: Homer": 1, "Person: Marge": 2, "Insurance": 3})
    {'Homer': 'Person: Homer', 'Marge': 'Person: Marge'}
    >>> build_person_lookup({"Insurance": 3})
    {}
    """
    return {
        tag.replace("Person: ", ""): tag
        for tag in tags
        if tag.startswith("Person: ")
    }


def match_persons(names: str | list | None, tags: dict[str, Any]) -> list[str]:
    """Match LLM-returned person name(s) to Paperless person tags.

    Handles all the ways an LLM might return person data:
    - Single name: "Homer"                  -> ["Person: Homer"]
    - Full name:   "Homer J. Simpson"       -> ["Person: Homer"]
    - Multiple:    ["Homer", "Marge"]       -> ["Person: Homer", "Person: Marge"]
    - Prefixed:    "Person: Homer"          -> ["Person: Homer"]
    - Literal null: "null"                  -> []
    - None:         None                    -> []

    Uses prefer_longest=True so "Homer Jr. Simpson" matches "Homer Jr"
    (most specific) rather than "Homer" when both exist.

    >>> tags = {"Person: Homer": 1, "Person: Marge": 2, "Insurance": 3}
    >>> match_persons("Homer", tags)
    ['Person: Homer']
    >>> match_persons(["Homer", "Marge"], tags)
    ['Person: Homer', 'Person: Marge']
    >>> match_persons("Homer J. Simpson", tags)
    ['Person: Homer']
    >>> match_persons(None, tags)
    []
    >>> match_persons("null", tags)
    []
    >>> match_persons("Lisa", tags)
    []
    """
    if not names:
        return []

    # Normalize to a list. LLM might return a string or a list.
    if isinstance(names, str):
        name_list = [names]
    elif isinstance(names, list):
        name_list = names
    else:
        return []

    lookup = build_person_lookup(tags)
    if not lookup:
        return []

    matched_tags = []
    for name in name_list:
        if not name or not isinstance(name, str):
            continue
        if _is_empty(name):
            continue

        clean_name = name.replace("Person: ", "").strip()
        if not clean_name:
            continue

        # prefer_longest=True: "Homer Jr. Simpson" should match "Homer Jr"
        # (most specific) not "Homer" (most general).
        matched = fuzzy_match_entity(clean_name, lookup, prefer_longest=True)
        if matched:
            tag = lookup[matched]
            if tag not in matched_tags:
                matched_tags.append(tag)

    return matched_tags


def match_topics(
    topics: str | list | None,
    category_tags: dict[str, Any],
) -> list[str]:
    """Match LLM-returned topic(s) to existing Paperless category tags.

    Handles the ways an LLM might return topic data:
    - Single string: "Insurance"             -> ["Insurance"]
    - List:          ["Insurance", "Medical"] -> ["Insurance", "Medical"]
    - Literal null:  "null"                   -> []
    - None:          None                     -> []

    Each topic is fuzzy-matched against existing category tags (excludes
    "Person: " tags). Matched names are returned; unmatched originals are
    returned as-is so the caller can create them in Paperless.

    Returns (matched, new) tuple:
    - matched: list of existing tag names that fuzzy-matched
    - new: list of topic strings that need to be created

    >>> tags = {"Insurance": 1, "Shopping": 2, "Medical": 3}
    >>> match_topics("Insurance", tags)
    (['Insurance'], [])
    >>> match_topics(["Insurance", "Medical"], tags)
    (['Insurance', 'Medical'], [])
    >>> match_topics(["Insurance", "School"], tags)
    (['Insurance'], ['School'])
    >>> match_topics("null", tags)
    ([], [])
    >>> match_topics(None, tags)
    ([], [])
    """
    if not topics:
        return [], []

    # Normalize to list
    if isinstance(topics, str):
        topic_list = [topics]
    elif isinstance(topics, list):
        topic_list = topics
    else:
        return [], []

    matched = []
    new = []
    seen = set()

    for topic in topic_list:
        if not topic or not isinstance(topic, str):
            continue
        if _is_empty(topic):
            continue

        topic_clean = topic.strip()
        if topic_clean.lower() in seen:
            continue
        seen.add(topic_clean.lower())

        existing = fuzzy_match_entity(topic_clean, category_tags)
        if existing:
            if existing not in matched:
                matched.append(existing)
        else:
            if topic_clean not in new:
                new.append(topic_clean)

    return matched, new


def build_document_event(
    doc_id: int,
    classification: dict,
    *,
    resolved_topics: list[str] | None = None,
    resolved_persons: list[str] | None = None,
    resolved_correspondent: str | None = None,
    resolved_type: str | None = None,
    paperless_url: str = "",
) -> dict:
    """Build a structured event payload for a classified document.

    Attaches full metadata as a custom Matrix event (dev.famstack.document)
    so downstream bots can consume it without parsing human-readable text.

    >>> evt = build_document_event(42, {"title": "ADAC - Kfz EUR 340", "summary": "Insurance renewal"}, resolved_topics=["Insurance"], resolved_persons=["Homer"], resolved_correspondent="ADAC")
    >>> evt["type"]
    'dev.famstack.document'
    >>> evt["body"]["doc_id"]
    42
    >>> evt["body"]["topics"]
    ['Insurance']
    >>> evt["body"]["persons"]
    ['Homer']
    """
    body = {
        "doc_id": doc_id,
        "title": classification.get("title", ""),
        "date": classification.get("date"),
        "topics": resolved_topics or [],
        "persons": resolved_persons or [],
        "correspondent": resolved_correspondent,
        "document_type": resolved_type,
        "summary": classification.get("summary", ""),
        "facts": classification.get("facts", []),
        "action_items": classification.get("action_items", []),
    }
    if paperless_url:
        body["url"] = f"{paperless_url}/documents/{doc_id}/details"

    return {
        "type": "dev.famstack.document",
        "body": body,
    }


def deduplicate_hashtags(*labels: str | None) -> list[str]:
    """Build a deduplicated list of #hashtags for the chat summary.

    Removes duplicates case-insensitively and strips "Person: " prefixes.
    Filters out None, empty strings, and the literal "null".

    >>> deduplicate_hashtags("Shopping", "Homer", "Invoice", "Kwik-E-Mart")
    ['#Shopping', '#Homer', '#Invoice', '#Kwik-E-Mart']
    >>> deduplicate_hashtags("Invoice", "Homer", "Invoice", "Homer")
    ['#Invoice', '#Homer']
    >>> deduplicate_hashtags("Person: Homer", "Homer")
    ['#Homer']
    >>> deduplicate_hashtags(None, "", "null", "Shopping")
    ['#Shopping']
    >>> deduplicate_hashtags()
    []
    """
    result = []
    seen = set()
    for label in labels:
        if _is_empty(label):
            continue
        clean = label.replace("Person: ", "").strip()
        if not clean:
            continue
        if clean.lower() not in seen:
            seen.add(clean.lower())
            result.append(f"#{clean}")
    return result


def fuzzy_match_entity(
    name: str,
    existing: dict[str, Any],
    *,
    prefer_longest: bool = False,
) -> str | None:
    """Find a close match for `name` among existing Paperless entity names.

    Two strategies:

    1. Case-insensitive exact: "kwik-e-mart" matches "Kwik-E-Mart"
    2. Word-boundary containment: the tokens of the shorter name must be
       a contiguous prefix of the longer name's tokens.
       "Kwik-E-Mart" matches "Kwik-E-Mart Inc." (token prefix)
       "Spring" does NOT match "Springfield" (not a token prefix)
       Minimum 3 chars on the shorter side to skip "AG", "Dr", etc.

    When multiple candidates match:
    - prefer_longest=False (default): picks shortest (most general).
      Good for correspondents: "ADAC" over "ADAC Versicherung".
    - prefer_longest=True: picks longest (most specific).
      Good for persons: "Homer Jr" over "Homer".

    >>> fuzzy_match_entity("Springfield Nuclear", {"Springfield Nuclear Power Plant": 1})
    'Springfield Nuclear Power Plant'
    >>> fuzzy_match_entity("Kwik-E-Mart Inc.", {"Kwik-E-Mart": 2, "Moe's Tavern": 3})
    'Kwik-E-Mart'
    >>> fuzzy_match_entity("Spring", {"Springfield Nuclear": 5})  # mid-word, no match
    """
    if not name or not existing:
        return None

    name_lower = name.lower().strip()
    if not name_lower:
        return None

    pick = max if prefer_longest else min

    # Strategy 1: case-insensitive exact match.
    for existing_name in existing:
        if name_lower == existing_name.lower().strip():
            return existing_name

    # Strategy 2: word-boundary containment.
    # The shorter name's tokens must be a prefix of the longer name's tokens.
    #   "ADAC" -> ["adac"] is a prefix of "ADAC e.V." -> ["adac", "e", "v"]  -> match
    #   "Spring" -> ["spring"] is NOT a prefix of "Springfield" -> ["springfield"] -> no match
    containment_matches = []
    for existing_name in existing:
        existing_lower = existing_name.lower().strip()
        shorter, longer = (name_lower, existing_lower) if len(name_lower) <= len(existing_lower) else (existing_lower, name_lower)
        if len(shorter) < 3:
            continue
        if _is_word_boundary_match(shorter, longer):
            containment_matches.append(existing_name)

    if len(containment_matches) == 1:
        return containment_matches[0]
    if len(containment_matches) > 1:
        return pick(containment_matches, key=len)

    return None
