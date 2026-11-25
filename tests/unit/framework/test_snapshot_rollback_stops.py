# ruff: noqa: F811  (the imported fixture `updated` is the tests' parameter)
"""`stack rollback` stopped at each step: what is on disk, and what starts.

A rollback restores each stacklet's state before it moves the checkout to
the old release. Until every state is back, the new release's code stays
on disk, and with it `stack rollback`, which finishes the run, and the
guard of `stack up`, which refuses the stacklets meanwhile. Once the
checkout moved, every state is whole, so the old release's own `stack up`
starts the stacklets safely, also a release that has no `stack rollback`.

The tests stop a rollback in two ways. A failure at a step (a folder it
cannot write, a snapshot file it cannot read, a tag Docker refuses, a git
lock) ends the run there, as `test_snapshot_rollback.py` does. A power loss
is a hook in the test's git checkout that kills the `stack rollback`
process: `post-checkout` right after git moved the checkout, and
`reference-transaction` inside the checkout, after git wrote the files and
before it moved HEAD. The `post-checkout` hook also writes down what the
record and the data folders hold at the moment of the checkout, which
shows the order of the steps.

Every `./stack` of a test runs this release's code. How a release without
the record starts after a stop is proven by the rollback drill, on a build
of a release without `stack rollback`.

The instance is the one of `snapshot_instance.py`: rc.2 running,
the snapshot of rc.1, and the family's changes since. Each test runs
three to six `./stack` processes, about 2 seconds.
"""

from __future__ import annotations

import json
import shutil
import sys

import pytest

from stack.snapshot import rollback_record

from tests.unit.framework.snapshot_instance import (  # noqa: F401 (the fixtures)
    ALBUMS,
    Checkout,
    NEWER_IMAGE_ID,
    OLD_IMAGE,
    checkout_fails,
    clone_fails,
    head,
    move_fails,
    replaced_folder,
    rollback,
    rolled_back,
    tag_fails,
    updated,
)


# A git hook. It writes one JSON line: the steps the record names as
# done, the restored files and how many Docker commands ran so far. With
# `kill` set, it then ends the `stack rollback` process (git's parent) and
# git as a power loss would. `when` says on which call: a reference-transaction
# hook runs once per state of git's ref update, and "prepared" comes
# before HEAD moves.
HOOK = """#!{python}
import glob, json, os, signal, subprocess, sys
if not ({when}):
    sys.exit(0)
data, docker_log, out = {data!r}, {docker_log!r}, {out!r}
records = glob.glob(os.path.join(data, ".snapshots", "replaced", "*", "ROLLBACK.json"))
def text(path):
    try:
        return open(path).read()
    except OSError:
        return None
seen = {{
    "done": json.loads(open(records[0]).read())["done"] if records else None,
    "docs": text(os.path.join(data, "docs", "postgres", "base.dat")),
    "photos": text(os.path.join(data, "photos", "albums.json")),
    "docker": len(open(docker_log).read().splitlines()) if os.path.exists(docker_log) else 0,
}}
with open(out, "a") as log:
    log.write(json.dumps(seen) + "\\n")
if {kill}:
    git = os.getppid()
    stack = int(subprocess.run(["ps", "-o", "ppid=", "-p", str(git)],
                               capture_output=True, text=True).stdout.strip())
    os.kill(stack, signal.SIGKILL)
    os.kill(git, signal.SIGKILL)
"""


def on_checkout(checkout: Checkout, kill: bool = False, inside: bool = False):
    """Install the hook that writes down the state at the checkout, and
    return the file it writes to. `inside` runs it inside git's checkout,
    before HEAD moves, instead of after it."""
    seen = checkout.root.parent / "at-checkout.jsonl"
    name, when = (("reference-transaction", 'sys.argv[1] == "prepared"') if inside
                  else ("post-checkout", "True"))
    hook = checkout.root / ".git" / "hooks" / name
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(HOOK.format(python=sys.executable, data=str(checkout.data),
                                docker_log=str(checkout.docker.folder / "commands.log"),
                                out=str(seen), kill=kill, when=when))
    hook.chmod(0o755)
    checkout.git("config", "core.hooksPath", ".git/hooks")
    return seen


