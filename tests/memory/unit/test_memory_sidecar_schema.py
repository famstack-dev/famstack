"""The sidecar schema means what JSON Schema says it means.

`schemas/sidecar.json` is the definition a reader outside the stack works
from, with any JSON Schema validator. The stack checks sidecars with a
reader of its own (`sidecar_schema.py`), because the host CLI is
stdlib-only. Pinned here: the schema is valid draft 2020-12, and the
reference validator (`jsonschema`) and the stack's reader accept and
refuse the same sidecars. What the format check tells a person is the
surface test's (`test_memory_sidecar_format.py`).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

_REPO = Path(__file__).resolve().parents[3]

from stacklets.memory.sidecar_schema import SCHEMA, sidecar_problems  # noqa: E402
from tests.memory.unit.simpsons_cards import SIDECARS  # noqa: E402

REFERENCE = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
ART = json.loads(SIDECARS["marge/notes/2026/10/art-supplies.json"])
HASH = "sha256:" + "4168fe9973e0031ca1a1b8c445d653bc3f7c56a985eea2093c070ccc5cd8f008"


def _with(change) -> dict:
    sidecar = copy.deepcopy(ART)
    change(sidecar)
    return sidecar


SIDECARS_TO_JUDGE = {
    "as written": ART,
    "a voice source": _with(lambda s: s["sources"][0].update(
        kind="voice", media=HASH, text={"form": "transcript", "tool": "whisper", "made": "x"})),
    "an unknown time, reconstructed": _with(lambda s: s["sources"][0].update(
        time="unknown", reconstructed=True)),
    "an unknown role": _with(lambda s: s["sources"][0].update(role="teacher")),
    "an unknown channel": _with(lambda s: s["sources"][0].update(channel="fax")),
    "an unknown kind": _with(lambda s: s["sources"][0].update(kind="telegram")),
    "a time in words": _with(lambda s: s["sources"][0].update(time="Thursday 4pm")),
    "a time without its offset": _with(lambda s: s["sources"][0].update(time="2026-10-13T21:12")),
    "an unknown time, not reconstructed": _with(lambda s: s["sources"][0].update(time="unknown")),
    "media as a path": _with(lambda s: s["sources"][0].update(media="/media/2026/10/x.m4a")),
    "a key no schema names": _with(lambda s: s.update(mood="cheerful")),
    "a source without its id": _with(lambda s: s["sources"][0].pop("id")),
    "made without its object": _with(lambda s: s.update(made="yesterday")),
}


def test_the_schema_is_valid_json_schema():
    Draft202012Validator.check_schema(REFERENCE.schema)


@pytest.mark.parametrize("name", SIDECARS_TO_JUDGE)
def test_the_stack_and_the_reference_validator_agree(name):
    sidecar = SIDECARS_TO_JUDGE[name]
    assert (sidecar_problems(sidecar) == []) == REFERENCE.is_valid(sidecar)
