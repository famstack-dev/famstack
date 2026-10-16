"""Stacklet migrations: a change to existing data runs once per instance, in order.

A stacklet ships `migrations/NNNN_<name>.py`. `stack up` runs the ones
this instance's data has not seen, after the stacklet is up and healthy,
records each, and stops at the first failure with the containers left
running. The record lives with the data: `<data_dir>/<id>/.migrations.json`,
and for a migration that commits to a repository on the code host, the
commit itself, found by its `Migration:` trailer.

The CLI runs for real over stacklets written to a temporary instance;
only the Docker calls are replaced, and Forgejo is a local HTTP server.
"""

import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))


@pytest.fixture(autouse=True)
def no_docker():
    with patch("stack.docker.ensure_network", return_value=("mocked", None)), \
         patch("stack.docker.compose_pull", return_value=(0, "")), \
         patch("stack.docker.compose_up", return_value=(0, "")), \
         patch("stack.docker.compose_stop", return_value=(0, "")), \
         patch("stack.docker.compose_down", return_value=(0, "")):
        yield


def _cli(tmp_path, stacklets: dict[str, str], migrations: dict[str, dict[str, str]],
         hooks: dict[str, dict[str, str]] | None = None):
    """A CLI over stacklets given as manifest bodies, each with migration files."""
    from stack import Stack
    from stack.cli import CLI
    from stack.output import CollectorOutput

    (tmp_path / "stack.toml").write_text('[core]\ntimezone = "Europe/Berlin"\n')
    (tmp_path / ".stack").mkdir(exist_ok=True)
    (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')
    for sid, body in stacklets.items():
        sdir = tmp_path / "stacklets" / sid
        (sdir / "hooks").mkdir(parents=True)
        (sdir / "migrations").mkdir()
        (sdir / "stacklet.toml").write_text(f'id = "{sid}"\nname = "{sid}"\n{body}')
        for name, code in migrations.get(sid, {}).items():
            (sdir / "migrations" / name).write_text(textwrap.dedent(code))
        for name, code in (hooks or {}).get(sid, {}).items():
            (sdir / "hooks" / f"{name}.py").write_text(textwrap.dedent(code))
    return CLI(Stack(root=tmp_path, data=tmp_path / "data", output=CollectorOutput()))


def _logging(log: Path, line: str, *, fail: bool = False, doc: str = "Change something.") -> str:
    """A migration that appends `line` to `log`, then raises if told to."""
    raises = "    raise RuntimeError('the vault is not reachable')\n" if fail else ""
    return (f'"""{doc}"""\n'
            "from pathlib import Path\n"
            "def run(ctx):\n"
            f"    with Path({str(log)!r}).open('a') as f:\n"
            f"        f.write({line!r} + '\\n')\n" + raises)


def _record(tmp_path, sid) -> dict:
    return json.loads((tmp_path / "data" / sid / ".migrations.json").read_text())


class TestPendingMigrationsRunOnceInOrder:

    def test_two_migrations_run_in_number_order_and_are_recorded(self, tmp_path):
        log = tmp_path / "log"
        cli = _cli(tmp_path, {"app": ""}, {"app": {
            "0002_second.py": _logging(log, "second", doc="Rename the second thing."),
            "0001_first.py": _logging(log, "first", doc="Rename the first thing."),
        }})

        result = cli.up("app")

        assert result["ok"]
        assert log.read_text().splitlines() == ["first", "second"]
        assert result["migrations"] == {"applied": ["app/0001", "app/0002"]}
        assert sorted(_record(tmp_path, "app")["applied"]) == ["0001", "0002"]
        assert "Migrating app 0001: Rename the first thing." in cli.stack.output.steps

    def test_a_second_up_runs_neither_and_says_nothing_about_them(self, tmp_path):
        log = tmp_path / "log"
        cli = _cli(tmp_path, {"app": ""}, {"app": {
            "0001_first.py": _logging(log, "first"),
            "0002_second.py": _logging(log, "second"),
        }})
        cli.up("app")
        record_before = (tmp_path / "data" / "app" / ".migrations.json").read_text()
        cli.stack.output.steps.clear()

        result = cli.up("app")

        assert result["ok"] and "migrations" not in result
        assert log.read_text().splitlines() == ["first", "second"]
        assert not [s for s in cli.stack.output.steps if "Migrat" in s]
        assert (tmp_path / "data" / "app" / ".migrations.json").read_text() == record_before

    def test_a_stacklet_without_migrations_gets_no_record(self, tmp_path):
        cli = _cli(tmp_path, {"app": ""}, {})
        assert "migrations" not in cli.up("app")
        assert not (tmp_path / "data" / "app" / ".migrations.json").exists()

    def test_with_the_record_gone_every_migration_runs_again(self, tmp_path):
        """A data dir restored from before the migrations has no record."""
        log = tmp_path / "log"
        cli = _cli(tmp_path, {"app": ""}, {"app": {"0001_first.py": _logging(log, "first")}})
        cli.up("app")
        (tmp_path / "data" / "app" / ".migrations.json").unlink()

        cli.up("app")

        assert log.read_text().splitlines() == ["first", "first"]

    def test_migrations_run_after_on_start_ready(self, tmp_path):
        """A hook repairs what a migration needs, a token for instance."""
        log = tmp_path / "log"
        cli = _cli(tmp_path, {"app": ""}, {"app": {"0001_first.py": _logging(log, "migration")}},
                   hooks={"app": {"on_start_ready": f"""
                       from pathlib import Path
                       def run(ctx):
                           with Path({str(log)!r}).open("a") as f:
                               f.write("ready\\n")
                   """}})
        cli.up("app")
        assert log.read_text().splitlines() == ["ready", "migration"]


class TestAFailureStopsTheRun:

    def _cli(self, tmp_path, log):
        return _cli(tmp_path, {"app": ""}, {"app": {
            "0001_first.py": _logging(log, "first", fail=True),
            "0002_second.py": _logging(log, "second"),
        }})

    def test_the_run_stops_up_fails_and_the_next_one_waits(self, tmp_path):
        log = tmp_path / "log"
        cli = self._cli(tmp_path, log)

        result = cli.up("app")

        assert not result["ok"]
        assert result["error"] == "Migration app/0001 failed"
        assert result["problems"] == ["Fix the cause, then: stack up app"]
        assert log.read_text().splitlines() == ["first"]
        assert "Migration app/0001 failed: the vault is not reachable" in cli.stack.output.errors
        record = _record(tmp_path, "app")
        assert record["applied"] == {}
        assert record["failed"]["0001"]["error"] == "the vault is not reachable"

    def test_the_containers_stay_up(self, tmp_path):
        cli = self._cli(tmp_path, tmp_path / "log")
        with patch("stack.docker.compose_stop") as stop, patch("stack.docker.compose_down") as down:
            cli.up("app")
        stop.assert_not_called()
        down.assert_not_called()

    def test_the_next_up_retries_it_and_then_runs_the_rest(self, tmp_path):
        log = tmp_path / "log"
        cli = self._cli(tmp_path, log)
        assert not cli.up("app")["ok"]
        assert not cli.up("app")["ok"]
        assert log.read_text().splitlines() == ["first", "first"]

        (tmp_path / "stacklets" / "app" / "migrations" / "0001_first.py").write_text(
            _logging(log, "first fixed"))
        result = cli.up("app")

        assert result["ok"]
        assert log.read_text().splitlines() == ["first", "first", "first fixed", "second"]
        assert _record(tmp_path, "app")["failed"] == {}

    def test_two_files_with_one_number_stop_everything_before_it_runs(self, tmp_path):
        log = tmp_path / "log"
        cli = _cli(tmp_path, {"app": ""}, {"app": {
            "0001_first.py": _logging(log, "first"),
            "0001_other.py": _logging(log, "other"),
        }})

        result = cli.up("app")

        assert not result["ok"]
        assert "two migrations numbered 0001" in result["error"]
        assert not log.exists()


class TestAFailedHealthCheckHoldsThemBack:

    def test_none_run_and_one_line_says_they_wait(self, tmp_path):
        log = tmp_path / "log"
        cli = _cli(tmp_path, {"app": '[health]\nurl = "http://localhost:1/health"\n'},
                   {"app": {"0001_first.py": _logging(log, "first")}})
        (tmp_path / "stacklets" / "app" / "docker-compose.yml").write_text("services: {}\n")

        with patch("stack.docker.wait_for_health", return_value="timeout"):
            result = cli.up("app")

        assert result["ok"]
        assert not log.exists()
        assert cli.stack.output.warnings == [
            "Migrations of app wait until its health checks pass"]


class TestSeveralStackletsStartFirst:

    def test_migrations_run_after_every_stacklet_is_up_in_dependency_order(self, tmp_path):
        """`app` sorts first, but needs `base`, so `base` starts and migrates first."""
        log = tmp_path / "log"
        ready = """
            from pathlib import Path
            def run(ctx):
                with Path({log!r}).open("a") as f:
                    f.write(ctx.stacklet_id + " up\\n")
        """.format(log=str(log))
        cli = _cli(tmp_path, {"app": 'requires = ["base"]\n', "base": ""}, {
            "app": {"0001_first.py": _logging(log, "app migrated")},
            "base": {"0001_first.py": _logging(log, "base migrated")},
        }, hooks={"app": {"on_start_ready": ready}, "base": {"on_start_ready": ready}})

        result = cli.up_many(["app", "base"])

        assert result["ok"]
        assert log.read_text().splitlines() == [
            "base up", "app up", "base migrated", "app migrated"]

    def test_a_failed_migration_is_reported_as_that_stacklets_error(self, tmp_path):
        log = tmp_path / "log"
        cli = _cli(tmp_path, {"app": "", "base": ""}, {
            "app": {"0001_first.py": _logging(log, "app", fail=True)},
            "base": {"0001_first.py": _logging(log, "base")},
        })

        result = cli.up_many(["app", "base"])

        assert not result["ok"]
        assert [e["stacklet"] for e in result["errors"]] == ["app"]
        assert sorted(log.read_text().splitlines()) == ["app", "base"]


class TestACommitIsTheRecordOfARepositoryMigration:
    """A migration of the family memory records itself in its commit.

    `.migrations.json` points at the commit; the migration counts as
    applied while the code host has a commit of that sha carrying its
    `Migration:` trailer, and runs again when it does not.
    """

    MIGRATION = '''
        """Move the old lists."""
        from pathlib import Path
        REPO = "family/memory"
        def run(ctx):
            with Path({log!r}).open("a") as f:
                f.write(ctx.trailer + "\\n")
            return "c0ffee"
    '''

    def _cli(self, tmp_path, httpserver, log):
        return _cli(tmp_path, {"code": f"port = {httpserver.port}\n", "memory": ""},
                    {"memory": {"0001_move_lists.py": self.MIGRATION.format(log=str(log))}})

    COMMIT = "/api/v1/repos/family/memory/git/commits/c0ffee"

    def test_the_commit_sha_is_recorded_and_the_migration_knows_its_trailer(
            self, tmp_path, httpserver):
        log = tmp_path / "log"
        cli = self._cli(tmp_path, httpserver, log)

        assert cli.up("memory")["ok"]

        assert log.read_text().splitlines() == ["Migration: memory/0001"]
        assert _record(tmp_path, "memory")["applied"]["0001"]["commit"] == "c0ffee"

    def test_with_the_commit_in_the_history_it_does_not_run_again(self, tmp_path, httpserver):
        log = tmp_path / "log"
        cli = self._cli(tmp_path, httpserver, log)
        cli.up("memory")
        httpserver.expect_request(self.COMMIT).respond_with_json(
            {"sha": "c0ffee", "commit": {
                "message": "lists: move the old lists\n\nMigration: memory/0001\n"}})

        cli.up("memory")

        assert log.read_text().splitlines() == ["Migration: memory/0001"]
        request, _ = httpserver.log[-1]
        assert request.headers["Authorization"].startswith("Basic ")

    def test_with_the_commit_gone_from_the_history_it_runs_again(self, tmp_path, httpserver):
        """As after restoring the code host from a backup older than the commit."""
        log = tmp_path / "log"
        cli = self._cli(tmp_path, httpserver, log)
        cli.up("memory")
        httpserver.expect_request(self.COMMIT).respond_with_data("", status=404)

        cli.up("memory")

        assert log.read_text().splitlines() == ["Migration: memory/0001"] * 2

    def test_a_commit_without_its_trailer_does_not_count(self, tmp_path, httpserver):
        log = tmp_path / "log"
        cli = self._cli(tmp_path, httpserver, log)
        cli.up("memory")
        httpserver.expect_request(self.COMMIT).respond_with_json(
            {"sha": "c0ffee", "commit": {"message": "lists: an unrelated change\n"}})

        cli.up("memory")

        assert len(log.read_text().splitlines()) == 2

    def test_a_code_host_that_does_not_answer_leaves_the_record_alone(self, tmp_path, httpserver):
        log = tmp_path / "log"
        cli = self._cli(tmp_path, httpserver, log)
        cli.up("memory")
        httpserver.expect_request(self.COMMIT).respond_with_data("", status=502)

        cli.up("memory")

        assert len(log.read_text().splitlines()) == 1


class TestTheShippedMigrations:

    def test_no_stacklet_ships_two_migrations_with_one_number(self):
        """Two branches that both add 0002 merge cleanly; this catches it."""
        from stack.migrations import discover
        for sdir in sorted((REPO_ROOT / "stacklets").iterdir()):
            discover(sdir.name, sdir)

    def test_every_shipped_migration_says_what_it_changes(self):
        from stack.migrations import discover
        for sdir in sorted((REPO_ROOT / "stacklets").iterdir()):
            for migration in discover(sdir.name, sdir):
                assert migration.summary != migration.name.replace("_", " "), migration.path


class TestDoctorNamesThem:
    """`stack doctor` lists what `stack up` would run, what failed, and
    data a newer release migrated."""

    def _findings(self, cli, sid):
        from stack.doctor import check_migrations
        status = cli.stack.migrations(sid).status()
        return check_migrations(sid, [(m.key, m.summary) for m in status.pending],
                                status.failed, status.newer)

    def test_a_pending_migration_is_a_warning_with_its_summary(self, tmp_path):
        cli = _cli(tmp_path, {"app": ""}, {"app": {
            "0001_first.py": _logging(tmp_path / "log", "first", doc="Rename the lists.")}})

        [finding] = self._findings(cli, "app")

        assert finding.level == "warn"
        assert "app/0001: Rename the lists." in finding.detail
        assert finding.fix == "stack up app"

    def test_a_failed_migration_is_an_error_with_its_recorded_error(self, tmp_path):
        cli = _cli(tmp_path, {"app": ""}, {"app": {
            "0001_first.py": _logging(tmp_path / "log", "first", fail=True)}})
        cli.up("app")

        [finding] = self._findings(cli, "app")

        assert finding.is_error
        assert finding.title == "migration app/0001 failed"
        assert finding.detail == "the vault is not reachable"

    def test_data_migrated_by_a_newer_release_is_a_warning(self, tmp_path):
        log = tmp_path / "log"
        cli = _cli(tmp_path, {"app": ""}, {"app": {
            "0001_first.py": _logging(log, "first"),
            "0002_second.py": _logging(log, "second"),
        }})
        cli.up("app")
        (tmp_path / "stacklets" / "app" / "migrations" / "0002_second.py").unlink()

        [finding] = self._findings(cli, "app")

        assert finding.level == "warn"
        assert finding.title == "app data was migrated by a newer release"
        assert "app/0002" in finding.detail

    def test_all_applied_says_nothing(self, tmp_path):
        cli = _cli(tmp_path, {"app": ""}, {"app": {
            "0001_first.py": _logging(tmp_path / "log", "first")}})
        cli.up("app")
        assert self._findings(cli, "app") == []