def at_checkout(seen) -> dict:
    """What the one checkout of a rollback saw."""
    [line] = seen.read_text().splitlines()
    return json.loads(line)


def record(checkout: Checkout) -> list[str]:
    """The steps the record of the one rollback names as done."""
    return json.loads((replaced_folder(checkout) / "ROLLBACK.json").read_text())["done"]


def held(checkout: Checkout, stacklet_id: str) -> dict:
    """The answer of the start that the rollback's record refuses: `stack
    up <id>` refuses, and its answer is the record's `why_held`. The events
    of `stack up` show screens only the message, so the test reads the
    answer's `state` from the record module."""
    refused = checkout.stack("up", stacklet_id)
    assert refused.code != 0
    folder = rollback_record.holding(checkout.data, stacklet_id, checkout.root)
    assert folder is not None
    answer = rollback_record.why_held(folder, stacklet_id, checkout.root)
    assert answer["error"] in refused.out + refused.err
    return answer


def every_state_back(checkout: Checkout) -> None:
    """Each data folder holds the snapshot's state."""
    assert (checkout.data / "docs" / "postgres" / "base.dat").read_text() == "homer's invoices"
    assert (checkout.data / "photos" / "albums.json").read_text() == ALBUMS


# ── The order ────────────────────────────────────────────────────────────

def test_the_checkout_comes_after_every_restore_and_tag_and_before_the_start(updated):
    """Given a rollback that moves a tag, when it runs, then at the
    moment of the checkout every state is back, the record names each
    restore, the stacklets are stopped and the tag is moved, and no
    stacklet started yet: the old release's code never meets a state that
    is not whole."""
    updated.docker.tags(**{OLD_IMAGE: NEWER_IMAGE_ID})
    seen = on_checkout(updated)

    ran = rollback(updated)

    assert ran.code == 0, ran.err
    checked_out = at_checkout(seen)
    assert {"moved docs", "restored docs", "moved photos", "restored photos"} \
        <= set(checked_out["done"])
    assert "starting" not in checked_out["done"]
    assert checked_out["docs"] == "homer's invoices"
    assert checked_out["photos"] == ALBUMS
    before = updated.docker.commands()[:checked_out["docker"]]
    after = updated.docker.commands()[checked_out["docker"]:]
    assert any("stop" in argv for argv in before)
    assert any(argv[:2] == ["image", "tag"] for argv in before)
    assert not any("up" in argv for argv in before)
    assert any("up" in argv for argv in after)
    rolled_back(updated, set())


# ── A stop before the checkout ───────────────────────────────────────────

BEFORE_THE_CHECKOUT = [("move aside", move_fails), ("clone", clone_fails),
                       ("tag", tag_fails)]


@pytest.mark.parametrize("step,stops_it", BEFORE_THE_CHECKOUT,
                         ids=[s[0] for s in BEFORE_THE_CHECKOUT])
def test_a_stop_before_the_checkout_keeps_the_new_release_and_holds_the_stacklets(
        updated, step, stops_it):
    """Given a rollback that stops before the checkout (a folder it cannot
    write, a snapshot file it cannot read, a tag Docker refuses), when the
    admin runs `stack up`, then it refuses each stacklet of the rollback,
    because the checkout is still the new release's, which has `stack
    rollback`; that command then finishes the rollback."""
    fixed = stops_it(updated)
    try:
        assert rollback(updated).code == 1
    finally:
        fixed()
    updated.docker.forget()

    assert head(updated) == updated.rc2
    for sid in ("docs", "photos"):
        refused = updated.stack("up", sid)
        assert refused.code != 0
        assert "stack rollback" in refused.out + refused.err
    assert "up" not in updated.docker.compose_steps()

    ran = rollback(updated)

    assert ran.code == 0, ran.err
    rolled_back(updated, {"photos.2"} if step == "clone" else set())


# ── A stop after the checkout ────────────────────────────────────────────

