"""Every message the stack posts into the chat is in the stack's language.

A family whose `stack.toml` says `[core] language = "de"` meets no English
in the chat: the welcomes, the bots' introductions, the Server room's
notices, the device notices and the bots' own answers are German. Any
other language gets English, as before.

Each sender is driven through its surface: `./stack messages setup` as
`stack up messages` runs it (through `Stack.run_cli_command`, against a
stand-in Synapse on localhost, `tests/synapse.py`). Which language a text
is in is judged by its common words (`language_of`), not by its wording,
so a reworded message does not break a test.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from tests.springfield import make_stack, write_instance
from tests.synapse import Synapse

SERVER = "springfield.example"

# ── Telling German from English ──────────────────────────────────────────

# Short words that nearly every sentence of the language has and the other
# language does not, and the verbs of the stack's one-line notices. A text
# is the language whose words it has more of.
_COMMON = {
    "de": {"der", "die", "das", "und", "ist", "ich", "du", "dich", "dir", "dein", "deine",
           "euer", "eure", "euch", "nicht", "mit", "für", "auf", "hier", "ein", "eine",
           "wird", "wurde", "kann", "sind", "zu", "den", "dem", "im", "wir", "oder", "wie",
           "ließ", "sich", "läuft", "wieder", "bereit", "gestoppt", "deinstalliert", "hat",
           "leider", "etwas", "dateien", "dauer", "ziel", "abgeschlossen", "neu"},
    "en": {"the", "and", "is", "you", "your", "to", "of", "in", "this", "it", "for",
           "with", "here", "can", "will", "are", "an", "not", "or", "what", "how", "be",
           "was", "failed", "stopped", "uninstalled", "ready", "back", "online", "has",
           "sorry", "something", "went", "wrong", "that", "completed", "target", "files",
           "new", "duration"},
}


def language_of(text: str) -> str:
    """"de" or "en": the language whose common words the text has more of;
    "?" when it has as many of one as of the other."""
    words = re.findall(r"[a-zäöüß]+", text.lower())
    de, en = (sum(w in _COMMON[lang] for w in words) for lang in ("de", "en"))
    return "de" if de > en else "en" if en > de else "?"


def test_language_of_tells_the_two_apart():
    """The judge itself: a German and an English sentence are told apart."""
    assert language_of("Hier findet ihr eure Erinnerungen, und du kannst sie lesen.") == "de"
    assert language_of("Here you find your memories, and you can read them.") == "en"
    assert language_of("Kite was stopped.") == "en"
    assert language_of("Kite wurde gestoppt.") == "de"


# ── The welcomes of `stack messages setup` ───────────────────────────────

@pytest.fixture
def setup_run(tmp_path, monkeypatch, httpserver):
    """Run `stack messages setup` on a Simpsons instance in `language`,
    against a stand-in Synapse, from a directory that is not the
    instance's; returns the stand-in with what was posted, and the
    instance. `bot_password` is the stacker bot's stored password, None
    for a first setup that makes one."""
    def run(language: str, *, bot_password: str | None = "duff"):
        stored = {"core__STACKER_BOT_PASSWORD": bot_password} if bot_password else {}
        instance = write_instance(tmp_path, language=language, secrets=stored,
                                  core='stack_owner = "Simpson"')
        synapse = Synapse(httpserver, server=SERVER, admin_password="donuts")
        stack = make_stack(instance)
        # The command finds Synapse at the manifest's port; the stand-in
        # listens on the test server's port instead.
        discover = stack.discover

        def discover_with_stand_in():
            found = discover()
            for s in found:
                if s["id"] == "messages":
                    s["manifest"].setdefault("ports", {})["synapse"] = httpserver.port
            return found
        monkeypatch.setattr(stack, "discover", discover_with_stand_in)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        result = stack.run_cli_command("messages", "setup")
        assert result and result.get("ok"), result
        return synapse, instance
    return run


def test_setup_welcomes_a_german_family_in_german(setup_run):
    """Given `[core] language = "de"`, when `stack messages setup` runs as
    `stack up messages` runs it, then the welcomes in the Server room and
    in the Memories room are German. Both arrived in English on a German
    install."""
    synapse, _ = setup_run("de")

    for room in ("famstack", "memories"):
        posted = synapse.posted_to(room)
        assert posted, f"nothing was posted into #{room}"
        assert [language_of(text) for text in posted] == ["de"] * len(posted), posted
    assert "Simpson" in synapse.posted_to("famstack")[0], "the welcome names no family"


def test_setup_welcomes_any_other_language_in_english(setup_run):
    """Given `[core] language = "fr"`, the welcomes are the English ones."""
    synapse, _ = setup_run("fr")

    for room in ("famstack", "memories"):
        posted = synapse.posted_to(room)
        assert posted and {language_of(text) for text in posted} == {"en"}, posted


def test_setup_stores_the_bots_password_in_the_instance(setup_run, tmp_path):
    """Given no stored password for the stacker bot, when `stack messages
    setup` runs from another directory, then the password it makes is in
    the instance's secrets, so the bot runner finds it, and nothing is
    written where the command ran."""
    _, instance = setup_run("de", bot_password=None)

    stored = (instance / ".stack" / "secrets.toml").read_text()
    assert "core__STACKER_BOT_PASSWORD" in stored
    assert not (tmp_path / "elsewhere" / ".stack").exists()


# ── The Server room's notices from `./stack` ─────────────────────────────

@pytest.fixture
def stack_cli(tmp_path, monkeypatch, docker_with_no_containers):
    """`./stack` on a Simpsons instance in `language` with one extension,
    `kite`, that has no containers and fails to start while its
    `on_start` finds `<tmp>/broken`. Returns `(run, told)`: `run(*args)`
    runs a command, `told` collects what it hands to the Server room."""
    from stack import cli

    from .conftest import StackCLI

    def build(language: str):
        extensions = tmp_path / "extensions"
        kite = extensions / "kite"
        (kite / "hooks").mkdir(parents=True)
        (kite / "stacklet.toml").write_text('id = "kite"\nname = "Kite"\ntype = "host"\n')
        (kite / "hooks" / "on_start.py").write_text(
            "from pathlib import Path\n"
            "def run(ctx):\n"
            f"    if Path({str(tmp_path / 'broken')!r}).exists():\n"
            "        raise RuntimeError('the kite is tangled')\n")
        instance = write_instance(tmp_path, language=language, extension_dirs=[extensions])
        told: list[str] = []
        # Where a text leaves for the chat: `stack messages send famstack`.
        monkeypatch.setattr(cli, "_notify", lambda _stck, text: told.append(text))
        monkeypatch.setattr(cli, "_send_notice",
                            lambda _stck, notice: told.append(notice["text"]) or {"sent": True})
        monkeypatch.setattr(cli, "_refresh_core", lambda *a, **k: None)
        return StackCLI(instance, monkeypatch), told
    return build


@pytest.mark.parametrize("language, expected", [("de", "de"), ("fr", "en")])
def test_the_server_room_hears_of_a_failed_start_and_a_stop_in_the_stacks_language(
        stack_cli, tmp_path, language, expected):
    """Given the stack's language, when a stacklet fails to start and is
    stopped, then each line the Server room hears is in that language
    (English for any language but German). The lines of a start that
    worked are pinned by `test_install_pass_de.py`."""
    run, told = stack_cli(language)

    (tmp_path / "broken").touch()
    run("up", "kite")
    run("down", "kite")

    assert len(told) == 2, told
    assert [language_of(text) for text in told] == [expected] * 2, told


@pytest.mark.parametrize("language, expected", [("de", "de"), ("fr", "en")])
def test_a_waiting_browser_is_announced_in_the_stacks_language(stack_cli, language, expected):
    """Given the stack's language, when a browser waits to join, then the
    Server room's notice is in it, and still carries the code."""
    run, told = stack_cli(language)

    ran = run("devices", "notice", "--kind", "waits", "--browser", "Chrome",
              "--system", "Mac", "--code", "K7MP4XQD", "--since", "21:14")

    assert ran.code == 0, ran
    assert len(told) == 1 and "K7MP-4XQD" in told[0], told
    assert language_of(told[0]) == expected, told


