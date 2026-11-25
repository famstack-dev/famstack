# ruff: noqa: F811  (the imported fixtures are the tests' parameters)
"""`stack rollback`, as the admin runs it after an update that broke a page.

The admin ran `stack update` to rc.2 and `stack restart`. The restart took
a snapshot of `docs` and `photos` while they were stopped, then started
them on rc.2. Since then the family filed a letter (the database changed)
and scanned a new bill (a new file in docs' `[[backup.archive]]` source).
`stack rollback` goes back to the snapshot: it stops the stacklets, moves
their current state aside, clones the snapshot back, checks out the
snapshot's commit, and starts them on the images they ran before.

The instance, the checkout and the Docker stand-in are those of
`snapshot_instance.py`, shared by the snapshot tests: a git checkout with the releases rc.1 and
rc.2, a data folder and an instance folder in the test's folder, and
`docker_with_projects`, which follows `compose up` and `compose stop` and
answers `image inspect` and `image tag`. Every `./stack` command is a
process of its own, as the admin runs it, and `stack up` at the end of a
rollback is one more. A test runs three to six of them, about 2 seconds.
"""

from __future__ import annotations

import json

import pytest

from tests.unit.framework.snapshot_instance import (  # noqa: F401 (the fixtures)
    ALBUMS,
    Checkout,
    LETTER,
    NEWER_IMAGE_ID,
    NEW_ALBUMS,
    OLD_IMAGE,
    OLD_IMAGE_ID,
    PHOTOS_IMAGE,
    PHOTOS_IMAGE_ID,
    checkout_fails,
    clone_fails,
    everything_in,
    head,
    in_terminal,
    move_fails,
    new_checkout,
    replaced_folder,
    replaced_folders,
    rollback,
    rolled_back,
    stopped_at_the_clone_of_docs,
    tag_fails,
    tags_set,
    updated,
)



def nothing_changed(checkout: Checkout, data_before: list[str], head_before: str) -> None:
    assert everything_in(checkout.data) == data_before
    assert head(checkout) == head_before
    assert checkout.docker.compose_steps() == []
    assert tags_set(checkout) == []


# ── The plan and the confirmation ────────────────────────────────────────

def test_the_plan_names_the_snapshot_the_release_and_what_it_replaces(updated):
    """Given the snapshot before the update, when the admin runs `stack
    rollback` and declines, then the stack has shown the snapshot, the
    release it goes back to and each stacklet it replaces, and changed
    nothing: the family's changes since are still where they were."""
    [name] = updated.snapshot_folders()
    data_before, head_before = everything_in(updated.data), head(updated)

    ran = in_terminal(updated, "rollback", answer="n")

    assert ran.code == 0, ran.err
    assert name in ran.out
    assert "0.4.0-rc.1" in ran.out
    assert "docs" in ran.out and "photos" in ran.out
    assert "replaced" in ran.out
    nothing_changed(updated, data_before, head_before)


def test_without_a_terminal_and_without_yes_it_refuses(updated):
    """Given no terminal to ask in, when a script runs `stack rollback`
    without `--yes`, then it refuses and changes nothing."""
    data_before, head_before = everything_in(updated.data), head(updated)

    ran = updated.stack("rollback")

    assert ran.code == 1
    assert "--yes" in ran.err
    nothing_changed(updated, data_before, head_before)


# ── The rollback ─────────────────────────────────────────────────────────

def test_the_rollback_moves_the_state_aside_restores_the_snapshot_and_starts_it(updated):
    """Given the confirmation, when the rollback runs, then docs was
    stopped, its current state lies whole under `replaced/<time>/docs/`,
    its data folder holds the snapshot's state, the checkout is on the
    snapshot's commit, and docs runs again."""
    ran = rollback(updated)

    assert ran.code == 0, ran.err
    answer = json.loads(ran.out)
    [time] = replaced_folders(updated)
    replaced = updated.data / ".snapshots" / "replaced" / time
    assert (replaced / "docs" / "postgres" / "base.dat").read_text() == LETTER
    assert (updated.data / "docs" / "postgres" / "base.dat").read_text() == "homer's invoices"
    assert head(updated) == updated.rc1
    steps = updated.docker.compose_steps()
    assert "stop" in steps and steps[-1] == "up"
    assert updated.docker.state_of("docs") == "running"
    assert sorted(answer["started"]) == ["docs", "photos"]
    assert answer["replaced"] == str(replaced)
    assert (replaced / "photos" / "albums.json").read_text() == NEW_ALBUMS
    assert (updated.data / "photos" / "albums.json").read_text() == ALBUMS
    # Each tag names the snapshot's image already, so none moves.
    assert tags_set(updated) == []
    assert answer["moved_tags"] == []


