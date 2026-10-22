"""Vault frontmatter parser and writer.

One shared parser for both host CLI and containers, implementing the
strict YAML subset of ADR-017 §8 (described in docs/memory-format.md).
No external dependencies — stdlib only, so the host CLI can use it
without pulling in PyYAML.

Two contracts:
  - Parser: reads the subset into a dict, raises on violations
  - Writer: serializes a dict to the subset with proper quoting
"""

from __future__ import annotations

from typing import Optional


# ── Exceptions ────────────────────────────────────────────────────────

class FrontmatterError(ValueError):
    """Raised when frontmatter violates the strict §2 subset."""
    pass


# ── Parser ────────────────────────────────────────────────────────────

def parse(text: str) -> dict:
    """Parse frontmatter from a markdown file.

    The §2 subset consists of:
      - Top-level keys only (no nesting)
      - Scalar values: string, integer, boolean, or date-as-string
      - One-deep string lists (key: followed by indented `- item` lines),
        also read from a one-line flow list (`key: [a, b]`)

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
      - Nested maps, lists of maps, flow maps ({})
      - Block scalars (|, >), anchors/aliases, tags
      - Unquoted values YAML reads differently (`: `, ` #`)
      - Multiple-document markers (---)
      - Indented key names
    """
    data: dict = {}
    current_list: Optional[list[str]] = None
    lines = block.split("\n")

    for i, raw_line in enumerate(lines):
        line = raw_line.rstrip()
        line_no = i + 1

        # Empty lines are fine
        if not line:
            current_list = None
            continue

        # List items: continue the current list or error if no list context
        if line.startswith("  - ") or line.startswith("- "):
            if current_list is None:
                raise FrontmatterError(
                    f"Line {line_no}: list item without a key (indent with `key:` first)"
                )
            item_text = line.split("- ", 1)[1].strip()
            current_list.append(_parse_item(item_text, line_no))
            continue

        # Continuation lines (indented but not a list item) are forbidden
        if line.startswith(" ") or line.startswith("\t"):
            raise FrontmatterError(
                f"Line {line_no}: indented line without a list context "
                "(block scalars and nested maps are forbidden)"
            )

        # Multiple-document marker is forbidden
        if line.startswith("---"):
            raise FrontmatterError(
                f"Line {line_no}: multiple-document separator --- found "
                "(only one document allowed)"
            )

        # Key: value line
        if ":" not in line:
            raise FrontmatterError(f"Line {line_no}: no ':' in key-value line")

        key, _, value_part = line.partition(":")
        key = key.strip()

        # Keys must not be indented (checked above)
        if not key:
            raise FrontmatterError(f"Line {line_no}: empty key name")

        value = value_part.strip()

        if value == "":
            # Start a new list
            current_list = []
            data[key] = current_list
        elif value.startswith("["):
            data[key] = _parse_flow_list(value, line_no)
            current_list = None
        else:
            # Scalar value — unquote and potentially coerce to bool/int
            data[key] = _parse_scalar(value, line_no)
            current_list = None

    return data


# ── Values ────────────────────────────────────────────────────────────
#
# The subset is read by this parser and, for the wiki, by Quartz's YAML
# parser. An unquoted value that YAML reads as structure, or reads with
# a different meaning, is refused here, so a value both parsers accept
# is a value both read the same way.

# Characters that open YAML syntax when they start an unquoted value:
# flow maps and lists, anchors, aliases, tags, block scalars, comments,
# directives and reserved characters.
_LEADING_SYNTAX = "[]{},&*!|>#%@`"

# `-`, `?` and `:` are syntax only when a space or the line end follows.
_LEADING_SYNTAX_BEFORE_SPACE = "-?:"

# The escapes a double-quoted value may carry. `dump` writes the first
# two; the rest are YAML's own that a hand edit may use.
_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "/": "/"}


def _parse_flow_list(value: str, line_no: int) -> list[str]:
    """A one-line flow list of scalars (`[a, "b, c"]`) as a list of strings.

    Read so that a hand edit like `tags: [a, b]` or `tags: []` means what
    it says. `dump` still writes block lists; nesting stays forbidden.
    """
    if not value.endswith("]"):
        raise FrontmatterError(
            f"Line {line_no}: a flow list must close with `]` on the same line"
        )
    inner = value[1:-1].strip()
    if not inner:
        return []
    return [_parse_item(part.strip(), line_no) for part in _split_flow(inner, line_no)]


