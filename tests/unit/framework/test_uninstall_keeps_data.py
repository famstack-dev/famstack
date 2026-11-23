"""`stack uninstall` deletes the data only when the admin types `delete`.

The uninstall tears the stack down first (containers, setup markers, the
config) and then asks one question about the data folder: "Type 'delete'
to remove all data". Every other answer, an empty one, a closed terminal
and Ctrl+C keep the data, file for file, and the secrets that open it
(the databases' passwords in `.stack/secrets.toml`), because the data
folder holds the family's photos, messages and documents and a new
install over it must be able to open it. A delete is its own step with
its own confirmation.

These tests run `./stack uninstall --yes` as a process from a checkout
in `tmp_path` (`tests/stack_process.py`), with the answer on its
standard input, over a fabricated Simpsons instance in `tmp_path`
(`STACK_DIR`). `HOME` points at `tmp_path` too, so the code under test
never sees the home folder of the Mac that runs the suite. Docker is the
stand-in from `tests/docker_with_projects.py`, first on the process's
`PATH`. A run takes about a second, most of it Python starting up.
"""

from __future__ import annotations

import shutil

import pytest

from stack.secrets import TomlSecretStore
from tests.springfield import USERS, write_instance
from tests.stack_process import Checkout

# The question the uninstall asks last; Ctrl+C is pressed when it shows.
DATA_QUESTION = b"remove all data: "

# Stacklets of the repo without stop or destroy hooks, so their teardown
# touches nothing outside the instance.
STACKLETS = ("photos", "docs")

# One made-up file per stacklet: what the family keeps there.
FAMILY_FILES = {
    "photos": ("library/2026/homer-and-marge.jpg", "a picture"),
    "docs": ("media/originals/power-plant-contract.pdf", "a contract"),
}

# The config of the instance the checkout serves itself, which an
# uninstall of the instance in STACK_DIR must not touch.
CHECKOUTS_OWN = {
    "stack.toml": '[core]\nname = "stack"\n',
    "users.toml": USERS,
    ".stack/secrets.toml": 'global__ADMIN_PASSWORD = "the checkout\'s own"\n',
}


@pytest.fixture
def checkout(tmp_path, monkeypatch, docker_with_projects):
    """A checkout with its own config, and in STACK_DIR a Simpsons instance
    whose photos and docs were set up and hold data."""
    monkeypatch.setenv("HOME", str(tmp_path))
    docker_with_projects()
    checkout = Checkout.under(tmp_path)
    for name, content in CHECKOUTS_OWN.items():
        (checkout.path / name).parent.mkdir(exist_ok=True)
        (checkout.path / name).write_text(content)
    instance = write_instance(tmp_path)
    for sid in STACKLETS:
        (instance / ".stack" / f"{sid}.setup-done").touch()
        path, content = FAMILY_FILES[sid]
        family_file = tmp_path / "data" / sid / path
        family_file.parent.mkdir(parents=True)
        family_file.write_text(content)
    monkeypatch.setenv("STACK_DIR", str(instance))
    return checkout


@pytest.fixture
def instance(tmp_path, checkout):
    return tmp_path / "instance"


def _uninstall(checkout, *flags: str, answer: str | None = ""):
    """`./stack uninstall --yes <flags>` with `answer` at the data question."""
    run = checkout.stack("uninstall", "--yes", *flags, answer=answer)
    assert run.returncode == 0, run.stderr
    return run


def _family_files(tmp_path) -> dict[str, str]:
    """Every file under the data folder, by its path, with its content."""
    data = tmp_path / "data"
    return {str(p.relative_to(data)): p.read_text()
            for p in sorted(data.rglob("*")) if p.is_file()}


def _as_written() -> dict[str, str]:
    return {f"{sid}/{path}": content for sid, (path, content) in FAMILY_FILES.items()}


def _db_password(checkout) -> str:
    """The `DB_PASSWORD` `stack up photos` renders into photos' `.env`;
    rendering generates it when the secrets hold none."""
    run = checkout.stack("env", "photos")
    assert run.returncode == 0, run.stderr
    lines = [line.strip() for line in run.stdout.splitlines()]
    return next(line.split("=", 1)[1] for line in lines if line.startswith("DB_PASSWORD="))


def _secrets(instance) -> TomlSecretStore:
    return TomlSecretStore(instance / ".stack" / "secrets.toml")


# ── The data stays unless the answer is `delete` ────────────────────────

