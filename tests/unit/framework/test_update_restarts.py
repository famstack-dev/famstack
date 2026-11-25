# ruff: noqa: F811  (the imported fixtures are the tests' parameters)
"""`stack update` applies a release in one command, as the admin runs it.

The update moves the checkout to the release and then restarts the
stacklets that the release changes. A snapshot is taken only when the
admin asks for one with `--with-snapshot`. `--no-restart` only moves the
checkout and names the two restarts that apply the release later.

The instance is the one the snapshot tests share (`snapshot_instance.py`):
a git checkout with `docs` and `photos` and two releases. Each test first
moves the checkout back to `v0.4.0-rc.1`, which both stacklets run, so
that `stack update v0.4.0-rc.2` has a release to apply. Docker is the
stand-in `docker_with_projects`, which follows `compose up` and `compose
stop`. Every `./stack` command is a process of its own, as the admin runs
it. The update starts the restart as one more process, so a test takes
about two seconds.
"""

from __future__ import annotations

from tests.unit.framework.snapshot_instance import (  # noqa: F401 (the fixtures)
    Checkout,
    checkout,
    head,
    in_terminal,
)

UPDATE = ("update", "v0.4.0-rc.2")


def on_rc1(checkout: Checkout) -> Checkout:
    """The checkout on rc.1, the release that docs and photos run."""
    checkout.git("checkout", "-q", "v0.4.0-rc.1")
    checkout.docker.forget()
    return checkout


# ── The update applies the release ───────────────────────────────────────

def test_the_update_moves_the_checkout_and_restarts_what_the_release_changes(checkout):
    """Given docs and photos running rc.1, when the admin runs `stack
    update v0.4.0-rc.2` and confirms, then the checkout is on rc.2, both
    stacklets were stopped and started on it, and no snapshot was taken:
    the update applies the release in one command, and a snapshot comes
    only on request."""
    on_rc1(checkout)

    ran = checkout.stack(*UPDATE, "--yes")

    assert ran.code == 0, ran.err
    assert head(checkout) == checkout.rc2
    steps = checkout.docker.compose_steps()
    assert steps.count("stop") == 2 and steps.count("up") == 2
    assert checkout.docker.state_of("docs") == checkout.docker.state_of("photos") == "running"
    assert checkout.snapshot_folders() == []


def test_with_snapshot_the_update_takes_one_before_the_new_release_starts(checkout):
    """Given the same, when the admin runs `stack update v0.4.0-rc.2
    --with-snapshot --yes`, then one snapshot holds docs as it ran rc.1,
    and docs runs rc.2 after it."""
    on_rc1(checkout)

    ran = checkout.stack(*UPDATE, "--with-snapshot", "--yes")

    assert ran.code == 0, ran.err
    [name] = checkout.snapshot_folders()
    manifest = checkout.manifest(name)
    assert manifest["stacklets"]["docs"]["commit"] == checkout.rc1
    assert manifest["applies"] == "0.4.0-rc.2"
    assert checkout.docker.state_of("docs") == "running"


def test_a_failed_snapshot_of_the_update_starts_nothing_on_the_new_release(checkout):
    """Given a snapshot that cannot be taken (`.snapshots` is a file),
    when the admin runs `stack update --with-snapshot`, then the
    checkout is on rc.2, docs is stopped and not started on it, and the
    update ends with exit 1 and the restart that takes the snapshot: a
    one-way migration must not run without the way back that was asked
    for."""
    on_rc1(checkout)
    (checkout.data / ".snapshots").write_text("not a folder")

    ran = checkout.stack(*UPDATE, "--with-snapshot", "--yes")

    assert ran.code == 1
    assert head(checkout) == checkout.rc2
    assert set(checkout.docker.compose_steps()) == {"stop"}
    assert checkout.docker.state_of("docs") == "stopped"
    assert "stack restart docs" in ran.err and "--with-snapshot" in ran.err


def test_the_plan_names_the_restart_and_how_to_take_a_snapshot(checkout):
    """Given the checkout on rc.1, when the admin asks `stack update
    --dry-run` for its plan, then it names the stacklets the update
    restarts and the flag that takes a snapshot first, and nothing
    moves or restarts."""
    on_rc1(checkout)

    ran = checkout.stack(*UPDATE, "--dry-run")

    assert ran.code == 0, ran.err
    assert "Restarts after the move: docs, photos\n" in ran.out
    assert "--with-snapshot" in ran.out
    assert head(checkout) == checkout.rc1
    assert checkout.docker.compose_steps() == []