# ── The bots' introductions and their own answers ────────────────────────

class _RecordingClient:
    """The bot's Matrix client at its edge: records what the bot sends."""

    def __init__(self):
        self.sent: list[str] = []

    def add_event_callback(self, callback, event_type):
        pass

    async def room_send(self, room_id, message_type, content, **_):
        if message_type == "m.room.message":
            self.sent.append(content.get("body", ""))


def _bot(cls, tmp_path, user_id):
    """`cls` built as the bot runner builds it, with a recording client."""
    bot = cls(homeserver="http://hs", user_id=user_id, password="x", session_dir=str(tmp_path))
    bot._client = _RecordingClient()
    return bot, bot._client


def _message(body, *, sender="@homer:springfield.example"):
    return SimpleNamespace(body=body, sender=sender, event_id="$e", source={"content": {}})


@pytest.mark.asyncio
@pytest.mark.parametrize("language, expected", [("de", "de"), ("fr", "en")])
async def test_the_mail_bot_introduces_itself_in_the_stacks_language(
        tmp_path, monkeypatch, language, expected):
    """Given the stack's language, when the mail bot joins a room no
    mailbox routes to, then its introduction is in that language."""
    from stacklets.core.bot.mail import MailBot
    monkeypatch.setenv("LANGUAGE", language)
    bot, client = _bot(MailBot, tmp_path, "@mail-bot:springfield.example")

    await bot.on_room_joined("!kitchen:springfield.example")

    assert len(client.sent) == 1 and "[mail]" in client.sent[0], client.sent
    assert language_of(client.sent[0]) == expected, client.sent


