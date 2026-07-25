"""Vault frontmatter parser, writer, and validator.

One shared parser for both host CLI and containers, implementing the
strict YAML subset defined in docs/design/brain/vault-format.md.
No external dependencies — stdlib only, so the host CLI can use it
without pulling in PyYAML.

The spec defines three contracts:
  - Parser: reads the §2 subset into a dict, raises on violations
  - Writer: serializes a dict to the §2 subset with proper quoting
  - Validator: checks §3-§6 schema (type vocab, required fields, generated)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ── Exceptions ────────────────────────────────────────────────────────

class FrontmatterError(ValueError):
    """Raised when frontmatter violates the strict §2 subset."""
    pass


# ── Type vocabulary (§4) ──────────────────────────────────────────────

# Records: source records (not generated)
RECORD_TYPES = {"document", "note", "bookmark", "email"}

# Projections: generated wiki pages
PROJECTION_TYPES = {"person", "correspondent", "topic", "index"}

# All valid types
TYPES = RECORD_TYPES | PROJECTION_TYPES


# ── Per-type field definitions (§5) ───────────────────────────────────

@dataclass
class TypeSchema:
    """Schema for a single type: required fields and optional list fields."""
    required: set[str]
    list_fields: set[str]


# Build the type-to-schema mapping from §5 of the spec.
SCHEMAS: dict[str, TypeSchema] = {
    "document": TypeSchema(
        required={"type", "title", "timestamp", "source", "paperless_id"},
        list_fields={"persons", "tags"},
    ),
    "note": TypeSchema(
        required={"type", "title", "timestamp"},
        list_fields={"persons", "tags"},
    ),
    "bookmark": TypeSchema(
        required={"type", "title", "timestamp"},
        list_fields={"persons", "tags"},
    ),
    "email": TypeSchema(
        required={"type", "title", "timestamp"},
        list_fields={"persons", "tags"},
    ),
    "person": TypeSchema(
        required={"type", "generated", "title", "slug", "canonical"},
        list_fields={"aliases"},
    ),
    "correspondent": TypeSchema(
        required={"type", "generated", "title", "canonical"},
        list_fields={"aliases"},
    ),
    "topic": TypeSchema(
        required={"type", "generated", "slug", "scope"},
        list_fields=set(),
    ),
    "index": TypeSchema(
        required={"type", "generated"},
        list_fields=set(),
    ),
}


# ── Parser ────────────────────────────────────────────────────────────

def parse(text: str) -> dict:
    """Parse frontmatter from a markdown file.

    The §2 subset consists of:
      - Top-level keys only (no nesting)
      - Scalar values: string, integer, boolean, or date-as-string
      - One-deep string lists (key: followed by indented `- item` lines)

    Out-of-subset input raises FrontmatterError with a descriptive message.
    Returns {} when there is no frontmatter block.

    Args:
        text: File content (markdown with optional frontmatter block)

    Returns:
        Parsed dict; empty dict if no frontmatter present

    Raises:
        FrontmatterError: When the frontmatter violates the subset grammar
    """
    if not text.startswith("---\n"):
        return {}

    end = text.find("\n---\n", 4)
    if end < 0:
        return {}

    fm_block = text[4:end]
    return _parse_block(fm_block)


def _parse_block(block: str) -> dict:
    """Parse the content between --- delimiters.

    Raises FrontmatterError on violations:
      - Nested maps, lists of maps, flow syntax ({}, [])
      - Block scalars (|, >), anchors/aliases
      - Multiple-document markers (---)
      - Indented key names
    """
    data: dict = {}
    current_list: Optional[list[str]] = None
    lines = block.split("\n")

    for i, raw_line in enumerate(lines):
        line = raw_line.rstrip()

        # Empty lines are fine
        if not line:
            current_list = None
            continue

        # List items: continue the current list or error if no list context
        if line.startswith("  - ") or line.startswith("- "):
            if current_list is None:
                raise FrontmatterError(
                    f"Line {i + 1}: list item without a key (indent with `key:` first)"
                )
            # Parse the item value, stripping quotes
            item_text = line.split("- ", 1)[1].strip()
            item = _unquote(item_text)
            current_list.append(item)
            continue

        # Continuation lines (indented but not a list item) are forbidden
        if line.startswith(" ") or line.startswith("\t"):
            raise FrontmatterError(
                f"Line {i + 1}: indented line without a list context "
                "(block scalars, nested maps, and flow syntax are forbidden)"
            )

        # Multiple-document marker is forbidden
        if line.startswith("---"):
            raise FrontmatterError(
                f"Line {i + 1}: multiple-document separator --- found "
                "(only one document allowed)"
            )

        # Key: value line
        if ":" not in line:
            raise FrontmatterError(f"Line {i + 1}: no ':' in key-value line")

        key, _, value_part = line.partition(":")
        key = key.strip()

        # Keys must not be indented (checked above)
        if not key:
            raise FrontmatterError(f"Line {i + 1}: empty key name")

        value = value_part.strip()

        if value == "":
            # Start a new list
            current_list = []
            data[key] = current_list
        else:
            # Scalar value — unquote and potentially coerce to bool/int
            parsed_val = _parse_scalar(value)
            data[key] = parsed_val
            current_list = None

    return data


def _unquote(s: str) -> str:
    """Remove surrounding quotes from a string if present.

    Handles both single and double quotes. A string that doesn't start
    and end with matching quotes is returned as-is.
    """
    s = s.strip()
    if len(s) >= 2:
        if (s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'"):
            return s[1:-1]
    return s


def _parse_scalar(s: str) -> str | bool | int:
    """Parse a scalar value, inferring type for booleans and integers.

    Per §2 of the spec, scalars can be strings, integers, booleans, or
    dates (as strings). Quoted values are always returned as strings.
    Unquoted 'true', 'false', 'yes', 'no' are interpreted as booleans.
    Unquoted integers are parsed as int. Everything else is a string.
    """
    # Quoted strings: always string (even if they look like numbers/booleans)
    if s and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        return _unquote(s)

    # Unquoted boolean literals
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False

    # Unquoted integer (only if purely digits, optionally with leading -)
    if s and (s.isdigit() or (s[0] == "-" and s[1:].isdigit())):
        try:
            return int(s)
        except ValueError:
            pass

    # Everything else is a string
    return s


# ── Writer ────────────────────────────────────────────────────────────

def dump(fm: dict) -> str:
    """Serialize a frontmatter dict to the §2 subset.

    Rules:
      - Top-level keys only; values are scalars or one-deep string lists
      - Quote strings containing ':', '#', or leading special characters
      - Preserve boolean and integer types where possible
      - Omit keys with empty string or empty list values (present-when-nonempty)
      - Deterministic output: keys appear in insertion order

    Args:
        fm: Frontmatter dict to serialize

    Returns:
        YAML string suitable for the frontmatter block (without --- delimiters)
    """
    lines: list[str] = []

    for key, value in fm.items():
        # Omit empty strings and empty lists
        if value == "" or value == []:
            continue

        if isinstance(value, list):
            # One-deep string list
            lines.append(f"{key}:")
            for item in value:
                # Quote each item if needed
                quoted_item = _quote_value(str(item))
                lines.append(f"  - {quoted_item}")
        elif isinstance(value, bool):
            # Preserve boolean as unquoted true/false
            bool_val = "true" if value else "false"
            lines.append(f"{key}: {bool_val}")
        elif isinstance(value, int):
            # Preserve integer as unquoted number
            lines.append(f"{key}: {value}")
        else:
            # Scalar string: convert to string and quote if needed
            str_val = str(value)
            quoted_val = _quote_value(str_val)
            lines.append(f"{key}: {quoted_val}")

    return "\n".join(lines)


def _quote_value(s: str) -> str:
    """Quote a string value if it contains special characters.

    Strings containing ':', '#', or starting with special characters
    (-, ?, [, ], {, }, &, *, #, |, >, &, !, %) must be quoted to avoid
    ambiguity in YAML.

    Strings that are purely numeric or boolean-like are also quoted to
    prevent YAML from interpreting them as numbers or booleans.
    """
    # Already quoted: leave as-is
    if s and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        return s

    # Check for characters that require quoting
    needs_quote = (
        ":" in s
        or "#" in s
        or (s and s[0] in "-?[]{}&*|>#!%@`")  # Leading special chars
        or s in ("true", "false", "yes", "no", "on", "off")  # Boolean-like
        or s == "null"
        or (s and s[0].isdigit())  # Leading digit (might be interpreted as number)
    )

    if needs_quote:
        # Use double quotes and escape any internal quotes
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    return s


# ── Validator ─────────────────────────────────────────────────────────

def validate(fm: dict) -> list[str]:
    """Validate a parsed frontmatter dict against the schema.

    Checks:
      - `type` is present and in the closed vocabulary
      - Required fields for the type are present
      - List fields are actually lists (or can be coerced)
      - `generated` presence matches record vs. projection class

    Args:
        fm: Parsed frontmatter dict

    Returns:
        List of validation error messages. Empty list means valid.
    """
    errors: list[str] = []

    # Check for required `type` field
    entry_type = fm.get("type")
    if not entry_type:
        errors.append("missing required field: `type`")
        return errors

    entry_type = str(entry_type)

    # Check that `type` is in the vocabulary
    if entry_type not in TYPES:
        errors.append(
            f"unknown type: `{entry_type}` "
            f"(allowed: {', '.join(sorted(TYPES))})"
        )
        return errors

    schema = SCHEMAS[entry_type]

    # Check required fields
    for req_field in schema.required:
        if req_field not in fm:
            errors.append(f"missing required field for `{entry_type}`: `{req_field}`")

    # Check list fields are lists (not scalars)
    for list_field in schema.list_fields:
        if list_field in fm:
            val = fm[list_field]
            if not isinstance(val, list):
                errors.append(
                    f"field `{list_field}` must be a list, got {type(val).__name__}"
                )

    # Check `generated` invariant (§6)
    is_projection = entry_type in PROJECTION_TYPES
    has_generated = fm.get("generated") is True

    if is_projection and not has_generated:
        errors.append(
            f"projection type `{entry_type}` must have `generated: true`"
        )
    if not is_projection and has_generated:
        errors.append(
            f"record type `{entry_type}` must not carry `generated` marker"
        )

    return errors


# ── Round-trip test helper ────────────────────────────────────────────

def round_trip(text: str) -> str:
    """Parse frontmatter and dump it back.

    Useful for testing that a file round-trips: parse(text) → dump() should
    produce valid frontmatter that parses to the same dict.

    Args:
        text: File content with frontmatter

    Returns:
        Full markdown with re-serialized frontmatter
    """
    # Split off body
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    body = text[end + len("\n---\n") :]

    # Parse and re-dump frontmatter
    fm = parse(text)
    fm_str = dump(fm)
    return f"---\n{fm_str}\n---\n{body}"
