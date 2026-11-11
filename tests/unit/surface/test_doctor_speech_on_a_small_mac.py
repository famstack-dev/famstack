"""`stack doctor` on a small Mac that keeps speech on without having chosen it.

A fresh install on a Mac under 24 GB stores `[ai] speech = false`. An
instance installed before that has no `voice` key, and an update changes
nothing the family uses, so its speech stays on. `stack doctor` says what
speech costs there and names the line that turns it off. A choice in
`stack.toml`, either way, is the admin's and is not reported.

Driven through `./stack doctor --json` in this process, with Docker
answering that nothing runs and a `sysctl` first on PATH that reports the
Mac's memory, as macOS does.
"""

from __future__ import annotations

import pytest

from .conftest import StackCLI, write_instance

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

GB = 1024 ** 3


@pytest.fixture
def doctor(tmp_path, monkeypatch, as_stack_runs_it):
    """`stack doctor --json` on a Mac with `memory_gb`, the ai stacklet set
    up, and this `[ai]`: the findings' titles and fixes."""
    def run(memory_gb: int, ai: str = "[ai]\n", core: str = "") -> list[dict]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        (bin_dir / "sysctl").write_text(f"#!/bin/sh\necho {memory_gb * GB}\n")
        (bin_dir / "sysctl").chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
        instance = write_instance(tmp_path, extra=ai, core=core)
        (instance / ".stack" / "ai.setup-done").touch()
        return StackCLI(instance, monkeypatch)("doctor", "--json").answer["findings"]
    return run


def _speech(findings: list[dict]) -> list[dict]:
    return [f for f in findings if "speech" in f["title"]]


def test_an_instance_from_before_the_profile_is_told_what_speech_costs(doctor):
    """Given a 16 GB Mac with speech on and no stored choice, doctor warns
    and names the line that turns speech off."""
    found = _speech(doctor(16))

    assert [f["level"] for f in found] == ["warn"]
    assert "1.6 GB" in found[0]["detail"]
    assert "speech = false" in found[0]["fix"]


@pytest.mark.parametrize("ai", ["[ai]\nspeech = true\n", "[ai]\nspeech = false\n"])
def test_a_stored_choice_is_not_reported(doctor, ai):
    assert _speech(doctor(16, ai)) == []


def test_a_larger_mac_is_not_told(doctor):
    assert _speech(doctor(32)) == []


def test_a_mac_set_to_the_small_profile_is_told(doctor):
    assert len(_speech(doctor(32, core='profile = "small"'))) == 1