@pytest.mark.asyncio
@pytest.mark.parametrize("language, expected", [("de", "de"), ("fr", "en")])
async def test_the_stacker_bot_answers_help_in_the_stacks_language(
        tmp_path, monkeypatch, language, expected):
    """Given the stack's language, when someone asks the stacker bot for
    help in the Server room, then the answer is in that language and
    still names the commands as typed."""
    from stacklets.core.bot.stacker import StackerBot
    monkeypatch.setenv("LANGUAGE", language)
    bot, client = _bot(StackerBot, tmp_path, "@stacker-bot:springfield.example")

    await bot._on_message(SimpleNamespace(room_id="!server"), _message("stack help"))

    assert len(client.sent) == 1 and "`stack status`" in client.sent[0], client.sent
    assert language_of(client.sent[0]) == expected, client.sent


@pytest.mark.asyncio
@pytest.mark.parametrize("language, expected", [("de", "de"), ("fr", "en")])
async def test_a_bots_failure_is_told_in_the_stacks_language(
        tmp_path, monkeypatch, language, expected):
    """Given the stack's language, when a bot's handler fails, then the
    apology the framework posts under the message is in that language."""
    from stacklets.core.bot.stacker import StackerBot
    monkeypatch.setenv("LANGUAGE", language)
    bot, client = _bot(StackerBot, tmp_path, "@stacker-bot:springfield.example")

    await bot._send_error("!server", _message("stack status"), RuntimeError("boom"))

    assert len(client.sent) == 1 and "boom" not in client.sent[0], client.sent
    assert language_of(client.sent[0]) == expected, client.sent


@pytest.mark.parametrize("language, expected", [("de", "de"), ("fr", "en")])
def test_the_backup_report_is_in_the_stacks_language(language, expected):
    """Given the stack's language, the backup's report in the Server room
    is in it; the numbers and the target's name stay as they are."""
    from stacklets.backup.cli._orchestrator import format_notification
    result = {"success": True, "duration_seconds": 75, "vault_size": "12 GB",
              "run_user": "homer", "run_context": "cron", "vault_state": "ejected",
              "sources": [{"id": "photos/library", "display": "Photos", "status": "ok",
                           "total_files": 1200, "new_files": 3}]}

    plain, html = format_notification("Simpson disk", result, language)

    assert "Simpson disk" in plain
    assert language_of(plain) == expected and language_of(html) == expected, plain


# ── No English text slips back in ────────────────────────────────────────
#
# This part reads the code, not a surface: it is the check that a new
# message cannot be added in English only. A module posts into a room when
# it calls one of `_SENDERS`. In such a module, a call that posts an
# English sentence written into the call is refused, and so is a table of
# texts by language (`{"en": ..., "de": ...}`) whose German side misses an
# English entry. A text built elsewhere and passed by name is seen through
# its table only: that is why each module keeps its texts in one.

from pathlib import Path  # noqa: E402

import ast  # noqa: E402
import yaml  # noqa: E402

from tests.springfield import REPO  # noqa: E402

# The calls that post into a room: the bots' (`MicroBot`), the host's
# Server room notices (`stack.cli`), and a host command's Matrix client.
_SENDERS = {"_send", "_answer", "_send_reply", "_room_send", "room_send", "post_source_message",
            "_notify", "_send_notice", "send"}


def _modules() -> list[Path]:
    """Every Python module the stack ships: the framework and the stacklets."""
    return sorted(p for root in (REPO / "lib" / "stack", REPO / "stacklets")
                  for p in root.rglob("*.py") if "tests" not in p.parts)


def _called(node: ast.Call) -> str:
    return node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")


