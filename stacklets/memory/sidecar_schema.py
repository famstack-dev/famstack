"""A card's sidecar against its schema, read without a JSON Schema library.

The sidecar, `<stem>.json` beside a card, says where each of the card's
sources came from and how the card was made (ADR-018 §3). Its precise
definition is `schemas/sidecar.json`, plain JSON Schema (draft 2020-12),
so a tool outside the stack reads the format the way the stack checks it.

The format check runs on the host, whose CLI is stdlib-only, as well as
in the curator, so the check here reads the schema with the standard
library, as `card_fields.py` reads the card schemas. It knows the keywords
the sidecar schema uses and no others: a keyword it does not know raises,
so the schema cannot grow a rule this check would silently skip.

    sidecar_problems({"card_id": "note-967b020f7e", "filed_by": "marge",
                      "sources": [{"role": "teacher", ...}], "made": {}})
    # [{"field": "sources[0].role",
    #   "message": "holds 'teacher', which is not one of the values it may hold",
    #   "allowed": ["original", "reply", "note"]}]
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .card_fields import SCHEMA_DIR

SCHEMA = SCHEMA_DIR.parent / "sidecar.json"

# Keywords that describe and never constrain.
_ANNOTATIONS = frozenset({"$schema", "$id", "$defs", "$comment", "title", "description",
                          "examples"})
_CHECKED = frozenset({"type", "required", "properties", "additionalProperties", "enum", "const",
                      "pattern", "minLength", "items", "$ref", "if", "then"})

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}
_KIND = {"object": "an object", "array": "a list", "string": "text", "boolean": "true or false"}


@lru_cache(maxsize=None)
def _schema(path: Path = SCHEMA) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sidecar_problems(sidecar: object, path: Path = SCHEMA) -> list[dict]:
    """What in `sidecar` the schema does not allow: one entry per value, [] for none."""
    root = _schema(path)
    return _check(sidecar, root, "", root)


# ── One schema, one value ─────────────────────────────────────────────────

def _check(value: object, schema: dict, where: str, root: dict) -> list[dict]:
    """What is wrong with `value` at `where` (`sources[0].role`) under `schema`."""
    if unknown := set(schema) - _ANNOTATIONS - _CHECKED:
        raise ValueError(f"the sidecar check does not know the keyword(s) {sorted(unknown)}")
    if "$ref" in schema:
        return _check(value, _resolved(schema["$ref"], root), where, root)
    if "type" in schema and not _is(value, schema["type"]):
        return [_problem(where, f"must be {_KIND[schema['type']]}")]
    if "enum" in schema and value not in schema["enum"]:
        return [_problem(where, f"holds {value!r}, which is not one of the values it may hold",
                         list(schema["enum"]))]
    if "const" in schema and value != schema["const"]:
        return [_problem(where, f"must be {json.dumps(schema['const'])}", [schema["const"]])]
    if isinstance(value, str):
        return _text_problems(value, schema, where)
    if isinstance(value, list) and "items" in schema:
        return [p for i, item in enumerate(value)
                for p in _check(item, schema["items"], f"{where}[{i}]", root)]
    if isinstance(value, dict):
        return _object_problems(value, schema, where, root)
    return []


def _text_problems(value: str, schema: dict, where: str) -> list[dict]:
    if len(value) < schema.get("minLength", 0):
        return [_problem(where, "must not be empty")]
    if "pattern" in schema and not re.search(schema["pattern"], value):
        form = [schema["description"]] if schema.get("description") else []
        return [_problem(where, f"holds {value!r}, which does not have the form it needs", form)]
    return []


def _object_problems(value: dict, schema: dict, where: str, root: dict) -> list[dict]:
    properties = schema.get("properties", {})
    found = [_problem(_at(where, name), "is missing")
             for name in schema.get("required", []) if name not in value]
    for name, sub in properties.items():
        if name in value:
            found += _check(value[name], sub, _at(where, name), root)
    if schema.get("additionalProperties") is False:
        found += [_problem(_at(where, name), "is not a field it may have", list(properties))
                  for name in value if name not in properties]
    if "if" in schema and not _check(value, schema["if"], where, root):
        then = schema.get("then", {})
        if _check(value, then, where, root):
            found.append(_problem(where or "sidecar", then.get("description", "")))
    return found


# ── Helpers ───────────────────────────────────────────────────────────────

def _resolved(ref: str, root: dict) -> dict:
    """A reference inside the schema itself, `#/$defs/source`."""
    if not ref.startswith("#/"):
        raise ValueError(f"the sidecar check reads only references inside its schema, not {ref}")
    target: object = root
    for part in ref[2:].split("/"):
        target = target[part] if isinstance(target, dict) else {}
    return target if isinstance(target, dict) else {}


def _is(value: object, json_type: str) -> bool:
    return isinstance(value, _TYPES[json_type])


def _at(where: str, name: str) -> str:
    return f"{where}.{name}" if where else name


def _problem(field: str, message: str, allowed: list | None = None) -> dict:
    return {"field": field, "message": message, "allowed": allowed or []}
