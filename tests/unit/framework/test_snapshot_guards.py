# ruff: noqa: F811  (the imported fixtures are the tests' parameters)
"""The guards that keep a release and a snapshot from losing the way back, as the admin meets them.

Only `stack restart` applies a release with a snapshot first. A `stack
up` of a stacklet that runs an older release than the checkout would
recreate it on the new code with no snapshot, so it refuses and names the
restart. A stopped stacklet starts as before. A snapshot of a stacklet
whose state a stopped rollback holds, or whose last snapshot failed, would
become the newest snapshot of a state that is not whole, so `stack
snapshot create` refuses it with the text `stack up` gives.

The tests run `./stack` as a process of its own against the fabricated
checkout of `snapshot_instance`: `docs` and `photos` run rc.1, the
checkout is on rc.2, and Docker is the stand-in `docker_with_projects`.
Each test runs one to four `./stack` commands, one to two seconds.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.framework.snapshot_instance import (  # noqa: F401 (the fixtures)
    Checkout,
    REPO,
    Ran,
    STACK_SECONDS,
    checkout,
    clone_fails,
    host_checkout,
    rollback,
    updated,
)



def error_of(ran) -> str:
    """The `error` of a command run with `--json`, from its answer."""
    for stream in (ran.out, ran.err):
        try:
            answer = json.loads(stream)
        except ValueError:
            continue
        if isinstance(answer, dict) and "error" in answer:
            return answer["error"]
    raise AssertionError(f"no error in the answer: {ran.out!r} {ran.err!r}")


def failed_restart(checkout: Checkout) -> None:
    """A restart whose snapshot failed: docs and photos stopped, each with
    the record of its failed take."""
    (checkout.data / ".snapshots").write_text("not a folder")
    assert checkout.stack("restart").code == 1
    (checkout.data / ".snapshots").unlink()
    checkout.docker.forget()


# ── `stack up` of a running stacklet on an older release ─────────────────

def test_up_of_a_running_stacklet_on_an_older_release_refuses_and_changes_nothing(checkout):
    """Given that `stack update` moved the checkout to rc.2 and docs runs
    rc.1, when the admin runs `stack up docs`, then docs keeps running on
    rc.1, nothing reaches compose, no snapshot exists, and the answer names
    the restart with and without a snapshot. A start here would apply the
    release, and its migration, with no way back."""
    checkout.docker.forget()

    ran = checkout.stack("up", "docs")

    assert ran.code != 0
    assert checkout.docker.compose_steps() == []
    assert checkout.docker.state_of("docs") == "running"
    assert checkout.snapshot_folders() == []
    said = ran.out + ran.err
    assert "stack restart docs" in said
    assert "stack restart docs --no-snapshot" in said


def test_up_of_several_refuses_each_one_on_an_older_release(checkout):
    """Given docs and photos both on rc.1, when the admin runs `stack up
    docs photos`, then neither is recreated and both are named."""
    checkout.docker.forget()

    ran = checkout.stack("up", "docs", "photos", "--json")

    assert ran.code != 0
    assert checkout.docker.compose_steps() == []
    said = ran.out + ran.err
    assert "stack restart docs" in said and "stack restart photos" in said


def test_up_of_a_stacklet_without_containers_on_an_older_release_refuses(host_checkout):
    """Given speech, up without containers from rc.1, when the admin runs
    `stack up speech`, then it refuses: its stamp still names rc.1, and
    its hook would start the host service on rc.2's code."""
    ran = host_checkout.stack("up", "speech")

    assert ran.code != 0
    assert (host_checkout.instance / ".stack" / "speech.up").read_text() == host_checkout.rc1
    assert "stack restart speech" in ran.out + ran.err


