"""The rollback drill's cleanup: it gives a shared test Mac back as the drill found it.

The drill runs on a Mac that holds images of other checkouts. Its cleanup
decides from two inventories which image names to put back and which
images to remove, and it must run to its end whatever failed before. The
decisions are tested on fabricated inventory lines (`"<name> <id>"`), the
runner and the guard on plain functions, and one cleanup against a
stand-in `docker`.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess

import pytest

from tests.unit.driver import driver_code

cleanup = driver_code.load("drill_cleanup")
drill = driver_code.load("rollback_drill")


# ── Refusing another instance ────────────────────────────────────────────


def test_any_stack_container_is_another_instance_and_other_containers_are_not():
    """Given a stopped `stack-` container and another tool's container. Then only the first is foreign.

    The drill refuses to start on such a Mac, because it never moves
    another instance aside."""
    assert cleanup.foreign_instances(["orb-other", "stack-docs-db", "stack-core"]) == ["stack-docs-db", "stack-core"]
    assert cleanup.foreign_instances(["orb-other"]) == []


# ── The image names ──────────────────────────────────────────────────────


BEFORE = [
    "stack-core-stack-core-tools:latest aaaaaaaaaaaa",
    "valkey/valkey:8.1-alpine bbbbbbbbbbbb",
    "<none>:<none> cccccccccccc",
]


def test_each_named_image_gets_a_keep_name_and_an_image_without_a_name_none():
    """Given the Mac's images. Then each named one gets a second name, so no build drops it."""
    assert cleanup.images_to_keep([*BEFORE, "rollback-drill-keep/old:latest dddddddddddd"]) == [
        ("aaaaaaaaaaaa", "rollback-drill-keep/stack-core-stack-core-tools:latest"),
        ("bbbbbbbbbbbb", "rollback-drill-keep/valkey_valkey:8.1-alpine"),
    ]


def test_a_name_that_a_build_moved_goes_back_to_its_image():
    """Given the drill's build moved the core name, and its pull moved nothing else. Then only the core name goes back.

    This is the case that cost the Mac two images of another checkout:
    the old image lost its last name and Docker dropped it."""
    now = ["stack-core-stack-core-tools:latest eeeeeeeeeeee", "valkey/valkey:8.1-alpine bbbbbbbbbbbb"]

    assert cleanup.tags_to_restore(BEFORE, now) == [("aaaaaaaaaaaa", "stack-core-stack-core-tools:latest")]


def test_a_name_that_is_gone_goes_back_too():
    """Given a name of the Mac that no image carries now. Then it is put back on its image."""
    assert cleanup.tags_to_restore(BEFORE[:1], []) == [("aaaaaaaaaaaa", "stack-core-stack-core-tools:latest")]


def test_a_keep_name_goes_only_when_another_name_holds_its_image():
    """Given a keep name whose image got its name back, and one that is the last name of its image.
    Then only the first goes; the second stays and is named, so its image is not deleted.

    The second is what a failed restore leaves, or an earlier run that
    died after its build moved the image's name."""
    now = [*BEFORE, "rollback-drill-keep/valkey_valkey:8.1-alpine bbbbbbbbbbbb",
           "rollback-drill-keep/stack-core-old:latest ffffffffffff"]

    assert cleanup.keep_tags_to_remove(BEFORE, now) == (["rollback-drill-keep/valkey_valkey:8.1-alpine"],
                                                        ["rollback-drill-keep/stack-core-old:latest"])


def test_a_name_the_drill_added_does_not_cover_a_keep_name():
    """Given a failed restore, and the drill's own pull naming the same image. Then the keep name stays.

    The next step removes the drill's name, and the keep name would then
    have been the image's last one."""
    now = ["stack-core-stack-core-tools:latest eeeeeeeeeeee", "valkey/valkey:8-alpine aaaaaaaaaaaa",
           "rollback-drill-keep/stack-core-stack-core-tools:latest aaaaaaaaaaaa"]

    assert cleanup.keep_tags_to_remove(BEFORE, now) == (
        [], ["rollback-drill-keep/stack-core-stack-core-tools:latest"])


def test_the_drills_names_go_by_name_its_unnamed_images_by_id_and_the_macs_stay():
    """Given a pulled name, an unnamed build of the drill and the Mac's images. Then only the drill's go.

    `<none>:<none>` is no name Docker can remove, so an unnamed image goes
    by its id. An unnamed image the Mac had stays."""
    now = [*BEFORE, "valkey/valkey:8.0-alpine 111111111111", "<none>:<none> 222222222222"]

    assert cleanup.images_to_remove(BEFORE, now) == ["valkey/valkey:8.0-alpine", "222222222222"]


