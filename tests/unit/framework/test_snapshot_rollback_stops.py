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
import os
import re
import shlex
import shutil
import subprocess
import sys

import pytest

from stack.snapshot import rollback_record

from tests.unit.framework.snapshot_instance import (  # noqa: F401 (the fixtures)
    ALBUMS,
    Checkout,
    NEWER_IMAGE_ID,
    QUOTED,
    QUOTED_TEXT,
    RC1_ONLY,
    README,
    REPO,
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
    updated_with_a_quoted_name,
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
    edits. The steps of the admin guide, run as written on the real git
    checkout, then finish it."""
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

    ran = follow_the_guide(updated)

    assert ran.code == 0, ran.out
    every_state_back(updated)
    assert head(updated) == updated.rc1
    assert "finished" in record(updated)


# A smudge filter. Git runs it for each file it writes into the worktree,
# before it writes the index. For the file `halt_at` it ends the `stack
# rollback` process and git, as a power loss would; it passes every other
# file through. It finds git among its ancestors, because git may start
# it through a shell.
HALT = """#!{python}
import os, shutil, signal, subprocess, sys
if sys.argv[1] != {halt_at!r}:
    shutil.copyfileobj(sys.stdin.buffer, sys.stdout.buffer)
    sys.exit(0)
pid = os.getppid()
while pid > 1:
    ppid, comm = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                capture_output=True, text=True).stdout.split(None, 1)
    if os.path.basename(comm.strip()) == "git":
        os.kill(int(ppid), signal.SIGKILL)
        os.kill(pid, signal.SIGKILL)
        break
    pid = int(ppid)
sys.exit(1)
"""


def halt_while_git_writes(checkout: Checkout, halt_at: str):
    """Make the next checkout stop while git writes `halt_at`: after the
    files before it, before the index. Returns what undoes it."""
    script = checkout.root.parent / "halt.py"
    script.write_text(HALT.format(python=sys.executable, halt_at=halt_at))
    script.chmod(0o755)
    attributes = checkout.root / ".git" / "info" / "attributes"
    attributes.parent.mkdir(exist_ok=True)
    attributes.write_text("* filter=halt\n")
    checkout.git("config", "filter.halt.smudge", f"{script} %f")

    def undo() -> None:
        attributes.unlink()
        checkout.git("config", "--unset", "filter.halt.smudge")
    return undo


def test_a_stop_before_git_wrote_the_index_ends_with_the_guides_steps(updated):
    """Given a rollback whose git stopped while it wrote the old release's
    files, before it wrote the index: git left `index.lock`, and a file
    that only the snapshot's release has, which the index does not know.
    When the admin follows the admin guide's steps as written, then the
    rollback ends on the snapshot's release. A file the admin made and an
    ignored file in the checkout stay as they were."""
    note = updated.root / "homer-todo.txt"
    note.write_text("call the dentist")
    env = updated.root / ".env"
    env.write_text("SECRET=donuts")
    undo = halt_while_git_writes(updated, "stacklets/photos/docker-compose.yml")

    killed = rollback(updated)
    undo()

    assert killed.code != 0
    assert (updated.root / ".git" / "index.lock").exists()
    assert head(updated) == updated.rc2
    left = updated.root / "stacklets" / "docs" / "NOTES.md"
    assert left.read_text() == RC1_ONLY
    assert "starting" not in record(updated)
    updated.docker.forget()

    ran = follow_the_guide(updated)

    assert ran.code == 0, ran.out
    every_state_back(updated)
    assert head(updated) == updated.rc1
    assert left.read_text() == RC1_ONLY
    assert "finished" in record(updated)
    assert note.read_text() == "call the dentist"
    assert env.read_text() == "SECRET=donuts"
    assert updated.git("status", "--porcelain") == "?? homer-todo.txt"


def test_the_checkout_never_removes_a_file_git_tracks(updated):
    """Given an untracked file whose name starts with a space, " README.md",
    next to the tracked README.md that both releases share, when the
    rollback checks out the snapshot's release, then both files stay as
    they were: a name is read as git writes it, and a tracked file is
    never git's leftover."""
    spaced = updated.root / " README.md"
    spaced.write_text("homer's copy")

    ran = rollback(updated)

    assert ran.code == 0, ran.out
    assert head(updated) == updated.rc1
    assert (updated.root / "README.md").read_text() == README
    assert spaced.read_text() == "homer's copy"
    assert updated.git("status", "--porcelain") == '?? " README.md"'


def test_a_link_in_the_way_is_not_a_copy_of_the_snapshots_file(updated):
    """Given an untracked symbolic link at the path of the file that only
    the snapshot's release has, pointing at a file with that file's text,
    when the rollback checks out, then the link stays: only a regular file
    is a copy of a regular file. Git refuses the checkout and names the
    path, and nothing starts."""
    outside = updated.root.parent / "notes-elsewhere.md"
    outside.write_text(RC1_ONLY)
    link = updated.root / "stacklets" / "docs" / "NOTES.md"
    os.symlink(outside, link)

    ran = rollback(updated)

    assert ran.code == 1
    assert "stacklets/docs/NOTES.md" in json.loads(ran.out)["error"]
    assert link.is_symlink()
    assert outside.read_text() == RC1_ONLY
    assert head(updated) == updated.rc2
    assert "up" not in updated.docker.compose_steps()