# ── The restart is the plan's list ───────────────────────────────────────

def docs_only_release(checkout: Checkout) -> None:
    """A release `v0.4.0-rc.3` after rc.2 that changes docs and nothing
    else, with the checkout back on rc.2."""
    compose = checkout.root / "stacklets" / "docs" / "docker-compose.yml"
    compose.write_text(compose.read_text().replace("2.20.2", "2.20.3"))
    checkout.release("v0.4.0-rc.3")
    checkout.git("checkout", "-q", "v0.4.0-rc.2")


def test_the_update_restarts_no_stacklet_that_its_plan_did_not_name(checkout):
    """Given photos stale from an earlier update (it runs rc.1) and a
    release rc.3 that changes only docs, when the admin runs `stack update
    v0.4.0-rc.3 --yes`, then the plan names docs alone and only docs is
    restarted: the confirmation covers the plan's list, and photos'
    service must not go down without it."""
    docs_only_release(checkout)
    checkout.docker.commits(docs=checkout.rc2, photos=checkout.rc1)
    checkout.docker.forget()

    ran = checkout.stack("update", "v0.4.0-rc.3", "--yes")

    assert ran.code == 0, ran.err
    assert "Restarts after the move: docs\n" in ran.out
    assert checkout.docker.recreated("docs") != []
    assert checkout.docker.recreated("photos") == []


def test_the_update_restarts_a_stacklet_of_the_plan_that_has_no_commit_stamp(checkout):
    """Given docs running without a commit stamp (containers from before
    the stamp existed) and photos on rc.1, when the admin runs `stack
    update v0.4.0-rc.2 --yes`, then both are restarted, as the plan named
    them: an update that leaves a named stacklet on the old code would
    end as if it had applied the release."""
    on_rc1(checkout)
    checkout.docker.commits(photos=checkout.rc1)

    ran = checkout.stack(*UPDATE, "--yes")

    assert ran.code == 0, ran.err
    assert "Restarts after the move: docs, photos\n" in ran.out
    assert checkout.docker.recreated("docs") != []
    assert checkout.docker.recreated("photos") != []


# ── Only the move ────────────────────────────────────────────────────────

def test_no_restart_only_moves_the_checkout_and_names_both_restarts(checkout):
    """Given the same, when the admin runs `stack update --no-restart`,
    then the checkout is on rc.2, nothing stopped or started, and the
    output names `./stack restart` and `./stack restart --with-snapshot`,
    the two commands that apply the release later."""
    on_rc1(checkout)

    ran = checkout.stack(*UPDATE, "--no-restart", "--yes")

    assert ran.code == 0, ran.err
    assert head(checkout) == checkout.rc2
    assert checkout.docker.compose_steps() == []
    assert "./stack restart" in ran.out
    assert "./stack restart --with-snapshot" in ran.out
    assert checkout.snapshot_folders() == []


def test_with_snapshot_and_no_restart_together_are_refused(checkout):
    """Given the checkout on rc.1, when the admin runs `stack update
    --with-snapshot --no-restart`, then the update refuses with exit 1
    and nothing moves: a snapshot comes only with a restart, and an admin
    who typed both would believe that one was taken."""
    on_rc1(checkout)

    ran = checkout.stack(*UPDATE, "--with-snapshot", "--no-restart", "--yes")

    assert ran.code == 1
    assert "--with-snapshot" in ran.out + ran.err
    assert head(checkout) == checkout.rc1
    assert checkout.docker.compose_steps() == []


# ── Nothing to do ────────────────────────────────────────────────────────

def test_a_declined_update_moves_nothing_and_restarts_nothing(checkout):
    """Given the checkout on rc.1, when the admin answers no at the
    update's question, then the checkout stays on rc.1 and nothing
    stops."""
    on_rc1(checkout)

    ran = in_terminal(checkout, *UPDATE, answer="n")

    assert ran.code == 0, ran.err
    assert head(checkout) == checkout.rc1
    assert checkout.docker.compose_steps() == []


def test_the_release_already_there_moves_nothing_and_restarts_nothing(checkout):
    """Given the checkout on rc.2 and docs running rc.1, when the admin
    runs `stack update v0.4.0-rc.2 --yes`, then the update says it is
    already there and restarts nothing: only a move applies a release."""
    checkout.docker.forget()

    ran = checkout.stack(*UPDATE, "--yes")

    assert ran.code == 0, ran.err
    assert head(checkout) == checkout.rc2
    assert checkout.docker.compose_steps() == []