def _literal(node) -> str | None:
    """The text of a string or f-string literal (its fixed parts), else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return " ".join(v.value for v in node.values if isinstance(v, ast.Constant))
    return None


def _posted_literals(tree) -> list[tuple[int, str]]:
    """(line, text) of each English sentence written into a call that posts."""
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _called(node) in _SENDERS):
            continue
        texts = [*node.args, *(k.value for k in node.keywords)]
        for arg in list(texts):
            if isinstance(arg, ast.Dict):   # a `room_send` content: its body
                texts += [v for k, v in zip(arg.keys, arg.values)
                          if isinstance(k, ast.Constant) and k.value == "body"]
        for arg in texts:
            text = _literal(arg)
            if text and language_of(text) == "en":
                found.append((node.lineno, text))
    return found


def _tables(tree) -> list[ast.Dict]:
    """The tables of texts by language: dict literals keyed "en" and "de"."""
    return [node for node in ast.walk(tree) if isinstance(node, ast.Dict)
            and {"en", "de"} <= {k.value for k in node.keys if isinstance(k, ast.Constant)}]


def _missing_german(table: ast.Dict) -> set[str]:
    """The entries of the table's English side that its German side lacks."""
    sides = {k.value: v for k, v in zip(table.keys, table.values) if isinstance(k, ast.Constant)}

    def keys(side):
        return {k.value for k in side.keys if isinstance(k, ast.Constant)} \
            if isinstance(side, ast.Dict) else set()
    return keys(sides["en"]) - keys(sides["de"])


def test_no_module_posts_an_english_sentence_written_into_the_call():
    """Given every module that posts into a room, then none passes an
    English sentence straight to the call: each text comes from a table
    with a German side."""
    found = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found += [f"{path.relative_to(REPO)}:{line}: {text[:60]!r}"
                  for line, text in _posted_literals(tree)]
    assert not found, "English text posted without a German one:\n" + "\n".join(found)


def test_every_table_of_texts_has_a_german_side_for_each_english_text():
    """Given a table of texts by language in a module that posts into a
    room, or a bot's message file, then every English entry has a German
    one."""
    gaps = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not any(isinstance(n, ast.Call) and _called(n) in _SENDERS for n in ast.walk(tree)) \
                and "_WORDS" not in path.read_text(encoding="utf-8"):
            continue
        gaps += [f"{path.relative_to(REPO)}:{table.lineno}: {sorted(missing)}"
                 for table in _tables(tree) if (missing := _missing_german(table))]
    for path in sorted((REPO / "stacklets").glob("*/bot/messages/*.yml")):
        texts = yaml.safe_load(path.read_text(encoding="utf-8"))
        if missing := set(texts["en"]) - set(texts.get("de", {})):
            gaps.append(f"{path.relative_to(REPO)}: {sorted(missing)}")
    assert not gaps, "English texts without a German one:\n" + "\n".join(gaps)


# ── Why a filing failed ──────────────────────────────────────────────────
#
# The archivist files into memory's test instance (`running_memory`) through
# its real capture pipeline and mirror, and answers through
# `_reply_for_capture`, with the Matrix side recording what it sends. The
# classifier is a stand-in that always says the same: what it says is not
# under test.

from stack.memory import MemoryAPI  # noqa: E402
from tests.memory.unit.instance import running_memory  # noqa: E402

_PEOPLE = {f"family/people/{pid}.md": f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n---\n"
           for pid, name in (("homer", "Homer Simpson"), ("marge", "Marge Simpson"))}
_FILED = {"title": "Sunscreen", "persons": ["Marge"], "tags": ["trip"], "summary": "Pack sunscreen."}
_MARGE = "@marge:simpson"


class _Classifier:
    async def classify_capture(self, **_):
        return dict(_FILED)


class _Words:
    """The text extractor: a typed note is its own text."""

    async def extract(self, text):
        return SimpleNamespace(text=text, source_uri=None, title_hint=None)


class _HomerEditsAfterTheRead(MemoryAPI):
    """The archivist's client, while Homer edits the card in Forgejo just
    after the archivist read it for a correction."""

    def __init__(self, memory, path: str):
        super().__init__(memory.environ["MEMORY_API_URL"], token=memory.environ["CARDS_WRITE_TOKEN"],
                         where="the curator")
        self._memory, self._path, self.edits = memory, path, 0

    def file(self, path: str):
        """The card as read, and then Homer's next edit of it, on every read:
        whichever read the write is based on, the card has changed since."""
        found = super().file(path)
        if self._path == path:
            self.edits += 1
            self._memory.edited_in_forgejo({path: f"Homer's edit {self.edits}\n"}, by="homer")
        return found