def _split_flow(inner: str, line_no: int) -> list[str]:
    """Split the inside of a flow list on the commas outside quotes."""
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    escaped = False
    for c in inner:
        if escaped:
            escaped = False
        elif quote == '"' and c == "\\":
            escaped = True
        elif quote and c == quote:
            quote = ""
        elif not quote and c in "\"'":
            quote = c
        elif not quote and c == ",":
            parts.append("".join(current))
            current = []
            continue
        current.append(c)
    parts.append("".join(current))
    if any(not part.strip() for part in parts):
        raise FrontmatterError(f"Line {line_no}: empty item in a flow list")
    return parts


def _parse_item(text: str, line_no: int) -> str:
    """A list item: always a string, quoted or plain, never a nested list or map."""
    if text[:1] in "\"'":
        return _parse_quoted(text, line_no)
    if any(c in text for c in "[]{}"):
        raise FrontmatterError(
            f"Line {line_no}: list items must be plain values, not nested lists or maps"
        )
    _check_plain(text, line_no)
    return text


def _parse_quoted(s: str, line_no: int) -> str:
    """The text inside a single- or double-quoted value, escapes resolved.

    YAML's rules: `''` is a single quote inside single quotes, and a
    backslash escapes inside double quotes. Text after the closing quote
    is refused, as YAML refuses it.
    """
    quote = s[0]
    out: list[str] = []
    i = 1
    while i < len(s):
        c = s[i]
        if quote == '"' and c == "\\":
            escape = s[i + 1:i + 2]
            if escape not in _ESCAPES:
                raise FrontmatterError(
                    f"Line {line_no}: unknown escape `\\{escape}` in a quoted value"
                )
            out.append(_ESCAPES[escape])
            i += 2
            continue
        if c == quote:
            if quote == "'" and s[i + 1:i + 2] == "'":
                out.append("'")
                i += 2
                continue
            if i != len(s) - 1:
                raise FrontmatterError(
                    f"Line {line_no}: text after the closing quote of a value"
                )
            return "".join(out)
        out.append(c)
        i += 1
    raise FrontmatterError(f"Line {line_no}: quoted value is never closed")


def _check_plain(s: str, line_no: int) -> None:
    """Refuse an unquoted value that YAML would read as something else."""
    first, second = s[0], s[1:2]
    if first in _LEADING_SYNTAX or (
        first in _LEADING_SYNTAX_BEFORE_SPACE and second in ("", " ", "\t")
    ):
        raise FrontmatterError(
            f"Line {line_no}: a value starting with `{first}` must be quoted"
        )
    if ": " in s or s.endswith(":"):
        raise FrontmatterError(
            f"Line {line_no}: a value containing `: ` must be quoted"
        )
    if " #" in s or "\t#" in s:
        raise FrontmatterError(
            f"Line {line_no}: a value containing ` #` must be quoted "
            "(YAML reads the rest as a comment)"
        )


def _parse_scalar(s: str, line_no: int) -> str | bool | int:
    """Parse a scalar value, inferring type for booleans and integers.

    Per §2 of the spec, scalars can be strings, integers, booleans, or
    dates (as strings). Quoted values are always returned as strings.
    Unquoted 'true' and 'false' are interpreted as booleans.
    Unquoted integers are parsed as int. Everything else is a string.
    """
    # Quoted strings: always string (even if they look like numbers/booleans)
    if s[0] in "\"'":
        return _parse_quoted(s, line_no)

    _check_plain(s, line_no)

    # Unquoted boolean literals
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False

    # Unquoted integer (only if purely digits, optionally with leading -)
    if s.isdigit() or (s[0] == "-" and s[1:].isdigit()):
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


def with_field(text: str, key: str, value) -> str:
    """`text` with the frontmatter field `key` set to `value`, every other byte as it was.

    The field's lines (its `key:` line and the list items under it) are
    replaced where they are; a field the file lacks is added last in the
    frontmatter; an empty value removes the field, since an optional
    field with no value is left out. A file without frontmatter comes
    back as it is.

        >>> with_field("---\\ntype: note\\npersons:\\n  - Homer\\n---\\n\\nBody\\n", "persons", ["homer"])
        '---\\ntype: note\\npersons:\\n  - homer\\n---\\n\\nBody\\n'
    """
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 3)
    if end < 0:
        return text
    lines = text[4:end].split("\n")
    rendered = dump({key: value})
    new = rendered.split("\n") if rendered else []
    start = next((i for i, line in enumerate(lines)
                  if not line[:1].isspace() and line.partition(":")[0].strip() == key), None)
    if start is None:
        if not new:
            return text
        lines += new
    else:
        stop = start + 1
        while stop < len(lines) and lines[stop].lstrip().startswith("- "):
            stop += 1
        lines[start:stop] = new
    return "---\n" + "\n".join(lines) + text[end:]


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