def test_after_the_rollback_the_stack_names_the_release_of_the_snapshot(updated):
    """Given a rollback that ended, when the admin runs `stack version`,
    then it names rc.1, the release of the snapshot."""
    assert rollback(updated).code == 0

    ran = updated.stack("version")

    assert "0.4.0-rc.1" in ran.out
    assert "rc.2" not in ran.out


def test_the_snapshot_stays_after_the_rollback(updated):
    """Given a rollback that ended, then the snapshot is still listed and
    still holds its state: the rollback cloned it, it did not move it."""
    [name] = updated.snapshot_folders()

    assert rollback(updated).code == 0

    listed = json.loads(updated.stack("snapshot", "list", "--json").out)
    assert [s["name"] for s in listed["snapshots"]] == [name]
    snapshot = updated.data / ".snapshots" / name
    assert (snapshot / "docs" / "postgres" / "base.dat").read_text() == "homer's invoices"


def test_the_rollback_starts_the_image_the_snapshot_names_not_the_tags_newer_one(updated):
    """Given that the tag of rc.1 now names a newer image, when the
    rollback runs, then the tag names the image docs ran before again, and
    it was set before the start: the old database never meets newer code."""
    updated.docker.tags(**{OLD_IMAGE: NEWER_IMAGE_ID})

    ran = rollback(updated)

    assert ran.code == 0, ran.err
    assert updated.docker.tagged(OLD_IMAGE) == OLD_IMAGE_ID
    assert tags_set(updated) == [["image", "tag", OLD_IMAGE_ID, OLD_IMAGE]]
    assert json.loads(ran.out)["moved_tags"] == [OLD_IMAGE]
    commands = updated.docker.commands()
    tagged = next(i for i, argv in enumerate(commands) if argv[:2] == ["image", "tag"])
    started = max(i for i, argv in enumerate(commands) if argv[:1] == ["compose"] and "up" in argv)
    assert tagged < started


# ── Files that came after the snapshot ───────────────────────────────────

def test_files_newer_than_the_snapshot_stay_and_are_counted(updated):
    """Given a new scan in docs' archive source, made after the snapshot,
    when the rollback ends, then the scan and the older originals stay
    where they are, nothing of the source was moved aside, and the answer
    names how many newer files there are and where."""
    ran = rollback(updated)

    assert ran.code == 0, ran.err
    media = updated.data / "docs" / "paperless" / "media"
    assert sorted(p.name for p in media.iterdir()) == ["dentist-bill.pdf", "new-bill.pdf"]
    [time] = replaced_folders(updated)
    assert not (updated.data / ".snapshots" / "replaced" / time / "docs" / "paperless" / "media").exists()
    assert json.loads(ran.out)["newer_files"] == {"docs": {"paperless/media": 1}}


# ── Refusals: nothing changes ────────────────────────────────────────────

def test_without_a_snapshot_it_refuses_and_names_the_list(tmp_path, monkeypatch,
                                                          docker_with_projects):
    """Given no snapshot, when the admin runs `stack rollback`, then it
    refuses, names `stack snapshot list`, and changes nothing."""
    checkout = new_checkout(tmp_path, monkeypatch, docker_with_projects, host=False)
    data_before, head_before = everything_in(checkout.data), head(checkout)
    checkout.docker.forget()

    ran = rollback(checkout)

    assert ran.code == 1
    assert "stack snapshot list" in ran.out + ran.err
    nothing_changed(checkout, data_before, head_before)