def _capture_pipeline(tmp_path, api: MemoryAPI):
    from stacklets.docs.bot.capture_pipeline import CapturePipeline
    from stacklets.docs.bot.git_mirror import GitMirror
    from stacklets.docs.bot.vault_context import VaultContext
    vault = VaultContext(language="en", shared_bucket="family")
    return CapturePipeline(
        url_extractor=None, text_extractor=_Words(), classifier=_Classifier(),
        mirror=GitMirror(data_dir=tmp_path / "archivist", vault=vault, memory=api),
        capture_tags=SimpleNamespace(top=lambda n: [], record=lambda *a, **k: None, save=lambda: None),
        paperless=None, bot_name="archivist-bot", classify_max_chars=10000,
        capture_keep_body=False, capture_tag_prompt_size=50, vault=vault)


async def _answer_in_chat(tmp_path, outcome) -> str:
    """The archivist's one answer in the chat to `outcome`, as it posts it."""
    from stacklets.docs.bot.archivist import ArchivistBot
    from stacklets.docs.bot.vault_context import VaultContext
    bot = ArchivistBot(homeserver="http://h", user_id="@archivist-bot:simpson", password="x",
                       session_dir=tmp_path / "session",
                       services=SimpleNamespace(vault=VaultContext(language="en",
                                                                   shared_bucket="family")))
    sent: list[str] = []

    async def _send(room_id, text, *_, **__):
        sent.append(text)

    async def _react(*_):
        pass
    bot._send, bot._react = _send, _react
    await bot._reply_for_capture("!notes:simpson", outcome, "$capture")
    assert len(sent) == 1, sent
    return sent[0]


@pytest.fixture
def family_memory(tmp_path, monkeypatch):
    with running_memory(tmp_path / "data", _PEOPLE, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons") as memory:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        yield memory


@pytest.mark.asyncio
@pytest.mark.parametrize("language, expected", [("de", "de"), ("fr", "en")])
async def test_a_correction_refused_because_the_card_changed_is_told_in_the_stacks_language(
        tmp_path, monkeypatch, family_memory, language, expected):
    """Given the stack's language, when Homer edits a note in Forgejo while
    the archivist writes Marge's correction of it, then the memory refuses
    the write, and the archivist's answer gives that reason in the stack's
    language. A German family got the reason in English."""
    monkeypatch.setenv("LANGUAGE", language)
    api = MemoryAPI.from_container(family_memory.environ)
    filed = await _capture_pipeline(tmp_path, api).capture_text(
        text="Pack sunscreen for the trip", sender_mxid=_MARGE)
    assert filed.vault_path, filed

    homer = _HomerEditsAfterTheRead(family_memory, filed.vault_path)
    corrected = await _capture_pipeline(tmp_path, homer).reprocess(
        vault_path=filed.vault_path, user_hint="Add tag beach", sender_mxid=_MARGE,
        initial_classification=filed.envelope["data"])

    assert corrected.status == "not_changed", corrected
    answer = await _answer_in_chat(tmp_path, corrected)
    assert language_of(answer) == expected, answer
    assert family_memory.text(filed.vault_path) == f"Homer's edit {homer.edits}\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("language, expected", [("de", "de"), ("fr", "en")])
async def test_a_filing_the_memory_does_not_answer_is_told_in_the_stacks_language(
        tmp_path, monkeypatch, family_memory, language, expected):
    """Given the stack's language, when the curator does not answer, then
    the archivist's answer that the note was not filed says why in the
    stack's language."""
    monkeypatch.setenv("LANGUAGE", language)
    offline = MemoryAPI("http://127.0.0.1:9", token="t", where="the curator")

    outcome = await _capture_pipeline(tmp_path, offline).capture_text(
        text="Pack sunscreen for the trip", sender_mxid=_MARGE)

    assert outcome.status == "not_filed", outcome
    answer = await _answer_in_chat(tmp_path, outcome)
    assert language_of(answer) == expected, answer
    assert "127.0.0.1" not in answer


@pytest.mark.asyncio
async def test_a_refusal_the_stack_cannot_name_keeps_the_curators_words_out_of_the_chat(
        tmp_path, monkeypatch, family_memory, httpserver):
    """Given a German stack, when the curator answers with a refusal the
    stack does not know, then the answer says in German that the reason is
    unknown, and the curator's own sentence is not in the chat."""
    monkeypatch.setenv("LANGUAGE", "de")
    httpserver.expect_request(re.compile(".*")).respond_with_json(
        {"reason": "exploded", "detail": "the git index is locked by pid 4711"}, status=500)
    strange = MemoryAPI(httpserver.url_for(""), token="t", where="the curator")

    outcome = await _capture_pipeline(tmp_path, strange).capture_text(
        text="Pack sunscreen for the trip", sender_mxid=_MARGE)

    assert outcome.status == "not_filed", outcome
    answer = await _answer_in_chat(tmp_path, outcome)
    assert language_of(answer) == "de", answer
    assert "4711" not in answer and "exploded" not in answer