def test_up_of_a_stopped_stacklet_on_an_older_release_starts_it(checkout):
    """Given docs stopped (`stack down docs`) while the checkout is on rc.2,
    when the admin runs `stack up docs`, then docs starts on rc.2 as
    before: nothing runs that a snapshot would keep from moving."""
    assert checkout.stack("down", "docs").code == 0
    checkout.docker.forget()

    ran = checkout.stack("up", "docs")

    assert ran.code == 0, ran.err
    assert "up" in checkout.docker.compose_steps()
    assert checkout.docker.state_of("docs") == "running"
    assert checkout.snapshot_folders() == []


def test_up_of_a_running_stacklet_on_the_current_release_recreates_it(checkout):
    """Given docs started from rc.2, the checkout's release, when the admin
    runs `stack up docs`, then it recreates docs as before: no release is
    applied."""
    checkout.docker.commits(docs=checkout.rc2, photos=checkout.rc1)
    checkout.docker.forget()

    ran = checkout.stack("up", "docs")

    assert ran.code == 0, ran.err
    assert "up" in checkout.docker.compose_steps()


def test_the_restart_still_applies_the_release_that_up_refuses(checkout):
    """Given docs on rc.1 that `stack up docs` refused, when the admin runs
    the restart it names, then docs starts on rc.2 with its snapshot."""
    assert checkout.stack("up", "docs").code != 0

    ran = checkout.stack("restart", "docs")

    assert ran.code == 0, ran.err
    assert checkout.docker.state_of("docs") == "running"
    assert len(checkout.snapshot_folders()) == 1


# ── `stack snapshot create` of a stacklet that waits ─────────────────────

def test_create_refuses_a_stacklet_whose_last_snapshot_failed(checkout):
    """Given docs stopped by a restart whose snapshot failed, when the
    admin runs `stack snapshot create docs`, then it takes nothing, leaves
    docs stopped, and refuses with the error `stack up docs` gives."""
    failed_restart(checkout)

    created = checkout.stack("snapshot", "create", "docs", "--json")

    assert created.code != 0
    assert checkout.snapshot_folders() == []
    assert checkout.docker.compose_steps() == []
    assert checkout.docker.state_of("docs") == "stopped"
    assert error_of(created) in checkout.stack("up", "docs").err


def test_create_refuses_a_stacklet_a_stopped_rollback_holds(updated):
    """Given a rollback that stopped while it cloned photos back (photos
    half restored), when the admin runs `stack snapshot create photos`,
    then no new snapshot exists and the error is the one `stack up photos`
    gives. A half-restored state as the newest snapshot would be where
    the next rollback goes."""
    stops = clone_fails(updated)
    assert rollback(updated).code != 0
    stops()
    before = updated.snapshot_folders()
    updated.docker.forget()

    created = updated.stack("snapshot", "create", "photos", "--json")

    assert created.code != 0
    assert updated.snapshot_folders() == before
    assert updated.docker.compose_steps() == []
    assert error_of(created) in updated.stack("up", "photos").err


# ── What a refusal leaves alone ──────────────────────────────────────────

def chat_log(checkout: Checkout) -> Path:
    """Put a `stack` script in the checkout that notes each Server-room
    notice (`stack messages send famstack ...`) instead of posting it,
    and answer where it notes them."""
    log = checkout.root.parent / "chat.log"
    script = checkout.root / "stack"
    script.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    script.chmod(0o755)
    return log