@pytest.mark.parametrize("answer", ["no", "", "Delete", "uninstall"])
def test_an_answer_other_than_delete_keeps_every_file(checkout, tmp_path, answer):
    """Given photos and docs with data, when the admin answers anything but
    `delete`, then every file is still there and the output says so.
    The data question comes after the teardown, so the data must survive
    the teardown itself."""
    run = _uninstall(checkout, answer=answer)

    assert _family_files(tmp_path) == _as_written()
    assert "Data kept" in run.stdout


def test_a_closed_terminal_keeps_every_file(checkout, tmp_path):
    """Given the same, when standard input ends before an answer, then
    nothing is deleted: no answer is never a yes."""
    _uninstall(checkout, answer=None)

    assert _family_files(tmp_path) == _as_written()


def test_ctrl_c_at_the_question_keeps_every_file(checkout, tmp_path):
    """Given the same, when the admin presses Ctrl+C at the data question,
    then nothing is deleted."""
    checkout.stack_interrupted("uninstall", "--yes", at=DATA_QUESTION)

    assert _family_files(tmp_path) == _as_written()


def test_a_new_install_over_kept_data_has_its_old_secrets(checkout, instance):
    """Given photos with data and its database password, when the admin
    declines the delete and installs again (`stack install` writes
    stack.toml and users.toml), then photos renders the same password:
    the kept database opens. A new password would lock Immich out of it,
    because Postgres keeps the password it was made with."""
    before = _db_password(checkout)
    config = {name: (instance / name).read_text() for name in ("stack.toml", "users.toml")}

    _uninstall(checkout, answer="no")
    for name, content in config.items():
        (instance / name).write_text(content)

    assert _db_password(checkout) == before


def test_the_teardown_still_removes_markers_and_config(checkout, instance):
    """Given the same, when the admin declines the delete, then the config
    and the setup markers of the instance are gone as before: only the
    data and its secrets wait for the answer."""
    _uninstall(checkout, answer="no")

    assert not (instance / "stack.toml").exists()
    assert not (instance / "users.toml").exists()
    assert sorted(p.name for p in (instance / ".stack").iterdir()) == ["secrets.toml"]


def test_a_second_uninstall_keeps_the_secrets_of_the_kept_data(checkout, tmp_path, instance):
    """Given a first uninstall whose delete was declined, over a data folder
    that is not the default one, when the admin uninstalls again, then
    the data and its secrets are still there, and the output names the
    secrets file. The first run removed stack.toml, the one place that
    names the data folder: the second run must not take the default
    folder, empty here, as "no data" and remove the secrets of the data
    it cannot see."""
    password = _db_password(checkout)
    _uninstall(checkout, answer="no")

    run = _uninstall(checkout, answer=None)

    assert _family_files(tmp_path) == _as_written()
    assert _secrets(instance).get("photos", "DB_PASSWORD") == password
    assert str(instance / ".stack" / "secrets.toml").replace(str(tmp_path), "~") in run.stdout


def test_a_data_folder_that_is_not_there_keeps_the_secrets(checkout, tmp_path, instance):
    """Given photos set up with data in the folder stack.toml names, when
    that folder is not there (its disk is not mounted, or it was moved)
    and the admin uninstalls, then the secrets stay and the output names
    the missing folder and the secrets file: the data comes back with its
    disk, and its databases need their passwords. The setup markers are
    the one sign here that something was set up."""
    (tmp_path / "data").rename(tmp_path / "unmounted")

    run = _uninstall(checkout, answer="no")

    assert _secrets(instance).get("global", "ADMIN_PASSWORD") == "donuts"
    assert "~/data" in run.stdout
    assert "~/instance/.stack/secrets.toml" in run.stdout


@pytest.mark.parametrize("config", ["[core\nbroken = ", '[core]\nname = "stack"\n'],
                         ids=["does-not-parse", "names-no-data-dir"])
def test_a_stack_toml_that_names_no_data_folder_keeps_the_secrets(
        checkout, tmp_path, instance, config):
    """Given photos set up with data, when stack.toml does not parse or
    names no `[core] data_dir` and the admin uninstalls, then the data and
    the secrets stay, and so does the default data folder the stack would
    use instead (`~/stack-data`): it is a guess at where the data is, so
    it is neither asked about nor deleted."""
    password = _db_password(checkout)
    (instance / "stack.toml").write_text(config)
    default = tmp_path / "stack-data" / "photos" / "someone-elses.jpg"
    default.parent.mkdir(parents=True)
    default.write_text("not this instance's")

    _uninstall(checkout, answer="delete")

    assert _family_files(tmp_path) == _as_written()
    assert _secrets(instance).get("photos", "DB_PASSWORD") == password
    assert default.read_text() == "not this instance's"


