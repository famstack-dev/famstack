"""`stack doctor` on the key of famstack's oMLX.

Earlier releases installed famstack's oMLX with the key `local`, which
every device at home can send: on port 42060 in port mode, at
`openai.<domain>` in domain mode. An update keeps it, because apps on
other computers send it. `stack doctor` names it on every run while
famstack's oMLX holds it, with the command that gives it a key of its own.

The settings file of famstack's oMLX owns the key, and `[ai] openai_key`
is the stack's copy. When the two differ (a write stopped between them),
every stacklet that uses the AI gets 401, and doctor says so as an error
of its own, with the command that brings them together. It prints
neither key.

Driven through `./stack doctor --json` in this process, with Docker
answering that nothing runs. The settings file is written where
famstack's oMLX keeps it, `<data_dir>/ai/omlx/settings.json`.
"""

from __future__ import annotations

import json

import pytest

from .conftest import StackCLI, write_instance

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

GENERATED = "7fk2-pq9w-m3xd-h8ra"


@pytest.fixture
def doctor(tmp_path, monkeypatch, as_stack_runs_it):
    """`stack doctor --json` with the ai stacklet set up, this `[ai]`, and
    famstack's oMLX's settings holding `omlx_key` (None: no file yet).
    The findings and doctor's whole output."""
    def run(ai: str, omlx_key: str | None) -> tuple[list[dict], str]:
        instance = write_instance(tmp_path, extra=ai)
        (instance / ".stack" / "ai.setup-done").touch()
        if omlx_key is not None:
            settings = tmp_path / "data" / "ai" / "omlx" / "settings.json"
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(json.dumps({"auth": {"api_key": omlx_key}}))
        ran = StackCLI(instance, monkeypatch)("doctor", "--json")
        return ran.answer["findings"], ran.out + ran.err
    return run


def managed(key: str) -> str:
    return (f'[ai]\nprovider = "managed"\nopenai_url = "http://localhost:42060/v1"\n'
            f'openai_key = "{key}"\nspeech = false\n')


def _shared(findings: list[dict]) -> list[dict]:
    return [f for f in findings if f["fix"] == "stack ai key new"]


def _apart(findings: list[dict]) -> list[dict]:
    return [f for f in findings if f["fix"] == "stack up ai" and "key" in f["title"]]


def test_the_shared_key_is_named_once_with_both_ways_in(doctor):
    findings, _ = doctor(managed("local"), omlx_key="local")

    [found] = _shared(findings)
    assert found["level"] == "warn"
    assert "local" in found["title"]
    assert "42060" in found["detail"] and "openai.<domain>" in found["detail"]
    assert _apart(findings) == []


def test_a_generated_key_in_both_is_not_named(doctor):
    findings, _ = doctor(managed(GENERATED), omlx_key=GENERATED)

    assert _shared(findings) == [] and _apart(findings) == []


def test_the_owner_decides_not_the_copy(doctor):
    """Given famstack's oMLX holds a generated key and `[ai]` still says
    `local` (a new key whose write into `[ai]` stopped), then doctor names
    no shared key: oMLX does not take `local`. It names the keys that
    differ, as an error, and prints neither key."""
    findings, output = doctor(managed("local"), omlx_key=GENERATED)

    assert _shared(findings) == []
    [found] = _apart(findings)
    assert found["level"] == "error"
    assert "stack ai key new" in found["detail"]
    assert GENERATED not in output
    assert '"local"' not in output and "'local'" not in output


def test_the_shared_key_in_the_file_is_named_whatever_ai_says(doctor):
    findings, output = doctor(managed(GENERATED), omlx_key="local")

    assert len(_shared(findings)) == 1
    assert len(_apart(findings)) == 1
    assert GENERATED not in output


def test_before_the_first_start_nothing_is_named(doctor):
    """No settings file yet: the next start writes the key into both."""
    findings, _ = doctor(managed("local"), omlx_key=None)

    assert _shared(findings) == [] and _apart(findings) == []


def test_an_ai_server_the_stack_does_not_manage_is_not_named(doctor):
    """`local` is also the placeholder for a server that takes any key."""
    ai = ('[ai]\nprovider = "external"\nopenai_url = "http://localhost:8000/v1"\n'
          'openai_key = "local"\nspeech = false\n')

    findings, _ = doctor(ai, omlx_key="local")

    assert _shared(findings) == [] and _apart(findings) == []
