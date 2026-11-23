"""`stack uninstall` deletes the data only when the admin types `delete`.

The uninstall tears the stack down first (containers, secrets, setup
markers, the config) and then asks one question about the data folder:
"Type 'delete' to remove all data". Every other answer, an empty one, a
closed terminal and Ctrl+C keep the data, file for file, because the
data folder holds the family's photos, messages and documents and a
delete is its own step with its own confirmation.

These tests run `./stack uninstall --yes` as a process, the way a shell
runs it, with the answer on its standard input, over a fabricated
Simpsons instance in `tmp_path` (`STACK_DIR`). `HOME` points at
`tmp_path` too, so the code under test never sees the home folder of the
Mac that runs the suite. Docker is the stand-in from
`tests/docker_with_projects.py`, first on the process's `PATH`. A run
takes about a second, most of it Python starting up.
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time

import pytest

from tests.springfield import REPO, write_instance

# The question the uninstall asks last; Ctrl+C is pressed when it shows.
DATA_QUESTION = b"remove all data: "

# Long enough for a loaded machine, short enough that a hang fails fast.
RUN_SECONDS = 30

# Stacklets of the repo without stop or destroy hooks, so their teardown
# touches nothing outside the instance.
STACKLETS = ("photos", "docs")

# One made-up file per stacklet: what the family keeps there.
FAMILY_FILES = {
    "photos": ("library/2026/homer-and-marge.jpg", "a picture"),
    "docs": ("media/originals/power-plant-contract.pdf", "a contract"),
}


@pytest.fixture
def instance(tmp_path, monkeypatch, docker_with_projects):
    """A Simpsons instance whose photos and docs were set up and hold data."""
    monkeypatch.setenv("HOME", str(tmp_path))
    docker_with_projects()
    instance = write_instance(tmp_path)
    for sid in STACKLETS:
        (instance / ".stack" / f"{sid}.setup-done").touch()
        path, content = FAMILY_FILES[sid]
        family_file = tmp_path / "data" / sid / path
        family_file.parent.mkdir(parents=True)
        family_file.write_text(content)
    monkeypatch.setenv("STACK_DIR", str(instance))
    return instance


def _uninstall(*flags: str, answer: str | None = "") -> subprocess.CompletedProcess:
    """Run `./stack uninstall --yes <flags>` with `answer` typed at the data
    question; None closes standard input without an answer."""
    typed = "" if answer is None else f"{answer}\n"
    return subprocess.run(_command(*flags), input=typed, capture_output=True, text=True,
                          cwd=REPO, env=_environment(), timeout=RUN_SECONDS)


def _uninstall_interrupted(*flags: str) -> int:
    """Run `./stack uninstall --yes <flags>` and press Ctrl+C (SIGINT) when
    the data question shows. Returns the exit code."""
    process = subprocess.Popen(_command(*flags), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, cwd=REPO, env=_environment())
    try:
        _wait_for(process, DATA_QUESTION)
        process.send_signal(signal.SIGINT)
        return process.wait(timeout=RUN_SECONDS)
    finally:
        process.kill()
        process.wait()


def _wait_for(process: subprocess.Popen, text: bytes) -> None:
    """Read the process's output until `text` shows; fail after RUN_SECONDS."""
    seen = b""
    deadline = time.monotonic() + RUN_SECONDS
    while text not in seen:
        left = deadline - time.monotonic()
        assert left > 0, f"the question never showed: {seen.decode()[-400:]}"
        ready, _, _ = select.select([process.stdout], [], [], left)
        chunk = os.read(process.stdout.fileno(), 4096) if ready else b""
        assert chunk or ready == [], f"the process ended first: {seen.decode()[-400:]}"
        seen += chunk


def _command(*flags: str) -> list[str]:
    return [sys.executable, "-m", "stack", "uninstall", "--yes", *flags]


def _environment() -> dict[str, str]:
    """The test's environment (`HOME`, `STACK_DIR`, the docker stand-in on
    `PATH`), with the repo's `lib` to import from, as `./stack` sets it."""
    return {**os.environ, "PYTHONPATH": str(REPO / "lib")}


def _family_files(tmp_path) -> dict[str, str]:
    """Every file under the data folder, by its path, with its content."""
    data = tmp_path / "data"
    return {str(p.relative_to(data)): p.read_text()
            for p in sorted(data.rglob("*")) if p.is_file()}


def _as_written() -> dict[str, str]:
    return {f"{sid}/{path}": content for sid, (path, content) in FAMILY_FILES.items()}


# ── The data stays unless the answer is `delete` ────────────────────────

@pytest.mark.parametrize("answer", ["no", "", "Delete", "uninstall"])
def test_an_answer_other_than_delete_keeps_every_file(instance, tmp_path, answer):
    """Given photos and docs with data, when the admin answers anything but
    `delete`, then every file is still there and the output says so.
    The data question comes after the teardown, so the data must survive
    the teardown itself."""
    run = _uninstall(answer=answer)

    assert run.returncode == 0, run.stderr
    assert _family_files(tmp_path) == _as_written()
    assert "Data kept" in run.stdout


def test_a_closed_terminal_keeps_every_file(instance, tmp_path):
    """Given the same, when standard input ends before an answer, then
    nothing is deleted: no answer is never a yes."""
    run = _uninstall(answer=None)

    assert run.returncode == 0, run.stderr
    assert _family_files(tmp_path) == _as_written()


def test_ctrl_c_at_the_question_keeps_every_file(instance, tmp_path):
    """Given the same, when the admin presses Ctrl+C at the data question,
    then nothing is deleted."""
    _uninstall_interrupted()

    assert _family_files(tmp_path) == _as_written()


def test_the_teardown_still_removes_markers_secrets_and_config(instance):
    """Given the same, when the admin declines the delete, then the config,
    the secrets and the setup markers of the instance are gone as before:
    only the data waits for the answer."""
    run = _uninstall(answer="no")

    assert run.returncode == 0, run.stderr
    assert not (instance / "stack.toml").exists()
    assert not (instance / "users.toml").exists()
    assert not (instance / ".stack").exists()


# ── `delete` removes it ─────────────────────────────────────────────────

def test_delete_removes_the_data_folder(instance, tmp_path):
    """Given the same, when the admin types `delete`, then the data folder
    is gone and the output names it as removed (`~` is `HOME`, here
    `tmp_path`)."""
    run = _uninstall(answer="delete")

    assert run.returncode == 0, run.stderr
    assert not (tmp_path / "data").exists()
    assert "Removed ~/data" in run.stdout


# ── An excluded stacklet keeps its data in both answers ─────────────────

@pytest.mark.parametrize("answer", ["no", "delete"])
def test_an_excluded_stacklet_keeps_its_data(instance, tmp_path, answer):
    """Given `--not photos`, then photos keeps its data whatever the answer,
    and docs keeps its data unless the answer is `delete`."""
    run = _uninstall("--not", "photos", answer=answer)

    assert run.returncode == 0, run.stderr
    kept = _as_written()
    if answer == "delete":
        del kept["docs/" + FAMILY_FILES["docs"][0]]
    assert _family_files(tmp_path) == kept