@pytest.mark.parametrize("named", [False, True], ids=["newest", "by name"])
def test_an_unreadable_snapshot_is_refused_and_named(updated, named):
    """Given a newest snapshot whose manifest cannot be read, when the
    admin runs `stack rollback` or names it, then it refuses, names the
    snapshot and why, and does not go to the older one on its own."""
    broken = updated.data / ".snapshots" / "2099-01-01T0000Z"
    broken.mkdir()
    (broken / "MANIFEST.json").write_text("{not json")
    data_before, head_before = everything_in(updated.data), head(updated)

    ran = rollback(updated, *([broken.name] if named else []))

    assert ran.code == 1
    assert broken.name in ran.out
    assert "MANIFEST.json" in ran.out
    nothing_changed(updated, data_before, head_before)


def test_an_image_the_mac_no_longer_holds_is_refused_before_anything_stops(updated):
    """Given that the Mac removed the image docs ran before, when the
    admin runs `stack rollback`, then it refuses before it stops anything,
    and names the image."""
    updated.docker.drops_image(OLD_IMAGE_ID)
    data_before, head_before = everything_in(updated.data), head(updated)

    ran = rollback(updated)

    assert ran.code == 1
    assert OLD_IMAGE in ran.out
    assert updated.docker.state_of("docs") == "running"
    nothing_changed(updated, data_before, head_before)


def test_local_edits_in_the_checkout_are_refused_and_named(updated):
    """Given an edit to a file of the checkout, when the admin runs `stack
    rollback`, then it refuses and names the file: the checkout of the
    snapshot's commit would set the edit aside or lose it."""
    toml = updated.root / "stacklets" / "photos" / "stacklet.toml"
    toml.write_text(toml.read_text() + "# a note of the admin\n")
    data_before, head_before = everything_in(updated.data), head(updated)

    ran = rollback(updated)

    assert ran.code == 1
    assert "stacklets/photos/stacklet.toml" in ran.out
    nothing_changed(updated, data_before, head_before)


# ── The tags and the images built on this Mac ───────────────────────────

SEARCH_TOML = """
id = "search"
name = "Search"
category = "test"
"""


def test_the_plan_names_a_moved_tag_and_the_stacklets_outside_that_start_it(updated):
    """Given that the tag docs started now names a newer image, and an
    extension outside the snapshot starts the same tag, when the admin
    runs `stack rollback --json` in a terminal and declines, then the
    answer is JSON only: the plan with the tag, the image it names again,
    and the extension that gets that image at its next start."""
    search = updated.root.parent / "extensions" / "search"
    search.mkdir()
    (search / "stacklet.toml").write_text(SEARCH_TOML)
    updated.docker.tags(**{OLD_IMAGE: NEWER_IMAGE_ID})
    updated.docker.running(docs="running", photos="running", search="running")
    updated.docker.runs_image("search", "app", OLD_IMAGE, NEWER_IMAGE_ID,
                              "ghcr.io/paperless-ngx/paperless-ngx@sha256:" + "c" * 64)
    data_before, head_before = everything_in(updated.data), head(updated)

    ran = in_terminal(updated, "rollback", "--json", answer="n")

    assert ran.code == 0, ran.err
    answer = json.loads(ran.out)
    assert answer["declined"] is True
    assert answer["tags"] == [{"name": OLD_IMAGE, "image": OLD_IMAGE_ID, "now": NEWER_IMAGE_ID,
                               "also_used_by": ["search"]}]
    assert answer["others"] == ["search"]
    nothing_changed(updated, data_before, head_before)


def test_an_image_built_on_this_mac_is_neither_checked_nor_tagged(updated):
    """Given a snapshot whose photos image was built on this Mac, and the
    Mac no longer holds that build, when the rollback runs, then it does
    not refuse and does not tag it: the start builds the image again from
    the snapshot's checkout."""
    [name] = updated.snapshot_folders()
    path = updated.data / ".snapshots" / name / "MANIFEST.json"
    manifest = json.loads(path.read_text())
    manifest["stacklets"]["photos"]["images"]["server"]["built"] = True
    path.write_text(json.dumps(manifest))
    updated.docker.drops_image(PHOTOS_IMAGE_ID)
    updated.docker.tags(**{PHOTOS_IMAGE: NEWER_IMAGE_ID})

    ran = rollback(updated)

    assert ran.code == 0, ran.err
    assert all(argv[3] != PHOTOS_IMAGE for argv in tags_set(updated))
    assert updated.docker.state_of("photos") == "running"