def notices(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def test_a_refused_up_posts_no_notice_into_the_server_room(checkout):
    """Given docs on rc.1 and the checkout on rc.2, when the admin runs
    `stack up docs` and it is refused, then the Server room gets no
    "failed to start" notice: docs runs as it did, nothing failed. The
    `stack down docs` after it shows that a notice does reach the log."""
    log = chat_log(checkout)

    assert checkout.stack("up", "docs").code != 0
    assert notices(log) == []

    assert checkout.stack("down", "docs").code == 0
    assert notices(log) != []


def test_up_of_a_stacklet_on_a_newer_release_than_the_checkout_refuses(checkout):
    """Given docs started from rc.2 while the checkout is back on rc.1 (a
    rollback of the other stacklets), when the admin runs `stack up docs`,
    then it refuses too: a start on rc.1's code is a release change with
    no snapshot, in this direction as well."""
    checkout.docker.commits(docs=checkout.rc2, photos=checkout.rc1)
    checkout.git("checkout", "-q", "v0.4.0-rc.1")
    checkout.docker.forget()

    ran = checkout.stack("up", "docs")

    assert ran.code != 0
    assert checkout.docker.compose_steps() == []
    assert "stack restart docs" in ran.out + ran.err


def test_a_restart_whose_stop_fails_says_so_and_starts_nothing(checkout):
    """Given docs on rc.1 whose stop fails, when the admin runs `stack
    restart docs --no-snapshot`, then the restart ends with the stop's
    error and does not try the start: a start of a stacklet that still
    runs would only be refused, naming the command just run."""
    checkout.docker.fail("stop", "container is busy", stacklet="docs")
    checkout.docker.forget()

    ran = checkout.stack("restart", "docs", "--no-snapshot")

    assert ran.code == 1
    assert "up" not in checkout.docker.compose_steps()
    said = ran.out + ran.err
    assert "container is busy" in said
    assert "stack restart docs" not in said


# ── Core ─────────────────────────────────────────────────────────────────

CORE_TOML = 'id = "core"\nname = "Core"\ncategory = "test"\n'
CORE_COMPOSE = """
name: stack-core
services:
  tools:
    image: ghcr.io/example/tools:{tag}
    container_name: stack-core-tools
"""


def test_up_of_core_on_an_older_release_refuses_as_any_stacklet(checkout):
    """Given core running rc.2 and a release rc.3 that changes core, when
    the admin runs `stack up core`, then core is not recreated and the
    answer names `stack restart core`, as for any other stacklet."""
    core = checkout.root / "stacklets" / "core"
    core.mkdir()
    (core / "stacklet.toml").write_text(CORE_TOML)
    (core / "docker-compose.yml").write_text(CORE_COMPOSE.format(tag="1"))
    checkout.release("v0.4.0-rc.3")
    (core / "docker-compose.yml").write_text(CORE_COMPOSE.format(tag="2"))
    checkout.release("v0.4.0-rc.4")
    rc3 = checkout.git("rev-parse", "--short", "v0.4.0-rc.3^{commit}")
    (checkout.instance / ".stack" / "core.setup-done").touch()
    checkout.docker.commits(core=rc3, docs=checkout.rc1, photos=checkout.rc1)
    checkout.docker.forget()

    ran = checkout.stack("up", "core")

    assert ran.code != 0
    assert checkout.docker.compose_steps() == []
    assert "stack restart core" in ran.out + ran.err


# ── Doctor ───────────────────────────────────────────────────────────────

MIGRATION = '''
SUMMARY = "Move the tags"


def run(ctx):
    pass
'''


def test_doctor_names_the_restart_for_migrations_that_wait_after_an_update(checkout):
    """Given a release rc.3 that ships a migration of docs, and docs still
    running rc.1, when the admin runs `stack doctor`, then its fix for the
    waiting migration is `stack restart docs`, which takes the snapshot
    first. `stack up docs` would be refused."""
    migrations = checkout.root / "stacklets" / "docs" / "migrations"
    migrations.mkdir()
    (migrations / "0001_move_tags.py").write_text(MIGRATION)
    checkout.release("v0.4.0-rc.3")

    ran = checkout.stack("doctor", "--json")

    findings = json.loads(ran.out)["findings"]
    waiting = [f for f in findings if "migrations waiting" in f["title"]]
    assert [f["fix"] for f in waiting] == ["stack restart docs"]


# ── The callers of `stack up` ────────────────────────────────────────────

def git_checkout(root: Path, stacklets: dict[str, dict[str, str]]) -> tuple[str, str]:
    """A git checkout at `root` with the files of `stacklets` in a first
    commit, and a second commit that changes each stacklet. Answers both
    short commits: the release a stacklet runs, and the checkout's."""
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Homer",
             "-c", "user.email=homer@example.com", "-c", "commit.gpgSign=false", *args],
            cwd=root, capture_output=True, text=True, check=True).stdout.strip()

    for sid, files in stacklets.items():
        for name, text in files.items():
            path = root / "stacklets" / sid / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "chore: release rc.1")
    before = git("rev-parse", "--short", "HEAD")
    for sid in stacklets:
        with (root / "stacklets" / sid / "stacklet.toml").open("a") as f:
            f.write("\n# changed by the next release\n")
    git("commit", "-q", "-am", "chore: release rc.2")
    return before, git("rev-parse", "--short", "HEAD")


