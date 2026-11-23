"""`stack uninstall` deletes the data only when the admin types `delete`.

The uninstall tears the stack down first (containers, secrets, setup
markers, the config) and then asks one question about the data folder:
"Type 'delete' to remove all data". Every other answer, an empty one, a
closed terminal and Ctrl+C keep the data, file for file, because the
data folder holds the family's photos, messages and documents and a
delete is its own step with its own confirmation.

These tests drive `./stack uninstall --yes` through `cli.main`, the entry
point `./stack` runs, over a fabricated Simpsons instance in `tmp_path`.
`HOME` points at `tmp_path` too, so the code under test never sees the
home folder of the Mac that runs the suite. Docker is the stand-in from
`tests/docker_with_projects.py`.
"""

from __future__ import annotations

import pytest

from tests.springfield import write_instance

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


def _uninstall(monkeypatch, *flags: str) -> None:
    """Run `./stack uninstall --yes <flags>` as the shell runs it."""
    from stack import cli

    monkeypatch.setattr("sys.argv", ["stack", "uninstall", "--yes", *flags])
    cli.main()


def _family_files(tmp_path) -> dict[str, str]:
    """Every file under the data folder, by its path, with its content."""
    data = tmp_path / "data"
    return {str(p.relative_to(data)): p.read_text()
            for p in sorted(data.rglob("*")) if p.is_file()}


def _as_written() -> dict[str, str]:
    return {f"{sid}/{path}": content for sid, (path, content) in FAMILY_FILES.items()}


# ── The data stays unless the answer is `delete` ────────────────────────

@pytest.mark.parametrize("answer", ["no", "", "Delete", "uninstall"])
def test_an_answer_other_than_delete_keeps_every_file(
        instance, tmp_path, monkeypatch, terminal, capsys, answer):
    """Given photos and docs with data, when the admin answers anything but
    `delete`, then every file is still there and the output says so.
    The data question comes after the teardown, so the data must survive
    the teardown itself."""
    terminal.types(answer)

    _uninstall(monkeypatch)

    assert _family_files(tmp_path) == _as_written()
    assert "Data kept" in capsys.readouterr().out


@pytest.mark.parametrize("ends", ["closes", "interrupts"])
def test_an_aborted_question_keeps_every_file(instance, tmp_path, monkeypatch, terminal, ends):
    """Given the same, when the terminal closes or Ctrl+C ends the question,
    then nothing is deleted: no answer is never a yes."""
    getattr(terminal, ends)()

    _uninstall(monkeypatch)

    assert _family_files(tmp_path) == _as_written()


def test_the_teardown_still_removes_markers_secrets_and_config(
        instance, monkeypatch, terminal):
    """Given the same, when the admin declines the delete, then the config,
    the secrets and the setup markers are gone as before: only the data
    waits for the answer."""
    terminal.types("no")

    _uninstall(monkeypatch)

    assert not (instance / "stack.toml").exists()
    assert not (instance / "users.toml").exists()
    assert not (instance / ".stack").exists()


# ── `delete` removes it ─────────────────────────────────────────────────

def test_delete_removes_the_data_folder(instance, tmp_path, monkeypatch, terminal, capsys):
    """Given the same, when the admin types `delete`, then the data folder
    is gone and the output names it as removed (`~` is `HOME`, here
    `tmp_path`)."""
    terminal.types("delete")

    _uninstall(monkeypatch)

    assert not (tmp_path / "data").exists()
    assert "Removed ~/data" in capsys.readouterr().out


# ── An excluded stacklet keeps its data in both answers ─────────────────

@pytest.mark.parametrize("answer", ["no", "delete"])
def test_an_excluded_stacklet_keeps_its_data(instance, tmp_path, monkeypatch, terminal, answer):
    """Given `--not photos`, then photos keeps its data whatever the answer,
    and docs keeps its data unless the answer is `delete`."""
    terminal.types(answer)

    _uninstall(monkeypatch, "--not", "photos")

    kept = _as_written()
    if answer == "delete":
        del kept["docs/" + FAMILY_FILES["docs"][0]]
    assert _family_files(tmp_path) == kept
