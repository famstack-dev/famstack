"""`stack memory reprocess` from the surface: the command as the stack runs it.

The host half is loaded through the framework's plugin loader
(`Stack.run_cli_command`). The bot half runs for real too: the stand-in
for the bot runner's `docker exec` (`stack.bot_runner.call`) runs the
archivist's CLI entrypoint in this process, the card on its stdin, so
the sources, the classifier's prompt, the guard on the clean version and
the refusals are the shipped code. What stands in is what lies outside
the stack: the model, an HTTP server answering the chat completions API;
Synapse, the same server answering the client API as the archivist; and
Forgejo's file reads.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from werkzeug.wrappers import Response

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from test_memory_reprocess_cli import _memory  # noqa: E402

from stack.forgejo import ForgejoClient  # noqa: E402

ENTRYPOINT = _REPO_ROOT / "stacklets" / "docs" / "bot" / "cli_entrypoint.py"

TRIP = "family/itchy-scratchy-land/notes/2026/10/trip-26933f.md"
TRIP_WORDS = ("ok so for the trip: homer needs to call the hotel abt the late checkin, "
              "lisa wants the museum thing on sunday. budget max 200")
DRAWING = "bart/notes/2026/10/drawing-00c9ff.md"
DRAWING_EVENT = "$B5o6SGEFycKlkNOJ8lrBUS2z-67aTnqTZlhrAbGhbEc"
ROOM = "!family:simpson"

PEOPLE = {f"family/people/{who}.md": f"---\ntype: person\ncard_id: person-{who}\n"
          f"title: {who.capitalize()} Simpson\n---\n\n# {who.capitalize()} Simpson\n"
          for who in ("homer", "marge", "bart", "lisa")}


def _note(card_id: str, title: str, interpretation: str, quote: str, original: dict) -> tuple[str, str]:
    """A note as it is filed today: its card, and its sidecar with one source."""
    lines = "\n".join(f"> {ln}" if ln else ">" for ln in quote.split("\n"))
    card = (f"---\ntype: note\ncard_id: {card_id}\ntitle: {title}\n---\n\n# {title}\n\n"
            f"{interpretation}\n\n## Sources\n\n{lines}\n")
    sidecar = json.dumps({"card_id": card_id, "filed_by": original["person"],
                          "sources": [original], "made": {}}, indent=2) + "\n"
    return card, sidecar


TRIP_CARD, TRIP_SIDECAR = _note(
    "note-26933f6ef8", "Trip to Itchy & Scratchy Land", "For the trip: budget at most 200.",
    TRIP_WORDS, {"role": "original", "person": "marge", "time": "2026-10-20T08:00:00+00:00",
                 "channel": "chat", "kind": "text", "id": "$trip:simpson"})
# A photo note whose words a correction once filled with prompt text, as
# migration memory/0008 converted it: the card's copy is marked reconstructed.
DRAWING_CARD, DRAWING_SIDECAR = _note(
    "note-00c9ff9c9d", "Bart's drawing", "> [!summary]\n> A crayon drawing.",
    "![[/media/2026/10/drawing.jpg]]\n\nbart drew this\n\nExisting tags on this entry: art, school",
    {"role": "original", "person": "marge", "time": "2026-10-19T16:00:00+00:00",
     "channel": "chat", "kind": "image", "id": DRAWING_EVENT, "reconstructed": True})


# ── The stand-ins ─────────────────────────────────────────────────────────

class _Model:
    """The model: the chat completions API, answering one JSON object per
    request, the given ones in order and the last one again after that."""

    def __init__(self, httpserver, *answers: dict):
        self.prompts: list[str] = []

        def complete(request):
            self.prompts.append(json.loads(request.data)["messages"][-1]["content"])
            answer = answers[min(len(self.prompts), len(answers)) - 1]
            return Response(json.dumps({
                "id": "c1", "object": "chat.completion", "created": 0, "model": "stand-in",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": json.dumps(answer)}}],
            }), content_type="application/json")

        httpserver.expect_request("/v1/chat/completions", method="POST").respond_with_handler(complete)


def _synapse(httpserver, message: dict, thread: list[dict]) -> None:
    """Synapse as the archivist sees it: one room, the note's message and its thread."""
    httpserver.expect_request("/_matrix/client/v3/joined_rooms").respond_with_json(
        {"joined_rooms": [ROOM]})
    httpserver.expect_request(f"/_matrix/client/v3/rooms/{ROOM}/event/{message['event_id']}"
                              ).respond_with_json(message)
    httpserver.expect_request(f"/_matrix/client/v1/rooms/{ROOM}/relations/{message['event_id']}/m.thread"
                              ).respond_with_json({"chunk": thread})


