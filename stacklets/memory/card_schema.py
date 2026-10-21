"""The card types as JSON Schema, and the check a card's fields pass (ADR-017 §5, §6).

Each card type is one file under `schemas/cards/`, `<type>.json`, and every
one of them starts from `base.json`, the fields all cards share. A type's
file adds its own fields and never gives a base field another meaning.
They are plain JSON Schema (draft 2020-12), so any tool that reads JSON
Schema reads the format the way the memory stacklet checks it.

**What depends on the instance** is a reference into one resource,
`urn:famstack:vault`, which no file holds. The validator builds it from the
memory repo before each check: the ids of the declared people, the topic
ids of the ontology, the tag rule. A schema says

    "persons": {"type": "array", "items": {"$ref": "urn:famstack:vault#/$defs/person_id"}}

and the resource answers `{"enum": ["bart", "homer", ...]}`. A `$ref` is
the marker because it is JSON Schema's own way to say "defined elsewhere":
no keyword of ours, no schema rewritten before use, and a tool that has
no such resource fails to resolve the reference instead of passing every
value unchecked.

**A tag's keyword half is a format.** A tag is a topic id or a keyword
(ADR-017 §4): lowercase words joined by hyphens, at least one letter, and
no spelling of a topic's label. That is `stack.tags.is_tag`, a rule over
Unicode letter classes and the ontology, which a `pattern` describes only
approximately. The resource gives it as `"format": "keyword"`, asserted
here with that rule; a tool without the checker reads the format as a note.

    schemas = CardSchemas.load()
    values = VaultValues(person_ids=["homer"], ontology=ontology)
    schemas.problems({"type": "note", "title": "Donuts", ...}, values)
    # [] when the fields are valid, else one entry per field:
    # [{"field": "persons", "message": "...", "allowed": ["homer"]}]
    schemas.read_only("note")    # the fields no change may touch: type, card_id, ...

A field neither the type's schema nor the base names is refused
(`unevaluatedProperties`), named, with the fields the type has
(`card_fields.py`, which the format check reads too).

Runs in the curator's container, which carries `jsonschema`. The host CLI
is stdlib-only and never imports this: it asks the card write endpoint.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from card_fields import BASE, SCHEMA_DIR, card_fields, unknown_fields

from stack.ontology import Ontology
from stack.tags import is_tag

VAULT = "urn:famstack:vault"
_CARD = "urn:famstack:card:"

# What a reader is told a keyword is, when a tag is none.
KEYWORD = "a keyword: lowercase words joined by hyphens, with at least one letter"


@dataclass(frozen=True)
class VaultValues:
    """The values a card's fields are checked against that one instance decides."""

    person_ids: Iterable[str] = ()
    ontology: Ontology = field(default_factory=Ontology)

    def resource(self) -> dict:
        """`urn:famstack:vault`, the schema the card schemas refer to for these values."""
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": VAULT,
            "$defs": {
                "person_id": {"enum": sorted(set(self.person_ids))},
                "topic_id": {"enum": sorted(self.ontology.topics)},
                "tag": {"anyOf": [{"$ref": "#/$defs/topic_id"},
                                  {"type": "string", "format": "keyword", "description": KEYWORD}]},
            },
        }

    def formats(self) -> FormatChecker:
        checker = FormatChecker(formats=())
        checker.checks("keyword")(lambda tag: not isinstance(tag, str) or is_tag(tag, self.ontology))
        return checker