def test_a_name_the_mac_had_is_never_removed_even_when_its_restore_failed():
    """Given the core name still on the drill's build after the restore. Then the cleanup leaves the name.

    Removing it would delete another checkout's name; criterion 4 names
    the difference instead."""
    now = ["stack-core-stack-core-tools:latest eeeeeeeeeeee", *BEFORE[1:]]

    assert cleanup.images_to_remove(BEFORE, now) == []


def test_an_image_two_names_share_goes_by_its_names():
    """Given the drill's pull put two new names on one image. Then each goes by name, never by the shared id.

    Docker refuses to remove an id that two names share."""
    now = [*BEFORE, "valkey/valkey:8.0-alpine 111111111111", "valkey/valkey:8.0 111111111111"]

    assert cleanup.images_to_remove(BEFORE, now) == ["valkey/valkey:8.0-alpine", "valkey/valkey:8.0"]


# ── Running to the end ───────────────────────────────────────────────────


def test_every_step_runs_after_one_failed_and_the_failure_is_named():
    """Given three steps, the first failing. Then the other two still run, and the failure is returned."""
    ran, notes = [], []

    def broken():
        raise OSError("Directory not empty")

    failures = cleanup.run_all([("empty", broken), ("restore", lambda: ran.append("restore")),
                                ("clone", lambda: ran.append("clone"))], notes.append)

    assert ran == ["restore", "clone"]
    assert failures == ["empty: OSError('Directory not empty')"]
    assert len(notes) == 1


def test_the_cleanup_runs_when_the_run_failed_and_the_report_cannot_print():
    """Given a run that fails and a report whose stdout is closed. Then the cleanup still runs.

    Over ssh, a dropped connection closes stdout: each print raises, also
    the one that records the failure."""
    cleaned = []

    def failing_run():
        raise RuntimeError("filing timed out")

    def closed_stdout(text):
        raise BrokenPipeError(32, "Broken pipe")

    cleanup.guarded(failing_run, lambda: cleaned.append(True), closed_stdout)

    assert cleaned == [True]


def test_a_stop_signal_does_not_end_the_cleanup_but_still_stops_its_children():
    """Given a SIGTERM to the drill while the cleanup runs. Then the cleanup goes on, a child it starts
    still ends on SIGTERM, and the drill handles SIGTERM as before afterwards.

    A second Ctrl-C or `kill` would end the cleanup halfway; an ignored
    signal would be inherited by `stack down`, which then could not be
    stopped."""
    seen = []

    def clean():
        os.kill(os.getpid(), signal.SIGTERM)
        child = subprocess.Popen(["sleep", "30"])
        child.terminate()
        seen.append(child.wait(timeout=5))

    handler = signal.getsignal(signal.SIGTERM)
    cleanup.guarded(lambda: None, clean, print)

    assert seen == [-signal.SIGTERM]
    assert signal.getsignal(signal.SIGTERM) == handler


def test_the_report_keeps_its_file_when_stdout_is_closed(tmp_path):
    """Given a report and a closed stdout. Then a line goes into the report's file and nothing raises."""
    report = drill.Report(tmp_path)

    class Closed:
        def write(self, text):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            raise BrokenPipeError(32, "Broken pipe")

    with contextlib.redirect_stdout(Closed()):
        report.line("Cleanup step failed")

    assert "Cleanup step failed" in (tmp_path / "report.md").read_text()


# ── One cleanup against a stand-in docker ────────────────────────────────


@pytest.fixture
def mac(tmp_path, monkeypatch):
    """The drill's folders in `tmp_path`, a `docker` that lists nothing, and a `run` that records."""
    driver_code.command(tmp_path, "docker")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    calls = []
    return calls, cleanup.Mac(run=lambda argv, **kw: calls.append(argv), note=lambda text: None,
                              home=tmp_path, checkout=tmp_path / "drill" / "famstack", drill_folder=tmp_path / "drill",
                              data=tmp_path / "famstack-data", run_folder=tmp_path / "run")


def test_a_data_folder_that_fails_leaves_the_rest_of_the_cleanup_running(mac, tmp_path):
    """Given a data path that cannot be emptied. Then the network, the clone and the names are still handled."""
    calls, the_mac = mac
    (tmp_path / "famstack-data").write_text("not a folder")
    (tmp_path / "drill" / "famstack").mkdir(parents=True)
    before = {"images": [], "networks": ["bridge"], "data_existed": True}

    failures = cleanup.clean(the_mac, before)

    assert [f.split(":")[0] for f in failures] == ["empty the data folder"]
    assert ["./stack", "uninstall", "--yes"] in calls
    assert ["docker", "network", "rm", "stack"] in calls
    assert not (tmp_path / "drill").exists()