# ── A rollback that stopped halfway ──────────────────────────────────────

def _stop_fails(c: Checkout):
    c.docker.fail("stop", "container is busy", stacklet="photos")
    return lambda: c.docker.fail("stop", "", stacklet="photos")


def _start_fails(c: Checkout):
    c.docker.fail("up", "port is already allocated", stacklet="photos")
    return lambda: c.docker.fail("up", "", stacklet="photos")


# Each step that can stop a rollback, how the test stops it there, and
# the entries of the replaced folder at the end besides docs and photos.
STOPS = [
    ("stop", _stop_fails, set()),
    ("checkout", checkout_fails, set()),
    ("move aside", move_fails, set()),
    ("clone", clone_fails, {"photos.2"}),
    ("tag", tag_fails, set()),
    ("start", _start_fails, set()),
]


def test_a_record_that_cannot_be_written_starts_docs_again_on_its_release(updated):
    """Given a replaced folder the rollback cannot write into (a full
    disk), when the rollback runs, then it refuses before any state moves,
    the checkout stays on the release it was on, and docs starts again on
    it: the old release's code never starts over the state the new release
    migrated."""
    replaced = updated.data / ".snapshots" / "replaced"
    replaced.mkdir()
    data_before, head_before = everything_in(updated.data), head(updated)
    branch_before = updated.git("branch", "--show-current")
    replaced.chmod(0o555)
    try:
        ran = rollback(updated)
    finally:
        replaced.chmod(0o755)

    assert ran.code == 1
    assert head(updated) == head_before
    assert updated.git("branch", "--show-current") == branch_before
    assert everything_in(updated.data) == data_before
    assert replaced_folders(updated) == []
    assert updated.docker.state_of("docs") == "running"


@pytest.mark.parametrize("step,stops_it,extra", STOPS, ids=[s[0] for s in STOPS])
def test_a_rollback_stopped_at_any_step_is_finished_by_the_next_run(updated, step, stops_it,
                                                                    extra):
    """Given a rollback that stops at one step (a container that does not
    stop, a git lock, a folder it cannot write, a snapshot file it cannot
    read, a tag Docker refuses, a start that fails), when the admin fixes
    the cause and runs `stack rollback` again, then the rollback ends as
    one that never stopped: no state moved twice, none written over."""
    fixed = stops_it(updated)
    try:
        assert rollback(updated).code == 1
    finally:
        fixed()

    ran = rollback(updated)

    assert ran.code == 0, ran.err
    rolled_back(updated, extra)


def test_a_refusal_before_the_first_state_moves_changes_nothing_and_holds_nothing(updated):
    """Given a stop that fails for photos, when the rollback runs, then
    each state stays where it was, the checkout stays on its release,
    there is no record, docs runs again, and `stack up docs` starts it:
    nothing waits for a rollback."""
    data_before, head_before = everything_in(updated.data), head(updated)
    fixed = _stop_fails(updated)
    try:
        ran = rollback(updated)
    finally:
        fixed()

    assert ran.code == 1
    assert everything_in(updated.data) == data_before
    assert head(updated) == head_before
    assert replaced_folders(updated) == []
    steps = updated.docker.compose_steps()
    assert "stop" in steps and steps[-1] == "up"
    assert updated.docker.state_of("docs") == "running"
    assert updated.stack("up", "docs").code == 0


def test_a_restore_that_stops_twice_keeps_each_half_state(updated):
    """Given a snapshot file that stays unreadable for two runs, when the
    admin runs `stack rollback` twice, fixes the file and runs it a third
    time, then each half restore is in its own entry (`photos.2`,
    `photos.3`) and the rollback ends whole: no run waits for a folder
    the admin has to move by hand."""
    fixed = clone_fails(updated)
    try:
        assert rollback(updated).code == 1
        assert rollback(updated).code == 1
    finally:
        fixed()

    ran = rollback(updated)

    assert ran.code == 0, ran.err
    rolled_back(updated, {"photos.2", "photos.3"})
    replaced = replaced_folder(updated)
    for half in ("photos.2", "photos.3"):
        assert (replaced / half / "albums.json").read_text() == ALBUMS


