"""A stacklet's `.env` reads back as `stack up` wrote it (`stack.env_file`).

`stack up` writes the file for docker compose, and a command whose work
runs in the bot runner reads it back to hand that work the stacklet's
settings. A value the two read differently is a setting the command gets
wrong without any error: a password with a quote in it, a path with a
backslash.
"""

from __future__ import annotations

from stack import env_file

TRICKY = {
    "WEB_SECRET": 'p"ss\\word',
    "SEARCH_URL": "http://stack-web-search:8080",
    "EMPTY": "",
    "SPACED": "two words",
}


def test_every_value_reads_back_as_it_was_written(tmp_path):
    """Given settings with quotes, backslashes, an empty value and spaces,
    when they are written as `.env` and read back, then each is the same."""
    path = tmp_path / ".env"
    path.write_text(env_file.render(TRICKY), encoding="utf-8")

    assert env_file.read(path) == TRICKY


def test_the_header_is_not_a_setting(tmp_path):
    """Given a written `.env`, when it is read, then the header comment and
    the blank line under it are not among the settings."""
    path = tmp_path / ".env"
    path.write_text(env_file.render({"TZ": "Europe/Berlin"}), encoding="utf-8")

    assert env_file.read(path) == {"TZ": "Europe/Berlin"}
