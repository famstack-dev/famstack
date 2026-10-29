"""An extension's commands, from outside: what the extension lane promises.

An extension is a stacklet the repo does not ship. A command of one that
has its work in a container runs it in the bot runner, as the shipped
stacklets' commands do, so the bot runner has to find the extension's
entry point and give it the extension's own settings. These tests drive
`./stack kettle boil` (`tests/extensions/kettle/`) as a person runs it, in
the test instance every extension's tests get (`tests/extensions/instance.py`).
"""

from __future__ import annotations

from tests.extensions.instance import installed


def _service_reports(httpserver, celsius: int) -> None:
    httpserver.expect_request("/water").respond_with_json({"celsius": celsius})


def test_an_extensions_command_does_its_work_in_the_bot_runner(tmp_path, kettle, httpserver):
    """Given the kettle installed and up, and its service reporting 97 °C,
    when a person runs `stack kettle boil --json`, then the answer is the
    service's reading.

    The entry point is the extension's, at `/extensions/kettle/` in the bot
    runner, and it finds the service by the kettle's own setting
    `KETTLE_URL`, which only the kettle's env holds."""
    _service_reports(httpserver, 97)
    with installed(tmp_path, kettle) as stack:
        stack.serves("stack-kettle:8080", httpserver)

        ran = stack.run("kettle", "boil", "--json")

    assert ran.code == 0, ran
    assert ran.json == {"celsius": 97, "boiling": False}


def test_people_see_the_reading_and_nothing_else(tmp_path, kettle, httpserver):
    """Given the kettle's service reporting 100 °C, when a person runs
    `stack kettle boil` without `--json`, then they see the temperature
    and that it boils, and no JSON."""
    _service_reports(httpserver, 100)
    with installed(tmp_path, kettle) as stack:
        stack.serves("stack-kettle:8080", httpserver)

        ran = stack.run("kettle", "boil")

    assert ran.code == 0, ran
    assert "100" in ran.out and "boiling" in ran.out
    assert "{" not in ran.out


def test_an_extension_the_bot_runner_cannot_see_is_told_where_to_put_it(tmp_path, kettle):
    """Given the kettle installed in the second extension directory, which
    the bot runner does not mount, when a person runs its command, then it
    fails and names that directory and the setting that decides it.

    Core mounts only the first of `[core] extension_dirs`; without this the
    command would fail in the container with a missing file."""
    with installed(tmp_path, kettle, mounted=False) as stack:
        ran = stack.run("kettle", "boil", "--json")

    assert ran.code == 1, ran
    error = ran.json["error"]
    assert str(tmp_path / "more-extensions") in error and "extension_dirs" in error


def test_an_extension_never_brought_up_says_to_bring_it_up(tmp_path, kettle):
    """Given the kettle installed but never brought up, so its settings were
    never rendered, when a person runs its command, then it fails and names
    `stack up kettle`."""
    with installed(tmp_path, kettle, up=False) as stack:
        ran = stack.run("kettle", "boil", "--json")

    assert ran.code == 1, ran
    assert "stack up kettle" in ran.json["error"]