def test_a_name_in_quotes_is_never_read_as_another_file(updated_with_a_quoted_name):
    """Given an untracked file named "q2" (with the quotes) holding text of
    its own, and an untracked q2 that holds the snapshot release's text of
    "q2", when the rollback checks out, then "q2" stays: git's line input
    would read its name as q2. Git refuses the checkout and names it."""
    updated = updated_with_a_quoted_name
    quoted = updated.root / QUOTED
    quoted.write_text("homer's own q2")
    (updated.root / "q2").write_text(QUOTED_TEXT)

    ran = rollback(updated)

    assert ran.code == 1
    assert QUOTED in json.loads(ran.out)["error"]
    assert quoted.read_text() == "homer's own q2"
    assert head(updated) == updated.rc2


def test_a_copy_with_another_exec_bit_stays(updated):
    """Given an untracked NOTES.md with the snapshot release's text, but
    executable where the release's file is not, when the rollback checks
    out, then the file stays with its mode: only a file with the
    release's mode is git's leftover. Git refuses and names it."""
    notes = updated.root / "stacklets" / "docs" / "NOTES.md"
    notes.write_text(RC1_ONLY)
    notes.chmod(0o755)

    ran = rollback(updated)

    assert ran.code == 1
    assert "stacklets/docs/NOTES.md" in json.loads(ran.out)["error"]
    assert notes.stat().st_mode & 0o111
    assert head(updated) == updated.rc2


def follow_the_guide(checkout: Checkout):
    """Run the steps of the admin guide's "After a stop inside the
    checkout" as written. Returns the answer of its `./stack rollback`."""
    ran = None
    for step in guide_steps("After a stop inside the checkout"):
        ran = run_step(checkout, step) or ran
    assert ran is not None, "the guide's steps name no ./stack rollback"
    return ran


def guide_steps(heading: str) -> list[str]:
    """The command of each numbered step under the admin guide's `####
    <heading>`: the first code span of the step, which the guide writes
    as "Run `<command>`."."""
    guide = (REPO / "docs" / "admin-guide.md").read_text()
    section = guide.split(f"#### {heading}\n", 1)[1].split("\n#", 1)[0]
    steps = [re.search(r"`([^`]+)`", line).group(1)
             for line in section.splitlines() if re.match(r"\d+\. ", line)]
    assert steps, f"no numbered steps under {heading}"
    return steps


def run_step(checkout: Checkout, step: str):
    """Run one step of the guide in the checkout, as the admin types it.
    `./stack rollback` runs as a script runs it (`--yes --json`)."""
    argv = shlex.split(step)
    if argv[:2] == ["./stack", "rollback"]:
        return rollback(checkout, *argv[2:])
    if argv[0] == "git":
        checkout.git(*argv[1:])
        return None
    subprocess.run(argv, cwd=checkout.root, check=True)
    return None


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


def stopped_in_the_start(checkout: Checkout) -> None:
    """A rollback whose start failed after the checkout: its record says
    "starting", and the checkout is on the snapshot's release."""
    checkout.docker.fail("up", "port is already allocated")
    try:
        assert rollback(checkout).code == 1
    finally:
        checkout.docker.fail("up", "")
    assert "starting" in record(checkout)
    checkout.docker.forget()


def test_the_way_out_of_a_gone_snapshot_says_what_another_release_does(updated):
    """Given a rollback that stopped in its start and its snapshot deleted
    by hand, when `stack rollback` refuses, then the answer says what
    happens when the admin renames the record on another release: its
    migrations run on the restored state without a snapshot. It also names
    the command that shows the checkout's release."""
    stopped_in_the_start(updated)
    [name] = [n for n in updated.snapshot_folders() if n != "replaced"]
    shutil.rmtree(updated.data / ".snapshots" / name)

    error = json.loads(rollback(updated).out)["error"]

    assert ("On another release, the start runs that release's migrations on the restored "
            "state, one way and without a snapshot.") in error
    assert "stack version" in error


def test_a_snapshot_commit_git_does_not_know_names_git_fetch(updated):
    """Given a rollback that stopped in its start, and a snapshot whose
    commit this checkout's git does not know, when the admin runs `stack
    rollback`, then it refuses, starts nothing and names `git fetch
    --tags`."""
    stopped_in_the_start(updated)
    [name] = [n for n in updated.snapshot_folders() if n != "replaced"]
    manifest = updated.data / ".snapshots" / name / "MANIFEST.json"
    written = json.loads(manifest.read_text())
    written["commit"] = "0123456789abcdef0123456789abcdef01234567"
    manifest.write_text(json.dumps(written))

    refused = rollback(updated)

    assert refused.code == 1
    assert "git fetch --tags" in json.loads(refused.out)["error"]
    assert "up" not in updated.docker.compose_steps()
