"""`stack memory write` of a new note files it as a note sent in the chat is.

A note has a clean version of its words, the words quoted under `## Sources`,
and a sidecar that says what it was made from (ADR-018). The capture pipeline
writes all three; a page written as given has none of them, and a refile has
nothing to read it again from. So a new note written with `stack memory write`
goes through the pipeline `stack memory capture` runs, in the bot runner.

Homer runs the command as `./stack` does. What is real: the command and its
loader, the archivist's capture command and its pipeline, run in this process
as the bot runner's `docker exec` would run them, and its mirror's commit.
What stands in: Forgejo over a real git repository (`test_memory_card_links`),
the curator's clone that follows it, Paperless, which knows no names, and the
model, an HTTP server answering the chat completions API.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import sys
from types import SimpleNamespace

import pytest
from werkzeug.wrappers import Response

from stack.frontmatter import parse

from .conftest import STACKLETS
from .dsl import Person, commit
from .test_memory_card_edit import core_door  # noqa: F401
from .test_memory_commit_author import (  # noqa: F401
    _in_the_bot_runner, archivist, containers, curator, endpoint, memory, server_name, simpson)
from .test_memory_refile_keeps_edits import _own_cli_package, _stdin, model  # noqa: F401
from test_memory_card_links import contents, forgejo  # noqa: E402, F401

ENTRYPOINT = STACKLETS / "docs" / "bot" / "cli_entrypoint.py"

GRILL = "the grill needs new charcoal before saturday"


@pytest.fixture
def bot_half(curator, httpserver, monkeypatch, tmp_path):  # noqa: F811
    """The bot runner's `docker exec` of the archivist's CLI entrypoint, run
    here with the environment the bot runner gives it. Its mirror has the
    Forgejo token the archivist was given on its first start."""
    for key, value in {"OPENAI_URL": httpserver.url_for("/v1"), "OPENAI_KEY": "k",
                       "LANGUAGE": "en", "PAPERLESS_URL": httpserver.url_for("/paperless"),
                       "PAPERLESS_TOKEN": "t", "CODE_URL": httpserver.url_for("").rstrip("/"),
                       "MATRIX_ADMIN_USER": "stackadmin", "MATRIX_ADMIN_PASSWORD": "donuts",
                       "MEMORY_VAULT_DIR": str(curator), "STACK_COMMIT": "9a5bdc9"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("stack.ai.models._DEFAULT_MODEL", "stand-in")
    httpserver.expect_request("/paperless/api/tags/").respond_with_handler(
        lambda request: Response(json.dumps({"results": []}), content_type="application/json"))

    def call(path, command, *argv, input_text=None):
        assert path == "/stacklets/docs/bot/cli_entrypoint.py"
        out = io.StringIO()
        with _in_the_bot_runner(), _own_cli_package():
            spec = importlib.util.spec_from_file_location("docs_cli_entrypoint", ENTRYPOINT)
            entrypoint = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(entrypoint)
            capture = sys.modules["cli.capture"]
            capture._DATA_DIR = tmp_path / "archivist-cli"
            capture.build_mirror_like_bot = _set_up(capture.build_mirror_like_bot)
            with contextlib.redirect_stdout(out), _stdin(input_text or ""):
                code = asyncio.run(entrypoint.main([command, *argv]))
        if code != 0:
            raise RuntimeError(f"{command} failed (exit {code}): {out.getvalue().strip()}")
        return out.getvalue()

    monkeypatch.setattr("stack.bot_runner.call", call)


def _set_up(build):
    """The mirror as the CLI builds it, past the archivist's first-start setup."""
    def built():
        mirror = build()
        mirror._setup_done = True
        mirror._creds = SimpleNamespace(token="archivisttoken")
        return mirror
    return built


@pytest.fixture
def homer(simpson, tmp_path) -> Person:  # noqa: F811
    return Person("homer", simpson, tmp_path)


def test_a_new_note_is_filed_with_its_sources_as_homers_note(memory, homer, model):  # noqa: F811
    """Given no page at `homer/notes/charcoal.md`, when Homer writes his words
    there, then the memory has one new commit, the pipeline's `capture:`, by
    Homer; the note it filed quotes his words under `## Sources` and has a
    sidecar that records them as sent on the command line; and Homer is
    told the card id and the path the pipeline chose.

    Why: a note without its sources cannot be refiled from its words, and
    the path asked for is not the path filed at, so the answer must say it.
    """
    model.answers({"title": "Grill needs charcoal", "tags": [], "persons": ["Homer"],
                   "clean": "The grill needs new charcoal before Saturday."})

    answer = homer.writes("homer/notes/charcoal.md", GRILL + "\n")

    assert answer.get("ok"), answer
    path, card_id = answer["path"], answer["card_id"]
    assert path.startswith("homer/notes/") and path != "homer/notes/charcoal.md"
    assert memory.new_commits() == [commit(by="homer", saying="capture: Grill needs charcoal")]
    card = memory.page(path)
    assert parse(card)["card_id"] == card_id
    assert GRILL in card.split("## Sources", 1)[1]
    sidecar = json.loads(memory.page(path.removesuffix(".md") + ".json"))
    assert [(s["role"], s["channel"]) for s in sidecar["sources"]] == [("original", "cli")]
    assert homer.was_told(card_id, path)