def _event(event_id: str, sender: str, content: dict, ts: int = 1_760_890_000_000) -> dict:
    return {"type": "m.room.message", "event_id": event_id, "sender": sender,
            "origin_server_ts": ts, "content": content}


@pytest.fixture
def stack(tmp_path, monkeypatch, httpserver):
    """A stack whose memory holds `files`, with the bot half run in this process."""

    def make(files: dict[str, str], *, chat: bool = False):
        memory = _memory(tmp_path, monkeypatch, {**PEOPLE, **files})
        from stack import Stack
        from stack.output import CollectorOutput

        (tmp_path / "stack.toml").write_text(f'[core]\ndomain = ""\ndata_dir = "{memory.data_dir}"\n'
                                             '\n[messages]\nserver_name = "simpson"\n')
        (tmp_path / "stacklets").mkdir(exist_ok=True)
        (tmp_path / "stacklets" / "memory").symlink_to(_REPO_ROOT / "stacklets" / "memory")
        stck = Stack(root=tmp_path, data=memory.data_dir, output=CollectorOutput())
        stck.secrets.set("memory", "MEMORY_BOT_TOKEN", "bottoken")
        # Forgejo, at the class: the command's `lib` is loaded afresh from
        # the stacklet on every run, so only the class reaches every copy.
        monkeypatch.setattr(ForgejoClient, "get_file", memory.forgejo_api.get_file)

        def change_files(self, *args, **kwargs):
            answer = memory.forgejo_api.change_files(self, *args, **kwargs)
            # The curator, which projects each commit into the trees that are read.
            state = memory.data_dir / "memory" / "curator"
            state.mkdir(parents=True, exist_ok=True)
            (state / "last-mirrored-sha").write_text(answer["commit"]["sha"])
            return answer

        monkeypatch.setattr(ForgejoClient, "change_files", change_files)

        # What the bot runner's environment carries for the archivist.
        for key, value in {"PAPERLESS_URL": httpserver.url_for("/paperless"),
                           "PAPERLESS_TOKEN": "t", "OPENAI_URL": httpserver.url_for("/v1"),
                           "OPENAI_KEY": "k", "LANGUAGE": "en", "CODE_URL": "http://stack-code:3000",
                           "MATRIX_ADMIN_USER": "admin", "MATRIX_ADMIN_PASSWORD": "pw",
                           "MEMORY_VAULT_DIR": str(memory.vault), "STACK_COMMIT": "9a5bdc9"}.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr("stack.ai.models._DEFAULT_MODEL", "stand-in")
        entrypoint = _load_entrypoint(monkeypatch)
        session = tmp_path / "archivist-bot.session.json"
        if chat:
            session.write_text(json.dumps({"access_token": "archivist-token"}))
        monkeypatch.setattr(sys.modules["cli._chat"], "_SESSION_FILE", session)
        monkeypatch.setattr(sys.modules["cli._chat"], "_HOMESERVER", httpserver.url_for("").rstrip("/"))

        def docker_exec(path, command, *argv, input_text=None):
            assert path == "/stacklets/docs/bot/cli_entrypoint.py"
            out = io.StringIO()
            monkeypatch.setattr(sys, "stdin", io.StringIO(input_text or ""))
            with contextlib.redirect_stdout(out):
                code = asyncio.run(entrypoint.main([command, *argv]))
            if code != 0:
                raise RuntimeError(f"{command} failed (exit {code})")
            return out.getvalue()

        monkeypatch.setattr("stack.bot_runner.call", docker_exec)
        return stck, memory

    return make