def stack_in(root: Path, *args: str, env: dict[str, str]) -> Ran:
    """`./stack <args>` as a process of its own in `root`."""
    done = subprocess.run([sys.executable, "-m", "stack", *args], cwd=root,
                          env={**os.environ, "PYTHONPATH": str(REPO / "lib"), **env},
                          capture_output=True, text=True, timeout=STACK_SECONDS)
    return Ran(done.returncode, done.stdout, done.stderr)


def test_the_installer_on_a_core_of_another_release_names_the_restart(
        tmp_path, monkeypatch, docker_with_projects):
    """Given core running rc.1 and the checkout on rc.2, when the admin runs
    the install again (`stack install --family <file> --yes`), then it
    stops with the refusal, which names `stack restart core`, and does not
    advise `stack up core`, which would be refused again."""
    root = tmp_path / "checkout"
    before, _ = git_checkout(root, {"core": {
        "stacklet.toml": CORE_TOML, "docker-compose.yml": CORE_COMPOSE.format(tag="1")}})
    (root / "simpsons.toml").write_text('name = "Simpson"\n\n[[people]]\nname = "Homer Simpson"\n')
    (root / ".stack").mkdir()
    (root / ".stack" / "core.setup-done").touch()
    brew = tmp_path / "brew"
    brew.mkdir()
    (brew / "brew").write_text("#!/bin/sh\nexit 0\n")
    (brew / "brew").chmod(0o755)
    monkeypatch.setenv("PATH", str(brew), prepend=os.pathsep)
    monkeypatch.delenv("STACK_DIR", raising=False)
    docker = docker_with_projects(core="running")
    docker.commits(core=before)

    ran = stack_in(root, "install", "--family", "simpsons.toml", "--yes",
                   env={"HOME": str(tmp_path / "home")})

    assert ran.code == 1
    said = ran.out + ran.err
    assert "stack restart core" in said
    assert "stack up core" not in said
    assert "up" not in docker.compose_steps()


AI_TOML = """[core]
data_dir = "{data}"

[ai]
provider = ""
openai_url = "http://192.168.1.20:8000/v1"
openai_key = ""
default = "mlx-community/Qwen3.5-9B-MLX-8bit"
"""


def test_the_switch_to_the_managed_ai_on_another_release_says_to_restart_it_first(
        tmp_path, monkeypatch, docker_with_projects):
    """Given ai up from rc.1 (a stacklet without containers) and the
    checkout on rc.2, when the admin runs `stack ai switch managed`, then
    the switch fails in its own error, which names `stack restart ai`, and
    `[ai]` stays as it was: the stack keeps the server it used."""
    root = tmp_path / "checkout"
    shutil.copytree(REPO / "stacklets" / "ai", root / "stacklets" / "ai",
                    ignore=shutil.ignore_patterns("__pycache__"))
    before, _ = git_checkout(root, {"ai": {}})
    instance = tmp_path / "instance"
    (instance / ".stack").mkdir(parents=True)
    (instance / "stack.toml").write_text(AI_TOML.format(data=tmp_path / "data"))
    (instance / ".stack" / "ai.setup-done").touch()
    (instance / ".stack" / "ai.up").write_text(before + "\n")
    toml_before = (instance / "stack.toml").read_text()
    docker_with_projects()

    ran = stack_in(root, "ai", "switch", "managed", "--json",
                   env={"STACK_DIR": str(instance), "HOME": str(tmp_path / "home")})

    assert ran.code != 0
    answer = json.loads(ran.out)
    assert "stack restart ai" in answer["error"]
    assert (instance / "stack.toml").read_text() == toml_before


