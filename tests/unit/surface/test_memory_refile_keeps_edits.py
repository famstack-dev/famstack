"""A refile keeps what people changed: `stack memory reprocess` after a correction or an edit.

ADR-018 §7. A note's history is its event log. A refile interprets the
note again from its sources, and must not write over what people did to
it since it was filed: a correction (`--msg`, a reply), an edit of its
clean version. A note a person edited is rebuilt as a card conversation,
the clean version before the first correction or edit and each event in
time order.

Marge and Homer run the commands as `./stack` does. What is real: the
commands and their loader, the card write endpoint behind core's door,
the curator's clone the host reads the history from, and the model half
of the refile, the archivist's own pipeline, run in this process as the
bot runner's `docker exec` would run it. What stands in: Forgejo over a
real git repository (`test_memory_card_links`), and the model, an HTTP
server answering the chat completions API with the answers a test gives.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import sys
from datetime import datetime, timezone

import pytest
from werkzeug.wrappers import Response


from .conftest import STACKLETS
from .dsl import Person
from .test_memory_card_edit import core_door  # noqa: F401
from .test_memory_commit_author import (  # noqa: F401
    _in_the_bot_runner, archivist, containers, curator, endpoint, marge, memory, server_name,
    simpson)
from test_memory_card_links import _git, contents, forgejo  # noqa: E402, F401  (on the path since commit_author)

ENTRYPOINT = STACKLETS / "docs" / "bot" / "cli_entrypoint.py"

WORDS = ("camping trip next weekend:\nflanders lends us his tent\nwe leave fri at 4\n"
         "bring 3 sleeping bags\nbart packs the flashlights")
CLEAN = ("Camping trip next weekend:\n\n- Flanders lends us his tent.\n- We leave on Friday at 4.\n"
         "- Bring 3 sleeping bags.\n- Bart packs the flashlights.")
AT_5 = CLEAN.replace("at 4.", "at 5.")
TITLE = "Camping trip"


# ── The model, and the bot runner's half of a refile ─────────────────────

class _Model:
    """The model: the chat completions API, answering the JSON objects a
    test gives in order, the last one again after that, and keeping every
    request's messages."""

    def __init__(self, httpserver):
        self.requests: list[list[dict]] = []
        self._answers: list[dict] = [{}]
        self._from = 0

        def complete(request):
            self.requests.append(json.loads(request.data)["messages"])
            answer = self._answers[min(len(self.requests) - self._from, len(self._answers)) - 1]
            return Response(json.dumps({
                "id": "c1", "object": "chat.completion", "created": 0, "model": "stand-in",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": json.dumps(answer)}}],
            }), content_type="application/json")

        httpserver.expect_request("/v1/chat/completions", method="POST").respond_with_handler(complete)

    def answers(self, *answers: dict) -> None:
        """What the next requests are answered, in order."""
        self._answers, self._from = list(answers), len(self.requests)


@pytest.fixture
def model(httpserver) -> _Model:
    return _Model(httpserver)