def test_a_rerun_after_a_failed_start_keeps_what_the_family_did_since(updated):
    """Given a rollback whose start of photos failed while docs started,
    and a letter filed in docs since, when the admin runs `stack
    rollback` again, then it only starts: docs keeps the letter, nothing
    of docs moves aside a second time, and docs is not stopped."""
    updated.docker.fail("up", "port is already allocated", stacklet="photos")
    assert rollback(updated).code == 1
    updated.docker.fail("up", "", stacklet="photos")
    assert updated.docker.state_of("docs") == "running"
    filed = updated.data / "docs" / "postgres" / "base.dat"
    filed.write_text("homer's invoices and the parking ticket")
    updated.docker.forget()

    ran = rollback(updated)

    assert ran.code == 0, ran.err
    assert filed.read_text() == "homer's invoices and the parking ticket"
    assert {p.name for p in replaced_folder(updated).iterdir()} == {"ROLLBACK.json", "docs",
                                                                    "photos"}
    assert "stop" not in updated.docker.compose_steps()
    assert updated.docker.state_of("photos") == "running"


def test_a_rerun_never_moves_a_state_over_one_moved_before(updated):
    """Given a rollback that stopped before it moved photos, and a file
    in the replaced folder where photos' albums would go, when the admin
    runs `stack rollback` again, then it refuses and names the file, and
    both files keep their content."""
    stopped_at_the_clone_of_docs(updated)
    there = replaced_folder(updated) / "photos" / "albums.json"
    there.parent.mkdir()
    there.write_text('["an older copy"]')

    ran = rollback(updated)

    assert ran.code == 1
    assert str(there) in ran.out
    assert there.read_text() == '["an older copy"]'
    assert (updated.data / "photos" / "albums.json").read_text() == NEW_ALBUMS


def test_a_rerun_checks_the_images_again_before_it_goes_on(updated):
    """Given a rollback that stopped at a restore, and the image docs ran
    before removed since, when the admin runs `stack rollback` again,
    then it refuses before it goes on and names the image."""
    stopped_at_the_clone_of_docs(updated)
    updated.docker.drops_image(OLD_IMAGE_ID)
    data_before = everything_in(updated.data)

    ran = rollback(updated)

    assert ran.code == 1
    assert OLD_IMAGE in ran.out
    assert everything_in(updated.data) == data_before
    assert updated.docker.compose_steps() == []


def test_a_rollback_to_another_snapshot_waits_until_the_stopped_one_is_finished(updated):
    """Given a rollback that stopped halfway, when the admin names another
    snapshot, then it refuses, names the one that waits, and changes
    nothing."""
    [name] = updated.snapshot_folders()
    stopped_at_the_clone_of_docs(updated)
    data_before, head_before = everything_in(updated.data), head(updated)

    ran = rollback(updated, "2026-01-01T0000Z")

    assert ran.code == 1
    assert name in ran.out
    nothing_changed(updated, data_before, head_before)


def test_the_plan_of_a_rerun_names_the_rollback_it_finishes_and_what_is_left(updated):
    """Given a rollback that stopped at the restore of photos, when the
    admin runs `stack rollback --json` in a terminal and declines, then
    the plan names the rollback it finishes and the steps that are left."""
    fixed = clone_fails(updated)
    try:
        assert rollback(updated).code == 1
    finally:
        fixed()

    ran = in_terminal(updated, "rollback", "--json", answer="n")

    assert ran.code == 0, ran.err
    answer = json.loads(ran.out)
    assert answer["finishes"] == replaced_folder(updated).name
    assert answer["left"] == ["restore photos", "start docs, photos"]