def _load_entrypoint(monkeypatch):
    """The archivist's CLI entrypoint, as `docker exec … python cli_entrypoint.py` runs it."""
    for name in [n for n in sys.modules if n == "cli" or n.startswith("cli.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(ENTRYPOINT.parent))
    spec = importlib.util.spec_from_file_location("docs_cli_entrypoint", ENTRYPOINT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reprocess(stck, *args):
    return stck.run_cli_command("memory", "reprocess", list(args))


# ── A correction may change a number ──────────────────────────────────────

class TestACorrectionChangesWhatTheCleanVersionSays:
    """The clean version is checked against the note's sources: every
    number and name of the original is kept, unless a correction (a reply
    or a `--msg` note) replaces it with one of its own."""

    CLEAN_250 = "For the trip: Homer calls the hotel about the late check-in. Lisa wants the museum visit on Sunday. Budget: at most 250."

    def test_a_correction_that_gives_a_new_amount_replaces_the_old_one(self, stack, httpserver):
        stck, _ = stack({TRIP: TRIP_CARD, TRIP.removesuffix(".md") + ".json": TRIP_SIDECAR})
        _Model(httpserver, {"title": "Trip", "tags": ["trip"], "persons": ["Homer"],
                            "clean": self.CLEAN_250})

        result = _reprocess(stck, "note-26933f6ef8", "--msg", "budget is 250 now", "--by", "marge")

        assert "error" not in result, result
        assert result["clean_rejected"] == ""
        assert "Budget: at most 250." in result["proposed"]["file"]

    def test_a_clean_version_that_drops_an_amount_nobody_corrected_is_refused(self, stack, httpserver):
        stck, _ = stack({TRIP: TRIP_CARD, TRIP.removesuffix(".md") + ".json": TRIP_SIDECAR})
        _Model(httpserver, {"title": "Trip", "tags": ["trip"], "persons": ["Homer"],
                            "clean": self.CLEAN_250.replace(" Budget: at most 250.", "")})

        result = _reprocess(stck, "note-26933f6ef8")

        assert "200" in result["clean_rejected"]
        assert "Lisa wants the museum visit" not in result["proposed"]["file"]


# ── A photo note's words come from the chat ───────────────────────────────

class TestAPhotoNoteIsRebuiltFromTheChat:
    """A photo note's original is the message it was sent as: its caption
    is the message's `body` when the client sent a `filename` beside it,
    and its replies are the thread's. When the chat can be read, a card
    whose copy of them was damaged is rebuilt from it."""

    MESSAGE = _event(DRAWING_EVENT, "@marge:simpson", {
        "msgtype": "m.image", "body": "Bart drew this at school", "filename": "IMG_1.jpg",
        "url": "mxc://simpson/drawing"})
    THREAD = [_event("$filed", "@archivist-bot:simpson", {"msgtype": "m.text", "body": "Filed: Bart's drawing"}),
              _event("$r1", "@bart:simpson", {"msgtype": "m.text", "body": "it is a dog"}),
              _event("$r2", "@lisa:simpson", {"msgtype": "m.text", "body": "Santa's Little Helper"}),
              _event("$r3", "@homer:simpson", {"msgtype": "m.text", "body": "frame it"})]

    def test_the_damaged_card_is_rebuilt_from_the_message_and_its_thread(self, stack, httpserver):
        stck, _ = stack({DRAWING: DRAWING_CARD, DRAWING.removesuffix(".md") + ".json": DRAWING_SIDECAR},
                        chat=True)
        _synapse(httpserver, self.MESSAGE, self.THREAD)
        model = _Model(httpserver, {"title": "Bart's drawing of a dog", "tags": ["drawing"],
                                    "persons": ["Bart"], "summary": "A crayon dog."})

        result = _reprocess(stck, "note-00c9ff9c9d")

        assert "error" not in result, result
        sources = json.loads(result["proposed"]["sidecar"])["sources"]
        assert sources[0] == {"role": "original", "person": "marge", "time": "2025-10-19T16:06:40+00:00",
                              "channel": "chat", "kind": "image", "id": DRAWING_EVENT}
        assert [(s["role"], s["person"], s["id"]) for s in sources[1:]] == [
            ("reply", "bart", "$r1"), ("reply", "lisa", "$r2"), ("reply", "homer", "$r3")]
        assert "## Sources\n\n> ![[/media/2026/10/drawing.jpg]]\n>\n> Bart drew this at school\n" \
            in result["proposed"]["file"]
        assert "Existing tags on this entry" not in model.prompts[-1]

    def test_without_the_chat_the_refusal_says_what_the_person_can_do(self, stack):
        stck, _ = stack({DRAWING: DRAWING_CARD, DRAWING.removesuffix(".md") + ".json": DRAWING_SIDECAR})

        result = _reprocess(stck, "note-00c9ff9c9d")

        assert result["refusal"] == "words"
        assert "by hand" not in result["error"]
        assert "chat" in result["error"] and "send" in result["error"]


# ── Who a refile's commit is by ───────────────────────────────────────────

TRIP_SIDE = TRIP.removesuffix(".md") + ".json"


def _applied(stck, *args) -> dict:
    """Preview a refile with `args`, then apply that proposal."""
    preview = _reprocess(stck, "note-26933f6ef8", *args)
    assert "error" not in preview, preview
    return _reprocess(stck, "--apply", preview["proposal_id"])


class TestARefileIsCommittedByWhoeverItsWordsAreFrom:
    """A refile without a correction is a machine rebuild: the memory bot
    commits it, as it commits a migration, and names who asked for it. A
    refile with a correction carries a person's words, so it is that
    person's commit (ADR-018 § 6, ADR-015)."""

    ANSWER = {"title": "Trip", "tags": ["trip"], "persons": ["Homer"],
              "clean": "For the trip: Homer calls the hotel about the late check-in. "
                       "Lisa wants the museum visit on Sunday. Budget: at most 200."}

    def test_a_plain_refile_is_the_memory_bots_commit_naming_the_admin_who_asked(
            self, stack, httpserver):
        """Given a filed note, when the admin at the terminal refiles it and
        applies the preview, then one commit by the memory bot lands on top,
        `reprocess: <title>`, naming the commit it replaced and who asked,
        and the sidecar names the same person. Why: the history must not
        say Marge rewrote her note when a machine did, and must still say
        whom to ask about it."""
        stck, memory = stack({TRIP: TRIP_CARD, TRIP_SIDE: TRIP_SIDECAR})
        _Model(httpserver, self.ANSWER)

        result = _applied(stck)

        assert result.get("ok"), result
        assert memory.log("--format=%P", "-1") == memory.filed, "one commit on top"
        assert memory.log("--format=%an <%ae>|%s", "-1") == \
            "memory-bot <memory-bot@local>|reprocess: Trip"
        trailers = memory.log("--format=%(trailers)", "-1").splitlines()
        assert trailers == [f"Replaces: {memory.filed}", "Requested-By: admin"]
        made = json.loads((memory.vault / TRIP_SIDE).read_text())["made"]
        assert made["requested_by"] == "admin"

    def test_a_plain_refile_names_the_person_who_asked_for_it(self, stack, httpserver):
        """Given a filed note, when Lisa is named as the one who asked
        (`--by lisa`), then the memory bot's commit and the sidecar name
        her. Why: a refile asked for from somewhere other than the terminal
        says who asked, not "admin"."""
        stck, memory = stack({TRIP: TRIP_CARD, TRIP_SIDE: TRIP_SIDECAR})
        _Model(httpserver, self.ANSWER)

        _applied(stck, "--by", "lisa")

        assert memory.log("--format=%an", "-1") == "memory-bot"
        assert "Requested-By: lisa" in memory.log("--format=%(trailers)", "-1").splitlines()
        assert json.loads((memory.vault / TRIP_SIDE).read_text())["made"]["requested_by"] == "lisa"

    def test_a_refile_with_a_correction_is_the_persons_commit(self, stack, httpserver):
        """Given a filed note, when Marge refiles it with a correction
        (`--msg ... --by marge`) and the preview is applied, then one commit
        by Marge lands on top, carrying her words. Why: a correction is a
        person's change to the record, and the history is where it is
        theirs (ADR-015)."""
        stck, memory = stack({TRIP: TRIP_CARD, TRIP_SIDE: TRIP_SIDECAR})
        _Model(httpserver, {**self.ANSWER, "clean": self.ANSWER["clean"].replace("200", "250")})

        _applied(stck, "--msg", "budget is 250 now", "--by", "marge")

        assert memory.log("--format=%P", "-1") == memory.filed, "one commit on top"
        assert memory.log("--format=%an <%ae>|%s", "-1") == "marge <marge@simpson>|reprocess: Trip"
        body = memory.log("--format=%b", "-1")
        assert "budget is 250 now" in body
        assert f"Replaces: {memory.filed}" in body


# ── A correction is worked into the clean version ─────────────────────────

LIST_TRIP = "family/itchy-scratchy-land/notes/2026/10/trip-list-5b1e07.md"
LIST_WORDS = ("for the trip: homer needs to call the hotel abt the late checkin, bart forgot "
              "his swimsuit again so buy one at the mall before saturday, lisa wants the museum "
              "thing on sunday not saturday. budget max 200")
LIST_CARD, LIST_SIDECAR = _note(
    "note-5b1e07c4a2", "Trip to-dos",
    "For the trip:\n- Homer needs to call the hotel about the late check-in.\n"
    "- Bart forgot his swimsuit again; buy one at the mall before Saturday.\n"
    "- Lisa wants the museum visit on Sunday, not Saturday.\n- Budget max 200.",
    LIST_WORDS, {"role": "original", "person": "marge", "time": "2026-10-20T08:00:00+00:00",
                 "channel": "chat", "kind": "text", "id": "$list:simpson"})
CORRECTION = "budget is 250 now, and lisa's museum is the science museum"


class TestACorrectionIsWorkedIntoTheCleanVersion:
    """The clean version is the note as corrected, as if it had been
    written right the first time: a correction changes the points it is
    about, and only something new becomes a point of its own. The model
    writes the clean version and cannot be forced; what the code controls
    is what the model is told, and that an answer which does this is
    filed."""

    FOLDED = ("For the trip:\n- Homer needs to call the hotel about the late check-in.\n"
              "- Bart forgot his swimsuit again; buy one at the mall before Saturday.\n"
              "- Lisa wants the science museum visit on Sunday, not Saturday.\n"
              "- Budget max 250.")

    def _refile(self, stack, httpserver, clean: str):
        stck, _ = stack({LIST_TRIP: LIST_CARD, LIST_TRIP.removesuffix(".md") + ".json": LIST_SIDECAR})
        model = _Model(httpserver, {"title": "Trip to-dos", "tags": ["trip"],
                                    "persons": ["Homer", "Bart", "Lisa"], "clean": clean})
        return _reprocess(stck, "note-5b1e07c4a2", "--msg", CORRECTION, "--by", "marge"), model

    def test_the_model_is_told_a_correction_replaces_what_it_corrects(self, stack, httpserver):
        """Given a note, when Marge refiles it with a correction, then the
        prompt that corrects the note carries her correction and says it
        replaces what it corrects rather than being appended. Why: told only
        to follow it, the model on the rig kept the old points and added the
        correction as a fifth."""
        result, model = self._refile(stack, httpserver, self.FOLDED)

        assert "error" not in result, result
        prompt = model.prompts[0]
        assert f"Marge: {CORRECTION}" in prompt
        assert "replace" in prompt.lower() and "append" in prompt.lower()

    def test_a_clean_version_with_the_correction_worked_in_is_filed(self, stack, httpserver):
        """Given the same refile, when the model answers the four points
        with the budget and the museum corrected in place, then the guard
        accepts it and the card shows those four points. Why: the guard
        must not refuse the corrected version the prompt asks for."""
        result, _ = self._refile(stack, httpserver, self.FOLDED)

        assert result["clean_rejected"] == ""
        card = result["proposed"]["file"]
        assert self.FOLDED in card
        assert "Budget max 200." not in card.split("## Sources")[0]


# ── A correction is written by a prompt of its own ────────────────────────

def _interpretation(card: str) -> str:
    """What the card says above its sources: the title, then the clean version."""
    return card.split("## Sources")[0]


class TestACorrectedNoteIsWrittenByAPromptOfItsOwn:
    """A note refiled with a correction is made in two steps. First the
    model is given the note's original words and the corrections, each
    with who wrote it, and asked for the note's clean version as
    corrected. Then the capture prompt reads that text as if it were the
    note, so the title, the tags and the persons are taken from the text
    the card shows and cannot contradict it. The corrected text is checked
    like any clean version, and also for every value a correction gives.
    A note without a correction is one call, as before."""

    CORRECTED = ("For the trip: Homer calls the hotel about the late check-in. "
                 "Lisa wants the museum visit on Sunday. Budget: at most 250.")
    CLASSIFIED = {"title": "Trip", "tags": ["trip"], "persons": ["Homer", "Lisa"]}

    def _refile(self, stack, httpserver, *answers, msg: str = "budget is 250 now"):
        stck, _ = stack({TRIP: TRIP_CARD, TRIP_SIDE: TRIP_SIDECAR})
        model = _Model(httpserver, *answers)
        args = ("--msg", msg, "--by", "marge") if msg else ()
        result = _reprocess(stck, "note-26933f6ef8", *args)
        assert "error" not in result, result
        return result, model

    def test_with_a_correction_the_capture_prompt_reads_the_corrected_text(self, stack, httpserver):
        """Given a note and Marge's correction, when it is refiled, then the
        model is asked twice: first with the original words and her
        correction, then with the corrected text alone. Why: asked for
        everything at once, the model appended the correction as a point of
        its own, and a title read from the original could contradict the
        corrected text."""
        _, model = self._refile(stack, httpserver, {"clean": self.CORRECTED}, self.CLASSIFIED)

        assert len(model.prompts) == 2
        correcting, classifying = model.prompts
        assert TRIP_WORDS in correcting and "Marge: budget is 250 now" in correcting
        assert self.CORRECTED in classifying
        assert TRIP_WORDS not in classifying
        assert "budget is 250 now" not in classifying

    def test_the_card_shows_the_corrected_text(self, stack, httpserver):
        result, _ = self._refile(stack, httpserver, {"clean": self.CORRECTED}, self.CLASSIFIED)

        assert result["clean_rejected"] == ""
        card = result["proposed"]["file"]
        assert self.CORRECTED in _interpretation(card)
        assert "title: Trip\n" in card

    def test_the_sidecar_names_the_correction_prompt_and_the_capture_prompt(self, stack, httpserver):
        """Why: a card says which prompts made it, so a card made by an old
        correction prompt can be found and refiled."""
        result, _ = self._refile(stack, httpserver, {"clean": self.CORRECTED}, self.CLASSIFIED)

        made = json.loads(result["proposed"]["sidecar"])["made"]
        assert made["correction_template"] and made["prompt_template"]
        assert made["correction_template"] != made["prompt_template"]

    def test_a_corrected_text_that_drops_an_amount_nobody_corrected_is_refused(self, stack, httpserver):
        """Given a correction about the museum only, when the corrected text
        the model answers has lost the budget of 200, then it is not filed:
        the card shows the note's words alone, and the refusal says what was
        lost."""
        dropped = "For the trip: Homer calls the hotel. Lisa wants the science museum on Sunday."
        result, _ = self._refile(stack, httpserver, {"clean": dropped}, self.CLASSIFIED,
                                 msg="lisa's museum is the science museum")

        assert "200" in result["clean_rejected"]
        assert dropped not in result["proposed"]["file"]

    def test_a_corrected_text_without_the_value_the_correction_gives_is_refused(self, stack, httpserver):
        """Given "budget is 250 now", when the corrected text still says 200,
        then it did not take the correction and is not filed."""
        unchanged = self.CORRECTED.replace("250", "200")
        result, _ = self._refile(stack, httpserver, {"clean": unchanged}, self.CLASSIFIED)

        assert "250" in result["clean_rejected"]
        assert unchanged not in result["proposed"]["file"]

    def test_without_a_correction_the_model_is_asked_once(self, stack, httpserver):
        clean = self.CORRECTED.replace("250", "200")
        result, model = self._refile(stack, httpserver, {**self.CLASSIFIED, "clean": clean}, msg="")

        assert len(model.prompts) == 1
        assert TRIP_WORDS in model.prompts[0]
        assert clean in _interpretation(result["proposed"]["file"])
        assert "correction_template" not in json.loads(result["proposed"]["sidecar"])["made"]
