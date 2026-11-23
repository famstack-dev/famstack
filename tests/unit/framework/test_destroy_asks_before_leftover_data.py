"""`stack destroy <id>` asks before it deletes a data folder that holds files.

A stacklet that is not set up and runs no container can still have a
data folder. A setup that failed leaves one behind, and so does an
uninstall whose delete was declined: there the folder holds the
family's photos or documents, kept on purpose. `stack destroy` cannot
tell the two apart, so a folder with files in it is deleted only after
a confirmation that names it, as every other delete of the stack:
`destroy` typed at the question, or `--yes`. Without a terminal and
without `--yes` it refuses and deletes nothing. A folder with no file
in it holds nothing to lose and goes without a question.

These tests run `./stack` as a process from a checkout in `tmp_path`
(`tests/stack_process.py`), over a fabricated Simpsons instance in
`tmp_path` (`STACK_DIR`), with `HOME` there too.
"""

from __future__ import annotations

import pytest

from tests.springfield import write_instance
from tests.stack_process import Checkout

KEPT_PHOTO = "library/2026/homer-and-marge.jpg"


@pytest.fixture
def checkout(tmp_path, monkeypatch, docker_with_projects):
    """A Simpsons instance where photos is not set up but has data."""
    monkeypatch.setenv("HOME", str(tmp_path))
    docker_with_projects()
    monkeypatch.setenv("STACK_DIR", str(write_instance(tmp_path)))
    return Checkout.under(tmp_path)


@pytest.fixture
def photos_data(tmp_path):
    photo = tmp_path / "data" / "photos" / KEPT_PHOTO
    photo.parent.mkdir(parents=True)
    photo.write_text("a picture")
    return tmp_path / "data" / "photos"


def test_a_declined_uninstall_keeps_its_data_through_a_destroy_without_input(
        checkout, tmp_path, photos_data):
    """Given an uninstall whose delete was declined and a new stack.toml,
    when `stack destroy photos` runs with no terminal and no `--yes`, then
    it refuses and the kept photo is still there."""
    instance = tmp_path / "instance"
    (instance / ".stack" / "photos.setup-done").touch()
    config = (instance / "stack.toml").read_text()
    assert checkout.stack("uninstall", "--yes", answer="no").returncode == 0
    (instance / "stack.toml").write_text(config)

    run = checkout.stack("destroy", "photos", answer=None)

    assert run.returncode == 1
    assert (photos_data / KEPT_PHOTO).read_text() == "a picture"


def test_yes_deletes_the_leftover_data(checkout, photos_data):
    """Given photos' data folder with a file, when `stack destroy photos
    --yes` runs, then the folder is gone."""
    run = checkout.stack("destroy", "photos", "--yes", answer=None)

    assert run.returncode == 0, run.stderr
    assert not photos_data.exists()


@pytest.mark.parametrize("answer", ["no", "", "Destroy"])
def test_an_answer_other_than_destroy_keeps_the_data(checkout, photos_data, answer):
    """Given the same in a terminal, when the admin answers anything but
    `destroy`, then the photo is still there."""
    checkout.stack_in_terminal("destroy", "photos", answer=answer)

    assert (photos_data / KEPT_PHOTO).read_text() == "a picture"


def test_destroy_at_the_question_deletes_and_names_the_folder(checkout, photos_data):
    """Given the same in a terminal, when the admin types `destroy`, then
    the folder is gone, and the question named it and its file count
    (`~` is `HOME`, here `tmp_path`)."""
    run = checkout.stack_in_terminal("destroy", "photos", answer="destroy")

    assert run.returncode == 0, run.stderr
    assert not photos_data.exists()
    assert "~/data/photos" in run.stdout
    assert "1 file" in run.stdout


def test_a_folder_without_files_goes_without_a_question(checkout, photos_data):
    """Given photos' data folder holds only empty folders (a setup that
    stopped early), when `stack destroy photos` runs with no terminal,
    then the folder is gone: there is nothing in it to lose."""
    (photos_data / KEPT_PHOTO).unlink()

    run = checkout.stack("destroy", "photos", answer=None)

    assert run.returncode == 0, run.stderr
    assert not photos_data.exists()