# ── Before the preparation ───────────────────────────────────────────────

ON_PREPARE = '''
from pathlib import Path


def run(ctx):
    Path({mark!r}).write_text("downloaded")
'''


def test_a_refused_up_runs_no_preparation(checkout):
    """Given a release rc.3 whose docs has a preparation (`on_prepare`,
    the hook of long downloads) and docs running rc.1, when the admin runs
    `stack up docs`, then it is refused before the preparation: nothing is
    downloaded. Once docs is stopped, the same `stack up docs` prepares it,
    so the hook does run where a start is allowed."""
    mark = checkout.root.parent / "prepared"
    hooks = checkout.root / "stacklets" / "docs" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "on_prepare.py").write_text(ON_PREPARE.format(mark=str(mark)))
    checkout.release("v0.4.0-rc.3")
    checkout.docker.forget()

    assert checkout.stack("up", "docs").code != 0
    assert not mark.exists()

    assert checkout.stack("down", "docs").code == 0
    assert checkout.stack("up", "docs").code == 0
    assert mark.exists()


def test_a_refusal_in_the_turn_posts_no_notice_either(checkout):
    """Given docs on the checkout's release, whose preparation leaves the
    record of a failed snapshot behind (a state that changed after `stack
    up` asked the guard), when the admin runs `stack up docs`, then the
    start in the run's turn is refused and the Server room gets no "failed
    to start" notice: docs runs as it did."""
    hooks = checkout.root / "stacklets" / "docs" / "hooks"
    hooks.mkdir(exist_ok=True)
    record = checkout.instance / ".stack" / "snapshot-pending" / "docs" / "MANIFEST.json"
    (hooks / "on_prepare.py").write_text(ON_PREPARE.format(mark=str(record)).replace(
        "def run(ctx):\n", "def run(ctx):\n    Path(" + repr(str(record.parent)) + ").mkdir(parents=True)\n"))
    checkout.release("v0.4.0-rc.3")
    rc3 = checkout.git("rev-parse", "--short", "v0.4.0-rc.3^{commit}")
    checkout.docker.commits(docs=rc3, photos=rc3)
    checkout.docker.forget()
    log = chat_log(checkout)

    ran = checkout.stack("up", "docs")

    assert ran.code != 0
    assert record.exists()
    assert "stack restart docs" in ran.out + ran.err
    assert notices(log) == []


def test_up_of_a_refused_and_a_stopped_stacklet_starts_the_stopped_one(checkout):
    """Given docs running rc.1, photos stopped, and the checkout on rc.2,
    when the admin runs `stack up docs photos`, then photos starts, docs
    is named with `stack restart docs`, and the command fails: one refusal
    does not hold up the other start."""
    assert checkout.stack("down", "photos").code == 0
    checkout.docker.forget()

    ran = checkout.stack("up", "docs", "photos")

    assert ran.code == 1
    assert checkout.docker.state_of("photos") == "running"
    assert "stack restart docs" in ran.out + ran.err


def test_a_bare_restart_whose_stop_fails_says_so_and_starts_nothing(checkout):
    """Given docs and photos on rc.1 and docs' stop failing, when the
    admin runs `stack restart --no-snapshot` (the restart of what runs
    stale code), then it ends with the stop's error and starts nothing."""
    checkout.docker.fail("stop", "container is busy", stacklet="docs")
    checkout.docker.forget()

    ran = checkout.stack("restart", "--no-snapshot")

    assert ran.code == 1
    assert "up" not in checkout.docker.compose_steps()
    assert "container is busy" in ran.out + ran.err