def _nothing_set_up(tmp_path, instance) -> None:
    """Take the instance back to one where nothing was set up: no setup
    marker, no stacklet's secret, no data folder."""
    for marker in (instance / ".stack").glob("*.setup-done"):
        marker.unlink()
    shutil.rmtree(tmp_path / "data")


def test_nothing_set_up_removes_the_secrets(checkout, tmp_path, instance):
    """Given an instance where nothing was set up (no marker, no container,
    no data folder), when the admin uninstalls, then `.stack/` goes with
    its secrets: there is no data they could open."""
    _nothing_set_up(tmp_path, instance)

    _uninstall(checkout, answer=None)

    assert not (instance / ".stack").exists()


def _a_container(tmp_path, instance, docker_with_projects) -> None:
    docker_with_projects(photos="stopped")


def _a_folder_in_the_default_data_folder(tmp_path, instance, docker_with_projects) -> None:
    (instance / "stack.toml").write_text('[core]\nname = "stack"\n')
    (tmp_path / "stack-data" / "photos").mkdir(parents=True)


def _a_secrets_file_that_cannot_be_read(tmp_path, instance, docker_with_projects) -> None:
    (instance / ".stack" / "secrets.toml").write_text("[photos\nDB_PASSWORD = ")


@pytest.mark.parametrize("sign", [_a_container, _a_folder_in_the_default_data_folder,
                                  _a_secrets_file_that_cannot_be_read],
                         ids=["container", "default-data-folder", "unreadable-secrets"])
def test_one_sign_of_a_setup_keeps_the_secrets(
        checkout, tmp_path, instance, docker_with_projects, sign):
    """Given an instance with no data folder and one sign that something
    was set up (a stacklet's container, its folder in the default data
    folder, a secrets file that cannot be read), when the admin
    uninstalls, then `secrets.toml` stays: the sign says data may exist
    somewhere the uninstall cannot see, and its databases need the
    passwords."""
    _nothing_set_up(tmp_path, instance)
    sign(tmp_path, instance, docker_with_projects)

    _uninstall(checkout, answer=None)

    assert (instance / ".stack" / "secrets.toml").exists()


def test_the_checkouts_own_config_stays(checkout):
    """Given STACK_DIR names an instance other than the checkout, when the
    admin uninstalls it, then the checkout's own stack.toml, users.toml
    and .stack/ are untouched: they belong to another instance."""
    _uninstall(checkout, answer="delete")

    assert {name: (checkout.path / name).read_text() for name in CHECKOUTS_OWN} == CHECKOUTS_OWN


# ── `delete` removes it ─────────────────────────────────────────────────

def test_delete_removes_the_data_and_its_secrets(checkout, tmp_path, instance):
    """Given the same, when the admin types `delete`, then the data folder
    and the instance's runtime state with its secrets are gone, and the
    output names the folder as removed (`~` is `HOME`, here `tmp_path`)."""
    run = _uninstall(checkout, answer="delete")

    assert not (tmp_path / "data").exists()
    assert not (instance / ".stack").exists()
    assert "Removed ~/data" in run.stdout


# ── An excluded stacklet keeps its data in both answers ─────────────────

@pytest.mark.parametrize("answer", ["no", "delete"])
def test_an_excluded_stacklet_keeps_its_data(checkout, tmp_path, answer):
    """Given `--not photos`, then photos keeps its data whatever the answer,
    and docs keeps its data unless the answer is `delete`."""
    _uninstall(checkout, "--not", "photos", answer=answer)

    kept = _as_written()
    if answer == "delete":
        del kept["docs/" + FAMILY_FILES["docs"][0]]
    assert _family_files(tmp_path) == kept


def test_delete_with_an_excluded_stacklet_keeps_its_secrets(checkout, instance):
    """Given `--not photos` and the answer `delete`, then photos keeps its
    database password and docs loses its own with its data."""
    photos = _db_password(checkout)
    _secrets(instance).set("docs", "DB_PASSWORD", "docs-password")

    _uninstall(checkout, "--not", "photos", answer="delete")

    assert _secrets(instance).get("photos", "DB_PASSWORD") == photos
    assert _secrets(instance).get("docs", "DB_PASSWORD") is None