def test_a_power_loss_after_the_checkout_leaves_whole_states_held_until_the_rollback(updated):
    """Given a rollback whose process ends right after the checkout (a
    power loss), then every state is whole and the checkout is the old
    release's. A release that knows the record still refuses `stack up`,
    because "starting" is not written yet, and its `stack rollback`
    finishes the run and starts the stacklets.

    This release's code runs here, so the test cannot show the start of a
    release that does not know the record; the rollback drill does.
    """
    on_checkout(updated, kill=True)

    killed = rollback(updated)

    assert killed.code != 0
    assert head(updated) == updated.rc1
    every_state_back(updated)
    done = record(updated)
    assert {"restored docs", "restored photos"} <= set(done)
    assert "starting" not in done
    updated.docker.forget()
    assert held(updated, "docs")["state"] == "whole"
    assert "up" not in updated.docker.compose_steps()

    (updated.root / ".git" / "hooks" / "post-checkout").unlink()
    ran = rollback(updated)

    assert ran.code == 0, ran.err
    every_state_back(updated)
    assert "finished" in record(updated)
    assert updated.docker.state_of("docs") == "running"
    assert updated.docker.state_of("photos") == "running"
    assert updated.stack("up", "docs").code == 0


def test_a_power_loss_inside_the_checkout_keeps_the_stacklets_held(updated):
    """Given a rollback whose process ends inside git's checkout, after git
    wrote the old release's files and before it moved HEAD, when the admin
    runs `stack up`, then it refuses each stacklet: the new release's code
    is still named by HEAD, and it must not start on the restored old
    state. `stack rollback` then refuses git's half checkout as local
    edits. Once the admin removes the lock the killed git left and runs
    `git checkout --force` of the release they came from, it finishes."""
    on_checkout(updated, kill=True, inside=True)

    killed = rollback(updated)

    assert killed.code != 0
    assert head(updated) == updated.rc2
    every_state_back(updated)
    assert "starting" not in record(updated)
    updated.docker.forget()
    for sid in ("docs", "photos"):
        assert updated.stack("up", sid).code != 0
    assert "up" not in updated.docker.compose_steps()

    (updated.root / ".git" / "hooks" / "reference-transaction").unlink()
    refused = rollback(updated)
    assert refused.code == 1
    assert "stacklets/docs/docker-compose.yml" in json.loads(refused.out)["problems"]

    # What git left: the lock of HEAD, and the snapshot's release's files.
    lock = updated.root / ".git" / "HEAD.lock"
    assert lock.exists()
    assert updated.git("diff", "--quiet", "v0.4.0-rc.1", "--", "stacklets") == ""

    # The way on the admin guide names: the lock goes, and the checkout
    # goes back to the release the admin came from.
    lock.unlink()
    updated.git("checkout", "--force", "v0.4.0-rc.2")
    ran = rollback(updated)

    assert ran.code == 0, ran.out
    every_state_back(updated)
    assert head(updated) == updated.rc1
    assert "finished" in record(updated)


def test_the_refusal_says_whether_a_state_may_be_half_restored(updated):
    """Given a rollback that stopped at the restore of photos, after docs
    was back, when the admin runs `stack up` for each, then both refuse,
    and the answer says what is true: photos' state may be half restored,
    docs' state is whole and waits only for the rollback to end."""
    fixed = clone_fails(updated)
    try:
        assert rollback(updated).code == 1
    finally:
        fixed()

    assert held(updated, "photos")["state"] == "half restored"
    assert held(updated, "docs")["state"] == "whole"


def test_a_checkout_git_refuses_after_the_restore_starts_nothing(updated):
    """Given a git lock that makes the checkout fail after every state is
    back, when the rollback runs, then nothing starts, the record names
    each restore and no later step, the message says to run `stack
    rollback` again, and `stack up` refuses the stacklets until then: the
    new release's code must not start on the old state."""
    fixed = checkout_fails(updated)
    try:
        ran = rollback(updated)
    finally:
        fixed()

    assert ran.code == 1
    assert "stack rollback" in ran.out + ran.err
    steps = updated.docker.compose_steps()
    assert "stop" in steps and "up" not in steps
    assert updated.docker.state_of("docs") != "running"
    assert head(updated) == updated.rc2
    done = record(updated)
    assert done[-1] == "restored photos"
    every_state_back(updated)
    assert updated.stack("up", "docs").code != 0

    ran = rollback(updated)

    assert ran.code == 0, ran.err
    rolled_back(updated, set())