def test_a_second_rollback_after_a_finished_one_replaces_the_state_again(updated):
    """Given a rollback that finished, and a letter filed in docs since,
    when the admin rolls back to the same snapshot again in the same
    minute, then the second rollback replaces the state again into a
    folder of its own, keeps the letter there, and `stack up docs` starts
    docs: a finished rollback is never finished a second time."""
    assert rollback(updated).code == 0
    filed = updated.data / "docs" / "postgres" / "base.dat"
    filed.write_text("homer's invoices and the parking ticket")
    updated.docker.forget()

    ran = rollback(updated)

    assert ran.code == 0, ran.err
    first, second = replaced_folders(updated)
    second_folder = updated.data / ".snapshots" / "replaced" / second
    assert json.loads(ran.out)["replaced"] == str(second_folder)
    assert (second_folder / "docs" / "postgres" / "base.dat").read_text() == \
        "homer's invoices and the parking ticket"
    assert filed.read_text() == "homer's invoices"
    assert "stop" in updated.docker.compose_steps()
    assert updated.stack("up", "docs").code == 0


def test_while_a_rollback_waits_to_finish_stack_up_refuses_and_names_it(updated):
    """Given a rollback that stopped halfway, when the admin runs `stack up
    docs`, then docs does not start on half a state, and the stack names
    `stack rollback`."""
    stopped_at_the_clone_of_docs(updated)

    ran = updated.stack("up", "docs")

    assert ran.code != 0
    assert "up" not in updated.docker.compose_steps()
    assert "stack rollback" in ran.out + ran.err


def test_an_unreadable_record_is_named_and_holds_only_the_stacklets_beside_it(updated):
    """Given the record of a rollback that cannot be read, with docs'
    state beside it, when the admin runs `stack rollback`, then it refuses
    and names the record; `stack up photos` starts photos, and `stack up
    docs` refuses, names the record and the rename that ends the hold.
    After that rename `stack up docs` starts docs, and nothing is deleted."""
    folder = updated.data / ".snapshots" / "replaced" / "2026-01-01T0000Z"
    (folder / "docs").mkdir(parents=True)
    (folder / "ROLLBACK.json").write_text("{not json")

    ran = rollback(updated)

    assert ran.code == 1
    assert str(folder / "ROLLBACK.json") in ran.out
    assert updated.stack("up", "photos").code == 0
    refused = updated.stack("up", "docs")
    assert refused.code != 0
    record = folder / "ROLLBACK.json"
    assert str(record) in refused.out + refused.err
    assert f"mv {record} {record}.unreadable" in refused.out + refused.err

    # The way on the message names: the rename holds nothing any more,
    # and the state the rollback replaced stays.
    record.rename(f"{record}.unreadable")
    assert updated.stack("up", "docs").code == 0
    assert (folder / "docs").is_dir()


# ── The record of a failed take ──────────────────────────────────────────

def test_the_rollback_ends_the_wait_of_a_failed_take(updated):
    """Given a later restart whose snapshot failed, so docs waits for its
    snapshot, when the admin rolls back, then the rollback starts docs and
    `stack up docs` starts it too: the restored state needs no snapshot."""
    updated.docker.commits(docs=updated.rc1, photos=updated.rc1)
    unreadable = updated.data / "docs" / "postgres" / "base.dat"
    unreadable.chmod(0)
    try:
        assert updated.stack("restart", "docs").code == 1
    finally:
        unreadable.chmod(0o644)
    assert (updated.instance / ".stack" / "snapshot-pending" / "docs").is_dir()

    ran = rollback(updated)

    assert ran.code == 0, ran.err
    assert updated.docker.state_of("docs") == "running"
    assert not (updated.instance / ".stack" / "snapshot-pending" / "docs").exists()
    assert updated.stack("up", "docs").code == 0


# ── The resting state ────────────────────────────────────────────────────

def test_without_a_rollback_list_and_restart_change_nothing_in_the_snapshots(updated):
    """Given a rollback that ended long ago, when the admin runs `stack
    snapshot list` and `stack restart`, then nothing in `.snapshots/`
    changes, and the list shows no replaced state."""
    assert rollback(updated).code == 0
    folder = updated.data / ".snapshots"
    before = everything_in(folder)

    listed = json.loads(updated.stack("snapshot", "list", "--json").out)
    restarted = updated.stack("restart")

    assert restarted.code == 0, restarted.err
    assert everything_in(folder) == before
    assert all(s["name"] != "replaced" for s in listed["snapshots"])