@pytest.fixture
def bot_half(curator, httpserver, monkeypatch, tmp_path):  # noqa: F811
    """The bot runner's `docker exec` of a refile's model half: the
    archivist's CLI entrypoint, run here with the environment the bot
    runner gives it, its clone of the memory the curator's."""
    for key, value in {"OPENAI_URL": httpserver.url_for("/v1"), "OPENAI_KEY": "k",
                       "LANGUAGE": "en", "PAPERLESS_URL": httpserver.url_for("/paperless"),
                       "PAPERLESS_TOKEN": "t", "MATRIX_ADMIN_USER": "stackadmin",
                       "MATRIX_ADMIN_PASSWORD": "donuts", "MEMORY_VAULT_DIR": str(curator),
                       "STACK_COMMIT": "9a5bdc9"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("stack.ai.models._DEFAULT_MODEL", "stand-in")

    def call(path, command, *argv, input_text=None):
        assert path == "/stacklets/docs/bot/cli_entrypoint.py"
        out = io.StringIO()
        with _in_the_bot_runner(), _own_cli_package():
            spec = importlib.util.spec_from_file_location("docs_cli_entrypoint", ENTRYPOINT)
            entrypoint = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(entrypoint)
            # No archivist session: the chat thread is not read.
            sys.modules["cli._chat"]._SESSION_FILE = tmp_path / "no-session.json"
            with contextlib.redirect_stdout(out), _stdin(input_text or ""):
                code = asyncio.run(entrypoint.main([command, *argv]))
        if code != 0:
            raise RuntimeError(f"{command} failed (exit {code})")
        return out.getvalue()

    monkeypatch.setattr("stack.bot_runner.call", call)


@contextlib.contextmanager
def _own_cli_package():
    """`cli` as the archivist's package for one call: the host's commands
    are loaded under that name too, and are given back after."""
    held = {n: m for n, m in sys.modules.items() if n == "cli" or n.startswith("cli.")}
    for name in held:
        del sys.modules[name]
    try:
        yield
    finally:
        for name in [n for n in sys.modules if n == "cli" or n.startswith("cli.")]:
            del sys.modules[name]
        sys.modules.update(held)


@contextlib.contextmanager
def _stdin(text: str):
    before, sys.stdin = sys.stdin, io.StringIO(text)
    try:
        yield
    finally:
        sys.stdin = before


@pytest.fixture
def homer(simpson, tmp_path) -> Person:  # noqa: F811
    return Person("homer", simpson, tmp_path)


# ── The note, and what people do to it ───────────────────────────────────

def _filed(by_the_archivist, *, tags: list[str]):
    """Homer's camping note, filed by the archivist as he sent it, with a
    clean version of his words and these tags."""
    source = by_the_archivist.source("original", "homer", "2026-10-20T08:00:00+00:00", "chat", "text",
                              "$camping:simpson", quote=WORDS)
    with _in_the_bot_runner():
        return asyncio.run(by_the_archivist.mirror.publish_capture(
            entity="homer", kind="note", source_uri=None, title_hint=None, body_text=WORDS,
            classification={"title": TITLE, "summary": ""}, captured_at="2026-10-20",
            model="stand-in", tags=tags, capture_id=source.id, submitter="@homer:simpson",
            clean=CLEAN, sources=[source], made={"at": "2026-10-20T08:00:05+00:00"}))


def _edits_the_text(repo, clone, path: str, old: str, new: str, *, by: str) -> None:
    """`by` changes the note's clean version by hand in Forgejo (`repo`),
    and the curator's `clone` follows."""
    page = repo.page(path)
    assert page.count(old) == 1
    repo.changes(f"Update '{path}'", by=by, at=datetime.now(timezone.utc),
                 files={path: page.replace(old, new)})
    _git(clone, "pull", "-q", "--ff-only")


def _shown(page: str) -> str:
    """What the card says above its sources."""
    return page.split("## Sources")[0]


def _classified(**fields) -> dict:
    return {"title": TITLE, "tags": ["camping"], "persons": [], **fields}


# ── Corrections and a person's text edits ────────────────────────────────

def test_a_correction_then_marges_edit_of_another_point_are_given_to_the_model_in_order(
        memory, curator, archivist, homer, marge, model):  # noqa: F811
    """Given Homer's note, Homer's correction `we leave at 5, not 4`, and
    then Marge's edit of the clean version by hand (4 sleeping bags, not
    3), when the note is refiled, then the model is asked with the card
    conversation: the task, Homer's words, the clean version as it was
    before the correction as the model's own answer, the correction, then
    Marge's edit, then the request; and the card shows what it answered.

    Why: a refile reads the note from its sources, and a person's edit is
    no source. Given as one more event after the correction, it is kept;
    rebuilt from the sources alone, it was lost. The note is refiled, not
    refused because somebody changed it by hand."""
    note = _filed(archivist, tags=["camping"])
    model.answers({"clean": AT_5}, _classified())
    homer.refiles(note.card_id, correction="we leave at 5, not 4")
    _edits_the_text(memory, curator, note.path, "- Bring 3 sleeping bags.",
                    "- Bring 4 sleeping bags.", by="marge")
    rebuilt = AT_5.replace("3 sleeping", "4 sleeping")
    model.answers({"clean": rebuilt}, _classified())

    refiled = marge.refiles(note.card_id)

    assert refiled.get("ok"), refiled
    asked = model.requests[2]
    assert [m["role"] for m in asked] == ["system", "user", "assistant", "user", "user", "user"]
    assert WORDS in asked[1]["content"] and "Homer" in asked[1]["content"]
    assert json.loads(asked[2]["content"]) == {"clean": CLEAN}
    assert [m["content"] for m in asked[3:5]] == [
        "Homer: we leave at 5, not 4",
        "Marge edited the card: changed 'Bring 3 sleeping bags.' to 'Bring 4 sleeping bags.'"]
    assert rebuilt in _shown(memory.page(note.path))


def test_a_note_nobody_corrected_or_edited_is_asked_once_as_today(memory, archivist, marge, model):  # noqa: F811
    """Given Homer's note as it was filed, when it is refiled, then the
    model is asked once, one message with his words: the capture prompt,
    which writes the clean version and the title and tags together."""
    note = _filed(archivist, tags=["camping"])
    model.answers(_classified(clean=CLEAN))

    marge.refiles(note.card_id)

    assert len(model.requests) == 1
    [message] = model.requests[0]
    assert message["role"] == "user" and WORDS in message["content"]