def test_the_data_folder_is_left_empty_when_it_was_there_and_empty_before(mac, tmp_path):
    """Given the drill's files in a data folder the Mac had, empty. Then the folder stays, empty."""
    _, the_mac = mac
    (tmp_path / "famstack-data" / "docs" / "paperless").mkdir(parents=True)
    (tmp_path / "famstack-data" / "docs" / "drill-2gb.bin").write_text("x")

    failures = cleanup.clean(the_mac, {"images": [], "networks": ["stack"], "data_existed": True})

    assert failures == []
    assert list((tmp_path / "famstack-data").iterdir()) == []


def test_the_cleanup_restores_names_before_it_removes_any_and_keeps_a_last_name(mac, tmp_path):
    """Given Docker listing a moved core name, two keep names and a pulled image.
    Then the core name is tagged back first, then the covered keep name goes, then the pulled image;
    the keep name that is still the last name of its image stays, and the step names it.

    The stand-in `docker` lists the same images on every call, so the
    keep name of the core image stays the last name of `aaaaaaaaaaaa`:
    the case of a restore that did not take."""
    calls, the_mac = mac
    listed = tmp_path / "images.txt"
    listed.write_text("stack-core-tools:latest eeeeeeeeeeee\nvalkey/valkey:8.1-alpine bbbbbbbbbbbb\n"
                      "rollback-drill-keep/stack-core-tools:latest aaaaaaaaaaaa\n"
                      "rollback-drill-keep/valkey_valkey:8.1-alpine bbbbbbbbbbbb\n"
                      "valkey/valkey:8.0-alpine 111111111111\n")
    driver_code.command(tmp_path, "docker", then=f'[ "$1" = images ] && cat "{listed}"; exit 0')
    before = {"images": ["stack-core-tools:latest aaaaaaaaaaaa", "valkey/valkey:8.1-alpine bbbbbbbbbbbb"],
              "networks": ["stack"], "data_existed": True}

    failures = cleanup.clean(the_mac, before)

    order = [calls.index(c) for c in (["docker", "image", "tag", "aaaaaaaaaaaa", "stack-core-tools:latest"],
                                      ["docker", "image", "rm", "rollback-drill-keep/valkey_valkey:8.1-alpine"],
                                      ["docker", "image", "rm", "valkey/valkey:8.0-alpine"])]
    assert order == sorted(order)
    assert ["docker", "image", "rm", "rollback-drill-keep/stack-core-tools:latest"] not in calls
    assert [f.split(":")[0] for f in failures] == ["restore the image names"]
    assert "rollback-drill-keep/stack-core-tools:latest" in failures[0]


def test_keep_gives_each_named_image_of_the_mac_a_second_name(mac):
    """Given the Mac's images before the run. Then `keep` tags each named one, so no build drops it."""
    calls, the_mac = mac

    cleanup.keep(the_mac, {"images": BEFORE})

    assert calls == [["docker", "image", "tag", "aaaaaaaaaaaa", "rollback-drill-keep/stack-core-stack-core-tools:latest"],
                     ["docker", "image", "tag", "bbbbbbbbbbbb", "rollback-drill-keep/valkey_valkey:8.1-alpine"]]


# ── The rollback the drill polls ─────────────────────────────────────────


def test_a_rollback_left_by_an_error_of_the_poll_is_killed(tmp_path):
    """Given a rollback that runs on, and a poll that fails (as a SIGTERM to the drill does).
    Then the error goes on, and the rollback no longer runs.

    It runs in a session of its own, so the drill's signal never reaches
    it, and it would write into the folders that the cleanup deletes."""
    pid_file, child_file = tmp_path / "pid", tmp_path / "child"
    stand_in = tmp_path / "stack"
    # A child in the background, as `stack rollback` starts docker and git:
    # killing only the leader would leave it running.
    stand_in.write_text(f'#!/bin/sh\nsleep 30 &\necho $! > "{child_file}"\necho $$ > "{pid_file}"\nwait\n')
    stand_in.chmod(0o755)

    def read():
        if pid_file.exists() and pid_file.read_text().strip():
            raise KeyboardInterrupt("signal 15")
        return None, []

    with pytest.raises(KeyboardInterrupt):
        drill.rollback_until_a_new_step(None, [], checkout=tmp_path, read=read)

    for pid in (int(pid_file.read_text()), int(child_file.read_text())):
        assert not _running(pid)


def _running(pid: int) -> bool:
    """Whether `pid` runs; a killed child that its parent has not reaped yet is a zombie, which counts as ended."""
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def test_the_build_cache_is_emptied_only_when_the_mac_had_none():
    """Given a Mac with no build cache before, and one with a cache of its own.
    Then the cleanup prunes the first and leaves the second.

    Docker prunes the cache as a whole, so pruning the second would delete
    the Mac's own layers."""
    assert cleanup.prune_build_cache({"build_cache": []})
    assert not cleanup.prune_build_cache({"build_cache": ["x1"]})
