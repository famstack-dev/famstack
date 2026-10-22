"""A refile keeps what people changed: `stack memory reprocess` after a correction or an edit.

ADR-018 §7. A note's history is its event log. A refile interprets the
note again from its sources, and must not write over what people did to
it since it was filed: a correction (`--msg`, a reply), an edit of its
clean version, an edit of its fields. A note with corrections or a
person's edits is rebuilt as a card conversation, the clean version
before the first of them and each event in time order; a person's field
edits are applied again after the model.

Marge and Homer run the commands as `./stack` does, and Homer replies
in the chat, which the archivist refiles. What is real: the commands and
their loader, the card write endpoint behind core's door, the curator's
clone the host reads the history from, the model half of the refile, the
archivist's own pipeline, run in this process as the bot runner's
`docker exec` would run it, and the archivist's refile of a reply, which
reads the history through Forgejo's API. What stands in: Forgejo over a
real git repository (`test_memory_card_links`, and here its commits of a
path and a file at a commit), the Matrix room the reply arrives in, and
the model, an HTTP server answering the chat completions API with the
answers a test gives.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from werkzeug.wrappers import Response

from stack.forgejo import ForgejoClient
from stack.frontmatter import parse

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
def history_api(memory, monkeypatch):  # noqa: F811
    """Forgejo's commits of a path, and a file at a commit, answered from
    the repository behind the stand-in, as Forgejo answers them."""
    read_now = ForgejoClient.get_file

    def get_file(self, owner, repo, path, ref="main"):
        if ref == "main":
            return read_now(self, owner, repo, path, ref)
        shown = subprocess.run(["git", "-C", str(memory.repo), "show", f"{ref}:{path}"],
                               capture_output=True, text=True)
        return {"content": shown.stdout, "sha": ref} if shown.returncode == 0 else None

    def list_commits(self, owner, repo, *, path=None, limit=20):
        log = subprocess.run(["git", "-C", str(memory.repo), "log", f"-{limit}",
                              "--format=%H%x1f%an%x1f%aI%x1f%cn%x1f%B%x1e", "--", path or "."],
                             capture_output=True, text=True, check=True).stdout
        records = [r.strip("\n").split("\x1f") for r in log.split("\x1e") if r.strip()]
        return [{"sha": sha, "commit": {"message": body, "author": {"name": who, "date": at},
                                        "committer": {"name": by}}}
                for sha, who, at, by, body in records]

    monkeypatch.setattr(ForgejoClient, "get_file", get_file)
    monkeypatch.setattr(ForgejoClient, "list_commits", list_commits)


class _Paperless:
    """Paperless, which the archivist asks for names when the memory has none."""

    async def get_tags(self):
        return []


def _replies_in_the_chat(the_archivist, httpserver, note, *, by: str, words: str):
    """`by` replies to the note's filing in the chat, and the archivist
    refiles it (`CapturePipeline.reprocess`, what its reply handler calls)."""
    with _in_the_bot_runner(), _own_cli_package():
        from capture_pipeline import CapturePipeline
        from openai import AsyncOpenAI
        from pipeline import LLM, Classifier

        llm = LLM(AsyncOpenAI(base_url=httpserver.url_for("/v1"), api_key="k"),
                  namespace="archivist-bot")
        pipeline = CapturePipeline(
            url_extractor=None, text_extractor=None, classifier=Classifier(llm, language="en"),
            mirror=the_archivist.mirror, capture_tags=None, paperless=_Paperless(),
            bot_name="archivist-bot", classify_max_chars=100_000, capture_keep_body=False,
            capture_tag_prompt_size=50)
        return asyncio.run(pipeline.reprocess(
            vault_path=note.path, user_hint=words, sender_mxid=f"@{by}:simpson",
            reply_text=words, reply_id="$reply:simpson", reply_at=_now()))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def _tags(repo, path: str) -> list[str]:
    return list(parse(repo.page(path)).get("tags") or [])


def _shown(page: str) -> str:
    """What the card says above its sources."""
    return page.split("## Sources")[0]


def _classified(**fields) -> dict:
    return {"title": TITLE, "tags": ["camping"], "persons": [], **fields}


# ── A person's field edits ───────────────────────────────────────────────

def test_a_tag_marge_added_stays_through_a_refile_and_the_models_other_tags_come_in(
        memory, archivist, marge, model):  # noqa: F811
    """Given Homer's note tagged `camping`, when Marge adds `tent` with
    `stack memory card edit` and the note is refiled, then it has her tag
    and the tags the model gave. Why: a refile interprets the note again,
    and a tag a person chose is not the model's to take back; the edit is
    applied again as an addition, so the model's own suggestions still
    come in."""
    note = _filed(archivist, tags=["camping"])
    marge.edits(note.card_id, "--add", "tags=tent")
    model.answers(_classified(tags=["camping", "outdoors"], clean=CLEAN))

    refiled = marge.refiles(note.card_id)

    assert refiled.get("ok"), refiled
    assert sorted(_tags(memory, note.path)) == ["camping", "outdoors", "tent"]
    assert len(model.requests) == 1, "a field edit is no edit of the text: one call, as today"


def test_a_tag_marge_removed_stays_removed_when_the_model_suggests_it_again(
        memory, archivist, marge, model):  # noqa: F811
    """Given Homer's note tagged `camping` and `tent`, when Marge removes
    `camping` and the model suggests it again on a refile, then the note
    is not tagged `camping`, and the model's new `outdoors` comes in."""
    note = _filed(archivist, tags=["camping", "tent"])
    marge.edits(note.card_id, "--remove", "tags=camping")
    model.answers(_classified(tags=["camping", "tent", "outdoors"], clean=CLEAN))

    marge.refiles(note.card_id)

    assert sorted(_tags(memory, note.path)) == ["outdoors", "tent"]


def test_a_tag_marge_added_stays_when_homer_corrects_the_note_in_the_chat(
        memory, archivist, marge, model, httpserver, history_api):  # noqa: F811
    """Given Homer's note tagged `camping`, when Marge adds `tent` with
    `stack memory card edit` and Homer then replies in the chat with a
    correction, then the archivist's refile keeps her tag, and the
    correction is in the card. Why: a refile from the chat reads the
    title, tags and persons again, and the bot runner has no clone to read
    a person's edits from; it reads the card's history through Forgejo, so
    a reply replays the same edits a refile at the terminal does."""
    note = _filed(archivist, tags=["camping"])
    marge.edits(note.card_id, "--add", "tags=tent")
    model.answers({"clean": AT_5}, _classified(tags=["camping", "outdoors"]))

    outcome = _replies_in_the_chat(archivist, httpserver, note, by="homer",
                                   words="we leave at 5, not 4")

    assert outcome.status == "reclassified", outcome
    assert sorted(_tags(memory, note.path)) == ["camping", "outdoors", "tent"]
    assert AT_5 in _shown(memory.page(note.path))


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