# ── `stack ai key new` ───────────────────────────────────────────────────

KEY_TOML = """[core]
data_dir = "{data}"

[ai]
provider = "managed"
openai_url = "http://127.0.0.1:{port}/v1"
openai_key = "local"
default = "mlx-community/Qwen3.5-9B-MLX-8bit"
"""

CONSUMER_TOML = """id = "memory"
name = "Memory"
category = "test"

[env.defaults]
MEMORY_AI_URL = "{ai_openai_url}"
"""


def test_a_new_ai_key_waits_for_the_restart_of_a_consumer_on_another_release(
        tmp_path, docker_with_projects):
    """Given memory, which renders the AI's key, running rc.1 and the
    checkout on rc.2, when the admin runs `stack ai key new`, then it
    refuses before it makes a key and names `stack restart memory`: the
    start that hands memory the key would be refused, and memory would
    keep the old key while oMLX takes only the new one. `[ai] openai_key`
    and oMLX's settings stay as they were."""
    from tests.loopback import a_closed_port

    root = tmp_path / "checkout"
    shutil.copytree(REPO / "stacklets" / "ai", root / "stacklets" / "ai",
                    ignore=shutil.ignore_patterns("__pycache__"))
    before, _ = git_checkout(root, {"ai": {}, "memory": {"stacklet.toml": CONSUMER_TOML}})
    instance, data = tmp_path / "instance", tmp_path / "data"
    (instance / ".stack").mkdir(parents=True)
    (instance / "stack.toml").write_text(KEY_TOML.format(data=data, port=a_closed_port()))
    (instance / ".stack" / "ai.setup-done").touch()
    settings = data / "ai" / "omlx" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"auth": {"api_key": "local"}}))
    toml_before = (instance / "stack.toml").read_text()
    docker = docker_with_projects(memory="running")
    docker.commits(memory=before)

    ran = stack_in(root, "ai", "key", "new", "--json",
                   env={"STACK_DIR": str(instance), "HOME": str(tmp_path / "home")})

    assert ran.code != 0, ran.out
    assert "stack restart memory" in json.loads(ran.out)["error"]
    assert (instance / "stack.toml").read_text() == toml_before
    assert json.loads(settings.read_text()) == {"auth": {"api_key": "local"}}


@pytest.mark.parametrize("named", [["docs", "photos"], ["all"]])
def test_a_restart_of_several_whose_stop_fails_says_so_and_starts_nothing(checkout, named):
    """Given docs and photos on rc.1 and docs' stop failing, when the
    admin runs `stack restart docs photos --no-snapshot` or `stack restart
    all --no-snapshot`, then it ends with docs' stop error and starts
    nothing: a start of docs, still running, would only be refused."""
    checkout.docker.fail("stop", "container is busy", stacklet="docs")
    checkout.docker.forget()

    ran = checkout.stack("restart", *named, "--no-snapshot")

    assert ran.code == 1
    assert "up" not in checkout.docker.compose_steps()
    assert "  ✗  Some stacklets failed to stop\n\n      docs: container is busy\n" in ran.err


# ── The run that holds a stacklet ────────────────────────────────────────

def test_a_run_refused_by_a_snapshot_command_names_what_holds_it():
    """Given a stacklet held by a rollback or by `stack snapshot remove`,
    when another run of it is refused, then the message names that
    command's action, not a bare "busy". The action's name belongs to its
    command, so a rename there must not leave the message behind."""
    from stack import runs
    from stack.snapshot import remove_cli, rollback_record

    for action in (rollback_record.RUN_ACTION, remove_cli.RUN_ACTION):
        refused = str(runs.AlreadyRunning("docs", {"action": action}))

        assert refused.startswith("docs is already "), action
        assert "busy" not in refused, action