@dataclass(frozen=True)
class CardSchemas:
    """Every card type's schema, read from `schemas/cards/`.

        schemas = CardSchemas.load()
        schemas.types               # ('bookmark', 'correspondent', 'diary', ...)
    """

    by_type: Mapping[str, dict]

    @classmethod
    def load(cls, folder: Path = SCHEMA_DIR) -> "CardSchemas":
        return cls({f.stem: json.loads(f.read_text(encoding="utf-8"))
                    for f in sorted(folder.glob("*.json"))})

    @property
    def types(self) -> tuple[str, ...]:
        return tuple(t for t in self.by_type if t != BASE)

    def problems(self, fields: Mapping[str, object], values: VaultValues) -> list[dict]:
        """What is wrong with a card's `fields`: one entry per field, [] for none.

        The card's `type` picks the schema; a type no schema has is the
        one problem, with the types there are.
        """
        card_type = str(fields.get("type") or "")
        if card_type not in self.types:
            return [_problem("type", f"`{card_type}` is no card type" if card_type
                             else "a card needs a `type`", list(self.types))]
        validator = Draft202012Validator(self.by_type[card_type], registry=self._registry(values),
                                         format_checker=values.formats())
        found: dict[str, dict] = {}
        instance: Any = dict(fields)
        for error in sorted(validator.iter_errors(instance), key=lambda e: e.path):
            for problem in _problems_of(error, fields, card_type):
                found.setdefault(problem["field"], problem)
        return list(found.values())

    def read_only(self, card_type: str) -> set[str]:
        """The fields of `card_type` a change keeps as they are (`readOnly`)."""
        names: set[str] = set()
        for schema in (self.by_type.get(BASE, {}), self.by_type.get(card_type, {})):
            names |= {name for name, spec in schema.get("properties", {}).items()
                      if isinstance(spec, dict) and spec.get("readOnly")}
        return names

    def _registry(self, values: VaultValues) -> Registry:
        resources = [(f"{_CARD}{name}", schema) for name, schema in self.by_type.items()]
        resources.append((VAULT, values.resource()))
        return Registry().with_resources(
            (uri, Resource.from_contents(schema, default_specification=DRAFT202012))
            for uri, schema in resources)


# ── One entry per field ───────────────────────────────────────────────────

def _problems_of(error: ValidationError, fields: Mapping[str, object], card_type: str) -> list[dict]:
    """The entries `error` gives: the field it is about, what is wrong, and
    what the field may hold."""
    if error.validator == "unevaluatedProperties":
        allowed = sorted(card_fields(card_type))
        return [_problem(name, f"{_a(card_type)} card has no field `{name}`", allowed)
                for name in unknown_fields(fields)]
    if error.validator == "required":
        missing = [name for name in _value(error) if name not in fields]
        return [_problem(name, f"{_a(card_type)} card needs `{name}`", []) for name in missing]
    if not error.absolute_path:
        return [_problem("fields", error.message, [])]
    name = str(error.absolute_path[0])
    value = fields.get(name)
    where = f"`{name}`" if len(error.absolute_path) == 1 else f"`{name}` holds {error.instance!r}, which"
    return [_problem(name, f"{where} {_what_is_wrong(error, value)}", _allowed(error))]


def _what_is_wrong(error: ValidationError, value: object) -> str:
    if error.validator in ("enum", "const") or (error.validator == "anyOf" and _listed(error)):
        return "is not one of the values it may hold"
    if error.validator == "type":
        return f"must be {_kind_of(_value(error))}"
    if error.validator == "minLength":
        return "must not be empty"
    if error.validator in ("pattern", "anyOf"):
        return "does not have the form it needs"
    return error.message


def _allowed(error: ValidationError) -> list:
    """The values the field may hold, or a sentence on its form where the
    schema describes it instead of listing them."""
    if listed := _listed(error):
        return listed
    if error.validator in ("pattern", "anyOf") and _schema(error).get("description"):
        return [_schema(error)["description"]]
    return []


def _listed(error: ValidationError) -> list:
    """The values the schema lists for the field: an enum, a const, or the
    enums of the shapes it may take, with a keyword's rule among them."""
    if error.validator == "enum":
        return list(_value(error))
    if error.validator == "const":
        return [_value(error)]
    if error.validator != "anyOf":
        return []
    allowed: list = []
    for branch in error.context or []:
        allowed += [a for a in _listed(branch) if a not in allowed]
        if branch.validator == "format" and KEYWORD not in allowed:
            allowed.append(KEYWORD)
    return allowed


def _value(error: ValidationError) -> Any:
    """What the failing keyword said (`["todo", ...]` for an enum)."""
    return error.validator_value


def _schema(error: ValidationError) -> dict:
    """The (sub)schema that failed, as a mapping."""
    return error.schema if isinstance(error.schema, dict) else {}


def _kind_of(json_type: object) -> str:
    names = {"string": "text", "integer": "a whole number", "boolean": "true or false",
             "array": "a list", "object": "an object"}
    types = json_type if isinstance(json_type, list) else [json_type]
    return " or ".join(names.get(str(t), str(t)) for t in types)


def _a(card_type: str) -> str:
    return f"an {card_type}" if card_type[:1] in "aeiou" else f"a {card_type}"


def _problem(name: str, message: str, allowed: list) -> dict:
    return {"field": name, "message": message, "allowed": allowed}