# ── A stop in the start, then another release ────────────────────────────

def stopped_in_the_start_then_updated(checkout: Checkout) -> None:
    """A rollback whose start failed after the checkout, so its record
    says "starting". Then `stack update` moved the checkout on to rc.2."""
    checkout.docker.fail("up", "port is already allocated")
    try:
        assert rollback(checkout).code == 1
    finally:
        checkout.docker.fail("up", "")
    assert "starting" in record(checkout)
    assert head(checkout) == checkout.rc1
    moved = checkout.stack("update", "v0.4.0-rc.2", "--yes")
    assert moved.code == 0, moved.err
    assert head(checkout) == checkout.rc2
    checkout.docker.forget()


def test_a_finish_on_another_release_refuses_and_names_the_way_back(updated):
    """Given a rollback that stopped in its start, and the checkout moved
    on to rc.2 since, when the admin runs `stack rollback`, then it
    refuses and starts nothing: rc.2 would migrate the snapshot's state
    with no snapshot of it. The answer names the snapshot's release and
    the command that goes back to it."""
    stopped_in_the_start_then_updated(updated)

    refused = rollback(updated)

    assert refused.code == 1
    error = json.loads(refused.out)["error"]
    assert "0.4.0-rc.1" in error
    assert "stack update v0.4.0-rc.1" in error
    assert "up" not in updated.docker.compose_steps()
    assert head(updated) == updated.rc2
    assert "finished" not in record(updated)


def test_up_refuses_the_stacklets_of_a_finish_on_another_release(updated):
    """Given the same rollback on rc.2, when the admin runs `stack up` of
    one of its stacklets, then it refuses too, with the same way back,
    because the record still holds the stacklet."""
    stopped_in_the_start_then_updated(updated)

    for sid in ("docs", "photos"):
        refused = updated.stack("up", sid)
        assert refused.code != 0
        assert "stack update v0.4.0-rc.1" in refused.out + refused.err
    assert "up" not in updated.docker.compose_steps()


def test_a_finish_back_on_the_snapshots_release_ends_the_rollback(updated):
    """Given the same rollback, and the checkout back on rc.1 with the
    command the refusal named, when the admin runs `stack rollback`, then
    it starts the stacklets and ends, as a finish does."""
    stopped_in_the_start_then_updated(updated)
    back = updated.stack("update", "v0.4.0-rc.1", "--yes")
    assert back.code == 0, back.err

    ran = rollback(updated)

    assert ran.code == 0, ran.err
    rolled_back(updated, set())


def test_a_finish_whose_snapshot_is_gone_names_the_way_out(updated):
    """Given a rollback that stopped in its start, on the snapshot's
    release, and its snapshot deleted by hand since, when the admin runs
    `stack rollback` or `stack up`, then both refuse, because the stack
    cannot tell the snapshot's commit. The answer names the way out: on
    the snapshot's release, rename the record and start the stacklets.
    After that, `stack up` starts them."""
    updated.docker.fail("up", "port is already allocated")
    try:
        assert rollback(updated).code == 1
    finally:
        updated.docker.fail("up", "")
    [name] = [n for n in updated.snapshot_folders() if n != "replaced"]
    shutil.rmtree(updated.data / ".snapshots" / name)
    updated.docker.forget()

    refused = rollback(updated)
    held_up = updated.stack("up", "docs")

    assert refused.code == 1
    error = json.loads(refused.out)["error"]
    path = replaced_folder(updated) / "ROLLBACK.json"
    assert "0.4.0-rc.1" in error
    assert f"mv {path} {path}.ended" in error
    assert "stack up docs photos" in error
    assert held_up.code != 0
    assert f"mv {path} {path}.ended" in held_up.out + held_up.err
    assert "up" not in updated.docker.compose_steps()

    path.rename(path.with_name("ROLLBACK.json.ended"))
    started = updated.stack("up", "docs")

    assert started.code == 0, started.err
    assert updated.docker.state_of("docs") == "running"
