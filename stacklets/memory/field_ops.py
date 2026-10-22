"""A person's field edits, as operations a rebuild applies again (ADR-018 §7).

A person changes a card's fields (`stack memory card edit`, the card
browser) and the change is their commit. A rebuild interprets the card
again from its sources, which would write over those changes; so each
one is read from the card's history as operations (`ops_between`) and
applied again after the model, in order (`apply_ops`):

- a list field changes item by item, `add` and `remove`, so the model's
  other suggestions still come in: Marge took `camping` off, the model
  suggests `camping` and `tent`, the card gets `tent`;
- any other field is `set` or `unset`.

An operation that no longer fits the card's schema (a person a later
change removed from the people, a field the type dropped) is left out and
named, never applied silently:

    ops = ops_between({"tags": ["camping"]}, {"tags": ["tent"]}, person="marge", at="…")
    # [FieldOp('marge', '…', 'add', 'tags', 'tent'), FieldOp('marge', '…', 'remove', 'tags', 'camping')]
    text, left_out = apply_ops(card, ops, values=FieldValues(person_ids={"marge"}, ontology=ontology),
                               path=path, bucket="family")

The check reads the card schemas (`schemas/cards/`) with the standard
library, as `card_fields` does, since the host CLI has no JSON Schema
library: the field's place in the type, `readOnly`, `required`, its
type, `minLength`, `pattern`, and the values the instance decides
(people, topics, tags). The card write endpoint checks a person's edit
with the full validator before it is ever committed; this check is about
whether it still fits the card the rebuild made.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

try:  # the host CLI, which has the memory stacklet on its path
    from card_fields import BASE, SCHEMA_DIR
except ImportError:  # the bot runner, which reads the memory stacklet as `memory`
    from memory.card_fields import BASE, SCHEMA_DIR

from stack.card_links import with_about_from_fields
from stack.frontmatter import FrontmatterError, parse, with_field
from stack.ontology import Ontology
from stack.tags import is_tag

# The fields that name the card and never change (`readOnly` in the
# schema), and the ones that say where it came from, which live in the
# sidecar of a note (ADR-018 §3). Neither is a person's edit.
_IDENTITY = frozenset({"type", "card_id"})
_HEADING = "# "


@dataclass(frozen=True)
class FieldOp:
    """One change of one field by `person` at `at`: `add` or `remove` an
    item of a list, `set` or `unset` a value."""

    person: str
    at: str
    op: str
    field: str
    value: object = None

    def shown(self) -> str:
        """`marge: add tags=tent`."""
        value = "" if self.op == "unset" else f"={self.value}"
        return f"{self.person}: {self.op} {self.field}{value}"

    def to_json(self) -> dict:
        return {"person": self.person, "at": self.at, "op": self.op, "field": self.field,
                "value": self.value}


def op_from_json(record: dict) -> FieldOp:
    return FieldOp(str(record.get("person") or ""), str(record.get("at") or ""),
                   str(record.get("op") or ""), str(record.get("field") or ""), record.get("value"))


@dataclass(frozen=True)
class FieldValues:
    """What the instance decides a field may hold: its people and its ontology."""

    person_ids: frozenset[str] = frozenset()
    ontology: Ontology = field(default_factory=Ontology)


# ── Reading the operations ────────────────────────────────────────────


def ops_between(before: Mapping[str, object], after: Mapping[str, object], *,
                person: str, at: str, skip: frozenset[str] = frozenset()) -> list[FieldOp]:
    """The operations that take the fields `before` to `after`, in field order.

    `skip` names fields that are not a person's to edit (where the card
    came from); the card's identity is never an operation.
    """
    ops: list[FieldOp] = []
    for name in [*after, *(k for k in before if k not in after)]:
        if name in _IDENTITY or name in skip:
            continue
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        if isinstance(old, list) or isinstance(new, list):
            old_items, new_items = _items(old), _items(new)
            ops += [FieldOp(person, at, "add", name, v) for v in new_items if v not in old_items]
            ops += [FieldOp(person, at, "remove", name, v) for v in old_items if v not in new_items]
        elif name not in after:
            ops.append(FieldOp(person, at, "unset", name))
        else:
            ops.append(FieldOp(person, at, "set", name, new))
    return ops


def _items(value: object) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


# ── Applying them ─────────────────────────────────────────────────────


def apply_ops(text: str, ops: Sequence[FieldOp], *, values: FieldValues,
              path: str, bucket: str) -> tuple[str, list[str]]:
    """`text` with `ops` applied in order, and the ones left out with why.

    The changed fields are written into the frontmatter where they are
    (`with_field`), the title heading follows a changed title, and the
    meta block's people links follow `persons`, as a person's edit does
    them (`card_edit`).
    """
    if not ops:
        return text, []
    try:
        before = parse(text)
    except FrontmatterError:
        return text, [f"{op.shown()}: the card's frontmatter does not parse" for op in ops]
    fields = dict(before)
    left_out = []
    for op in ops:
        why = op_problem(op, str(fields.get("type") or ""), fields, values)
        if why:
            left_out.append(f"{op.shown()}: {why}")
        else:
            _apply(fields, op)
    for name in [k for k in before if k not in fields]:
        text = with_field(text, name, "")
    for name, value in fields.items():
        if before.get(name) != value:
            text = with_field(text, name, value)
    old_title, new_title = str(before.get("title") or ""), str(fields.get("title") or "")
    if new_title and new_title != old_title:
        text = text.replace(f"\n{_HEADING}{old_title}\n", f"\n{_HEADING}{new_title}\n", 1)
    return with_about_from_fields(text, path, bucket=bucket), left_out


def _apply(fields: dict, op: FieldOp) -> None:
    if op.op == "add":
        items = _items(fields.get(op.field))
        fields[op.field] = items if op.value in items else [*items, op.value]
    elif op.op == "remove":
        items = [v for v in _items(fields.get(op.field)) if v != op.value]
        if items:
            fields[op.field] = items
        else:
            fields.pop(op.field, None)
    elif op.op == "set":
        fields[op.field] = op.value
    else:
        fields.pop(op.field, None)


# ── Whether an operation fits ─────────────────────────────────────────


def op_problem(op: FieldOp, card_type: str, fields: Mapping[str, object],
               values: FieldValues) -> str:
    """Why `op` does not fit a card of `card_type` holding `fields`; "" when it does."""
    spec = _properties(card_type).get(op.field)
    if spec is None:
        return f"a {card_type or 'card'} has no field `{op.field}`"
    if spec.get("readOnly"):
        return f"`{op.field}` is read-only"
    if op.op == "unset":
        return f"a {card_type} needs `{op.field}`" if op.field in _required(card_type) else ""
    if op.op in ("add", "remove"):
        if spec.get("type") != "array":
            return f"`{op.field}` is no list"
        if op.op == "remove":
            return ""
        return _value_problem(spec.get("items") or {}, op.value, values)
    return _value_problem(spec, op.value, values)


def _value_problem(spec: Mapping[str, object], value: object, values: FieldValues) -> str:
    ref = str(spec.get("$ref") or "")
    if ref.endswith("/person_id"):
        return "" if value in values.person_ids else f"{value} is no declared person"
    if ref.endswith("/topic_id"):
        return "" if value in values.ontology.topics else f"{value} is no topic"
    if ref.endswith("/tag"):
        return "" if isinstance(value, str) and is_tag(value, values.ontology) \
            else f"{value} is no topic and no keyword"
    for check in _CHECKS:
        if why := check(spec, value):
            return why
    if spec.get("type") == "array" and isinstance(value, list):
        item = spec.get("items")
        return next((w for v in value if (w := _value_problem(item, v, values))), "") \
            if isinstance(item, Mapping) else ""
    return ""


def _type_problem(spec: Mapping[str, object], value: object) -> str:
    expected = {"string": str, "array": list, "integer": int, "boolean": bool, "object": dict}
    kind = expected.get(str(spec.get("type") or ""))
    return f"{value!r} is no {spec['type']}" if kind and not isinstance(value, kind) else ""


def _length_problem(spec: Mapping[str, object], value: object) -> str:
    least = spec.get("minLength")
    return f"{value!r} is too short" if isinstance(least, int) and isinstance(value, str) \
        and len(value) < least else ""


def _pattern_problem(spec: Mapping[str, object], value: object) -> str:
    pattern = spec.get("pattern")
    return f"{value!r} is not written as {pattern}" if isinstance(pattern, str) \
        and isinstance(value, str) and not re.search(pattern, value) else ""


def _enum_problem(spec: Mapping[str, object], value: object) -> str:
    allowed = spec.get("enum") or ([spec["const"]] if "const" in spec else None)
    return f"{value!r} is not one of {allowed}" if isinstance(allowed, list) \
        and value not in allowed else ""


_CHECKS: tuple[Callable[[Mapping[str, object], object], str], ...] = (
    _type_problem, _length_problem, _pattern_problem, _enum_problem)


@lru_cache(maxsize=None)
def _schema(name: str) -> dict:
    file = Path(SCHEMA_DIR) / f"{name}.json"
    return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}


def _properties(card_type: str) -> dict[str, dict]:
    """The fields a card of `card_type` may hold, with their schema; {} for no such type."""
    if card_type == BASE or not _schema(card_type):
        return {}
    return {**_schema(BASE).get("properties", {}), **_schema(card_type).get("properties", {})}


def _required(card_type: str) -> set[str]:
    return {*_schema(BASE).get("required", []), *_schema(card_type).get("required", [])}
