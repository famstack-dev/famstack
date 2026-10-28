"""`stack memory diary`: what a compile writes to the memory, and what it leaves.

The diary compile reads the memories room and keeps one card per entry in
the memory repo. These tests drive it as an admin does, through `./stack
memory diary` and its hop into the bot runner, and read what reached the
memory repo and the wiki afterwards.

What is real: the host command and its loader, the diary command in the
bot runner, the cards it renders, the pages it publishes into a brain
directory, and memory's test instance (`running_memory`): the curator's API
and card store it reads and writes the cards through, and a bare git
repository as the memory in Forgejo. What stands in, at its boundary:
Synapse (its login, room directory and admin history API), the model (it
reads the room, reads each entry for its card, and writes a month's
paragraph), and the bot runner's `docker exec`, which runs the command in
this process: the tests hand the command a model of their own and give it
new prompts, which a process of its own could not be given. A change made
in Forgejo is pulled by the curator, as Forgejo's push notice has it do.
The family is the Simpsons.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from werkzeug.wrappers import Response

from stack.ai.client import CallCost
from tests.memory.unit.instance import TOKEN, RunningMemory, git, running_memory
from tests.memory.unit.simpsons_cards import ONTOLOGY


ENTRYPOINT = "/stacklets/memory/bot/cli_entrypoint.py"
ENTRIES = "family/diary/entries"
# The first message arrives at eight in the morning, the next an hour
# later: far enough apart that none of them reads as a synced burst.
BASE_TS = int(datetime(2026, 10, 1, 8, tzinfo=timezone.utc).timestamp() * 1000)
HOUR_MS = 3_600_000


# ── The memories room, on the stand-in Synapse ───────────────────────────

class Room:
    """The memories room, oldest event first, as people and the archivist post to it."""

    def __init__(self):
        self.events: list[dict] = []

    def _post(self, sender: str, content: dict) -> str:
        n = len(self.events) + 1
        event_id = f"$m{n}:simpson"
        self.events.append({"type": "m.room.message", "event_id": event_id,
                            "sender": f"@{sender}:simpson",
                            "origin_server_ts": BASE_TS + n * HOUR_MS, "content": content})
        return event_id

    def says(self, who: str, words: str) -> str:
        return self._post(who, {"msgtype": "m.text", "body": words})

    def answers(self, who: str, to: str, words: str) -> str:
        """A reply to an entry, as a client sends one outside a thread."""
        return self._post(who, {"msgtype": "m.text", "body": words,
                                "m.relates_to": {"m.in_reply_to": {"event_id": to}}})

    def files_the_card(self, root: str) -> None:
        """The archivist's card notice in the entry's thread, and a thank-you there."""
        self._post("archivist-bot", {
            "msgtype": "m.notice", "body": "Filed in the diary.",
            "dev.famstack.event": {"type": "diary.filed"},
            "m.relates_to": {"rel_type": "m.thread", "event_id": root}})
        self._post("marge", {"msgtype": "m.text", "body": "Thanks!",
                             "m.relates_to": {"rel_type": "m.thread", "event_id": root}})

    def posts_the_day(self) -> None:
        """The archivist's daily post of the day's cards."""
        self._post("archivist-bot", {"msgtype": "m.notice", "body": "Today in the diary: 2 entries."})


@pytest.fixture
def room(httpserver) -> Room:
    """Synapse as the compile reads it: the admin's login, the room's alias,
    and the admin API's history, newest event first, in one page."""
    room = Room()

    def history(request):
        return Response(json.dumps({"chunk": list(reversed(room.events)), "end": ""}),
                        content_type="application/json")

    httpserver.expect_request("/_matrix/client/v3/login", method="POST").respond_with_json(
        {"access_token": "admintoken"})
    httpserver.expect_request(re.compile(r"^/_matrix/client/v3/directory/room/.+"),
                              method="GET").respond_with_json({"room_id": "!memories:simpson"})
    httpserver.expect_request(re.compile(r"^/_synapse/admin/v1/rooms/.+/messages$"),
                              method="GET").respond_with_handler(history)
    return room


# ── The model ────────────────────────────────────────────────────────────

# A sentence the tests add to the card prompt, as a release that changes
# the prompts does. The stand-in model titles an entry differently when
# it reads it.
NEW_RULE = "Keep the title to three words."


class Model:
    """The model at its boundary.

    It reads the room as monologues with no dates, titles an entry with
    its first sentence (its first three words under `NEW_RULE`), says who
    answered it, and offers the topic "Treehouse" whenever the prompt
    lists it, "Baumhaus" on a German household. Asked to apply a
    correction, it gives the card back with the title and the topics the
    prompt shows it. `entries_read` is the first line of every entry it
    was asked to read for a card, `corrections` every correction prompt.
    """

    def __init__(self):
        self.entries_read: list[str] = []
        self.corrections: list[str] = []

    async def complete(self, role, prompt: str, **_) -> str:
        # Every call reports what it cost, as the client does.
        if on_cost := _.get("on_cost"):
            on_cost(CallCost(estimated_tokens=len(prompt) // 3, seconds=0.1,
                             prompt_tokens=900, completion_tokens=60, finish_reason="stop"))
        if prompt.startswith("You are reading a family's private memories room"):
            count = len(re.findall(r"^\[\d+\] ", prompt, re.M))
            return json.dumps({"messages": [{"n": n, "mode": "note"} for n in range(1, count + 1)]})
        if prompt.startswith("You are filing one entry"):
            words = re.search(r"Recorded by \w+, [^\n]*\n(?:Spoken to:[^\n]*\n)?\n([^\n]*)",
                              prompt).group(1)
            self.entries_read.append(words)
            title = " ".join(words.split()[:3]) if NEW_RULE in prompt else words.rstrip(".")
            answered = re.findall(r"^(\w+) replied:", prompt, re.M)
            return json.dumps({
                "title": title,
                "description": f"Answered by {', '.join(answered)}." if answered else "A note.",
                "summary": "", "facts": [], "persons": [], "quotes": [],
                "topics": [t for t in ("Treehouse", "Baumhaus") if t in prompt]})
        if prompt.startswith("A family member corrected an entry"):
            self.corrections.append(prompt)
            card = dict(re.findall(r"^(Title|Topics): (.*)$", prompt, re.M))
            return json.dumps({
                "title": card["Title"], "description": "", "summary": "", "facts": [],
                "persons": [], "quotes": [], "topics": card["Topics"].split(", "),
                "date": None, "addressee": None})
        return "A month of the family."


@pytest.fixture
def model() -> Model:
    return Model()


# ── The memory, the brain, the bot runner ───────────────────────────────

@pytest.fixture
def memory(tmp_path):
    """The memory as a fresh install leaves it, holding the ontology the
    compile offers topics from."""
    with running_memory(tmp_path, {"ontology.toml": ONTOLOGY}, seeded_by="stackadmin",
                        seeded_as="seed: the ontology") as running:
        yield running


class Brain:
    """The brain working copy the diary pages are published into."""

    def __init__(self, root: Path):
        self.root = root

    def pages(self) -> dict[str, str]:
        return {p.relative_to(self.root).as_posix(): p.read_text(encoding="utf-8")
                for p in sorted(self.root.rglob("*.md"))}


@pytest.fixture
def brain(tmp_path) -> Brain:
    root = tmp_path / "brain"
    root.mkdir()
    return Brain(root)


@pytest.fixture
def bot_command(httpserver, memory, brain, monkeypatch, tmp_path):
    """The diary command in the bot runner, with the environment `stack up`
    renders for it, every service behind the one stand-in address."""
    url = httpserver.url_for("").rstrip("/")
    for key, value in {
            "MATRIX_HOMESERVER": url, "WHISPER_URL": url,
            "MATRIX_ADMIN_USER": "stackadmin", "MATRIX_ADMIN_PASSWORD": "donuts",
            "MATRIX_SERVER_NAME": "simpson",
            "MEMORY_API_URL": memory.curator, "CARDS_WRITE_TOKEN": TOKEN,
            "MEMORY_VAULT_DIR": str(memory.vault), "BRAIN_REPO_DIR": str(brain.root),
            "DIARY_STATE_DIR": str(tmp_path / "diary-state"),
            "TRANSCRIPT_DIR": str(tmp_path / "transcripts"),
            "CURATOR_STATE_DIR": str(tmp_path / "curator-state"),
            "SHARED_BUCKET": "family", "LANGUAGE": "en", "AI_DEFAULT_MODEL": "stand-in",
            "FAMILY_NAMES": "Homer,Marge,Bart,Lisa,Maggie"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MEDIA_ARCHIVE_DIR", raising=False)
    from stacklets.memory.bot.cli import diary
    return diary


@pytest.fixture
def bot_runner(bot_command, model, monkeypatch) -> None:
    """`docker exec` into the bot runner, running the command here."""
    real_run, real_call = subprocess.run, subprocess.call

    def in_the_bot_runner(cmd) -> int:
        at = cmd.index(ENTRYPOINT)
        assert cmd[at + 1] == "diary", cmd
        return asyncio.run(bot_command.run(model, list(cmd[at + 2:])))

    def run(cmd, *args, **kwargs):
        if cmd[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(cmd, 0, "true\n", "")
        if cmd[:2] == ["docker", "exec"]:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = in_the_bot_runner(cmd)
            return subprocess.CompletedProcess(cmd, rc, out.getvalue(), None)
        return real_run(cmd, *args, **kwargs)

    def call(cmd, *args, **kwargs):
        if cmd[:2] == ["docker", "exec"]:
            return in_the_bot_runner(cmd)
        return real_call(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "call", call)


# ── The admin ────────────────────────────────────────────────────────────

class Diary:
    """`./stack memory diary <args>`, as the admin runs it."""

    def __init__(self, stack, command, monkeypatch):
        self.stack, self.command, self.monkeypatch = stack, command, monkeypatch
        self.answer: dict = {}
        self.printed = self.progress = ""

    def runs(self, *args: str) -> str:
        """Run it; returns what it printed for people. `progress` holds stderr."""
        out, err = io.StringIO(), io.StringIO()
        argv = sys.argv
        sys.argv = ["stack", "memory", "diary", *args]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.answer = self.stack.run_cli_command("memory", "diary", list(args)) or {}
        except SystemExit as e:
            self.answer = {"exit": e.code}
        finally:
            sys.argv = argv
        self.printed, self.progress = out.getvalue(), err.getvalue()
        return self.printed

    def gets_new_prompts(self) -> None:
        """The card prompt as a release that changed it ships it."""
        prompt = self.command._EXTRACT_PROMPT
        self.monkeypatch.setattr(self.command, "_EXTRACT_PROMPT", prompt.replace(
            "Report what the entry says.", f"Report what the entry says. {NEW_RULE}", 1))


@pytest.fixture
def diary(bot_runner, bot_command, memory, monkeypatch) -> Diary:
    return Diary(memory.stack, bot_command, monkeypatch)


def head(memory: RunningMemory) -> str:
    return git(memory.forgejo.repo, "rev-parse", "main")


def commits_since(memory: RunningMemory, mark: str) -> list[str]:
    out = git(memory.forgejo.repo, "log", "--reverse", "--format=%s", f"{mark}..main")
    return out.splitlines() if out else []


def cards(memory: RunningMemory) -> dict[str, str]:
    """Each diary card's title, by the first line of its entry's words."""
    out = {}
    for _, text in sorted(memory.pages(ENTRIES).items()):
        title = re.search(r"^title: (.*)$", text, re.M).group(1).strip("'\"")
        words = text.rsplit("## Text\n\n", 1)[-1].splitlines()[0]
        out[words] = title
    return out


# ── A limited run ────────────────────────────────────────────────────────

def test_a_limited_run_writes_no_card_and_publishes_no_page(memory, room, diary, brain):
    """Given a diary compiled from three entries and a fourth entry since,
    when the admin runs `stack memory diary --limit 3` without `--dry-run`,
    then no card is written or removed and no page changes, and the run
    says how many entries it saw and that it removes nothing.

    `--limit` counts the newest messages, so the run sees part of the
    room: an entry cut at the edge, a reply without the entry it answers.
    A card written from that is wrong, and the pages built from it with it.
    """
    room.says("marge", "Lisa played the saxophone at the school concert.")
    room.says("homer", "We built a treehouse in the backyard.")
    room.says("bart", "I got a skateboard for my birthday.")
    diary.runs()
    mark, pages = head(memory), brain.pages()
    room.says("marge", "Maggie said her first word today.")

    diary.runs("--limit", "3")

    assert commits_since(memory, mark) == []
    assert brain.pages() == pages
    assert "3 entries in the newest 3 messages" in diary.progress
    assert "removes nothing" in diary.progress


# ── A preview ────────────────────────────────────────────────────────────

def test_a_preview_shows_the_newest_entries_cards_as_they_are_and_as_they_would_be(
        memory, room, diary, brain, model):
    """Given three entries compiled into cards, the archivist's card notices
    and its daily post as the newest messages in the room, and a release
    with new card prompts, when the admin runs `stack memory diary
    --preview 2`, then it shows the two newest people's entries, each with
    the card on file and the card the new prompts make, asks the model
    about those two only, and writes nothing.

    This is how an admin sees what new prompts do before a compile
    applies them. Counting messages instead of entries showed an empty
    diary: the newest messages in a lived-in room are the archivist's.
    """
    lisa = room.says("marge", "Lisa played the saxophone at the school concert.")
    treehouse = room.says("homer", "We built a treehouse in the backyard.")
    skateboard = room.says("bart", "I got a skateboard for my birthday.")
    diary.runs()
    for entry in (lisa, treehouse, skateboard):
        room.files_the_card(entry)
    room.posts_the_day()
    mark, pages = head(memory), brain.pages()
    diary.gets_new_prompts()
    model.entries_read.clear()

    printed = diary.runs("--preview", "2")

    assert [(e["before"]["title"], e["after"]["title"], e["state"])
            for e in diary.answer["entries"]] == [
        ("We built a treehouse in the backyard", "We built a", "changed"),
        ("I got a skateboard for my birthday", "I got a", "changed")]
    assert [e["next_compile"] for e in diary.answer["entries"]] == ["keeps", "keeps"]
    assert "We built a treehouse in the backyard" in printed
    assert "I got a skateboard for my birthday" in printed
    assert "Lisa" not in printed
    assert model.entries_read == ["We built a treehouse in the backyard.",
                                  "I got a skateboard for my birthday."]
    assert commits_since(memory, mark) == []
    assert brain.pages() == pages


def test_a_preview_with_nothing_changed_shows_the_cards_unchanged(memory, room, diary, model):
    """Given two entries compiled into cards and nothing changed since,
    when the admin previews them, then both show as unchanged and the
    model is not asked again: the compile's readings are reused."""
    room.says("marge", "Lisa played the saxophone at the school concert.")
    room.says("homer", "We built a treehouse in the backyard.")
    diary.runs()
    model.entries_read.clear()

    printed = diary.runs("--preview", "2")

    assert [e["state"] for e in diary.answer["entries"]] == ["unchanged", "unchanged"]
    assert printed.count("unchanged") == 2
    assert model.entries_read == []


def test_a_preview_of_an_empty_room_shows_no_entries(memory, room, diary):
    """Given a memories room with nothing recorded yet, when the admin
    previews it, then the preview shows no entries and writes nothing."""
    mark = head(memory)

    diary.runs("--preview", "1")

    assert diary.answer["entries"] == []
    assert commits_since(memory, mark) == []


# ── The model never rewrites a card on its own ───────────────────────────

LISA = "Lisa played the saxophone at the school concert."
TREEHOUSE = "We built a treehouse in the backyard."
SKATEBOARD = "I got a skateboard for my birthday."


def test_after_new_prompts_a_compile_reads_only_the_new_entry_and_rewrites_no_card(
        memory, room, diary, model):
    """Given three entries compiled into cards and a release with new card
    prompts, when a new entry arrives and the diary compiles, then the
    model reads the new entry only, only its card is written, and the run
    says how many cards were made under older prompts and how to rebuild
    them.

    A card is what the family reads and corrects. New prompts are a
    reason to offer a rebuild, not to rewrite a family's diary overnight.
    """
    room.says("marge", LISA)
    room.says("homer", TREEHOUSE)
    room.says("bart", SKATEBOARD)
    diary.runs()
    mark = head(memory)
    diary.gets_new_prompts()
    model.entries_read.clear()
    room.says("marge", "Maggie said her first word today.")

    diary.runs()

    assert model.entries_read == ["Maggie said her first word today."]
    assert commits_since(memory, mark) == ["learn: Maggie said her"]
    assert cards(memory)[TREEHOUSE] == "We built a treehouse in the backyard"
    assert "3 cards were made under older prompts" in diary.progress
    assert "--rebuild" in diary.progress


def test_every_model_call_of_the_compile_is_recorded(
        memory, room, diary, monkeypatch, tmp_path):
    """Given a record file for the compile's model calls, when the diary
    compiles an October entry, then the room's reading, the entry's
    reading for its card and the month's paragraph are each recorded, the
    paragraph for October's page, with the server's counts.

    The nightly sweep's record covers every model call it makes, the
    diary's as well as the wiki's. (The transcript passes record as well;
    this room holds no recording, so none is polished here.)
    """
    log = tmp_path / "wiki-calls.jsonl"
    monkeypatch.setenv("WIKI_CALLS_LOG", str(log))
    room.says("marge", LISA)

    diary.runs()

    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert sorted(c["kind"] for c in calls) == ["diary-entry", "diary-month", "diary-room"]
    [month] = [c for c in calls if c["kind"] == "diary-month"]
    assert month["page"] == "family/diary/2026/10.md"
    assert month["ok"] is True
    assert (month["prompt_tokens"], month["completion_tokens"]) == (900, 60)


def test_a_new_topic_rewrites_no_card(memory, room, diary, model):
    """Given an entry about a treehouse compiled into a card, when the
    family adds the topic "Treehouse" and the diary compiles, then no card
    is written and the model is not asked.

    The topics and the people are what the model is offered, not what an
    entry says. A new one changes no entry."""
    room.says("homer", TREEHOUSE)
    diary.runs()
    memory.edited_in_forgejo({"ontology.toml": ONTOLOGY + TREEHOUSE_TOPIC}, by="stackadmin",
                             subject="ontology: add the treehouse")
    mark = head(memory)
    model.entries_read.clear()

    diary.runs()

    assert commits_since(memory, mark) == []
    assert model.entries_read == []


def test_a_rebuild_brings_the_cards_made_under_older_prompts_up_to_date(memory, room, diary, model):
    """Given two cards made under older prompts, when the admin runs `stack
    memory diary --rebuild`, then both are read again and rewritten under
    the current prompts; a second rebuild asks the model nothing and
    writes nothing."""
    room.says("marge", LISA)
    room.says("homer", TREEHOUSE)
    diary.runs()
    diary.gets_new_prompts()
    diary.runs()
    mark = head(memory)
    model.entries_read.clear()

    diary.runs("--rebuild")
    rebuilt = commits_since(memory, mark)
    read = list(model.entries_read)
    mark = head(memory)
    model.entries_read.clear()
    diary.runs("--rebuild")

    assert sorted(rebuilt) == ["update: Lisa played the", "update: We built a"]
    assert read == [LISA, TREEHOUSE]
    assert cards(memory) == {LISA: "Lisa played the", TREEHOUSE: "We built a"}
    assert commits_since(memory, mark) == []
    assert model.entries_read == []


def test_a_new_reply_rebuilds_the_entry_it_answers_and_no_other(memory, room, diary, model):
    """Given two entries compiled into cards and new card prompts since,
    when Homer answers Marge's entry, then the compile reads that entry
    again, with his reply, and rewrites its card only.

    A reply is part of the entry, so the entry changed and its card
    follows. The other card was made from an entry that did not change."""
    lisa = room.says("marge", LISA)
    room.says("homer", TREEHOUSE)
    diary.runs()
    mark = head(memory)
    diary.gets_new_prompts()
    model.entries_read.clear()
    room.answers("homer", lisa, "She was the best one on stage.")

    diary.runs()

    assert model.entries_read == [LISA]
    assert commits_since(memory, mark) == ["update: Lisa played the"]
    assert cards(memory) == {LISA: "Lisa played the", TREEHOUSE: "We built a treehouse in the backyard"}


def test_the_first_compile_after_an_update_keeps_every_card_of_an_unchanged_entry(
        memory, room, diary, model, tmp_path):
    """Given cards written before cards named the prompts that made them,
    and no reading on file in the form this release keeps, when the diary
    compiles after the update, then no card is written and the model is
    not asked, and the run counts the cards as made under older prompts.

    An updated instance has a diary the family has read for months. The
    card on file is the reading of its entry for as long as the entry
    does not change."""
    room.says("marge", LISA)
    room.says("homer", TREEHOUSE)
    diary.runs()
    as_before_the_update(memory)
    (tmp_path / "diary-state" / "extractions.json").unlink()
    mark = head(memory)
    model.entries_read.clear()

    diary.runs()

    assert commits_since(memory, mark) == []
    assert model.entries_read == []
    assert "2 cards were made under older prompts" in diary.progress


def as_before_the_update(memory: RunningMemory) -> None:
    """The cards as the compile signed them before they named their prompts,
    pulled by the curator."""
    from stack.digest import signed
    memory.edited_in_forgejo(
        {path: signed(re.sub(r"^(prompt_template|digest): .*\n", "", text, flags=re.M))
         for path, text in memory.pages(ENTRIES).items()},
        by="memory-bot", subject="cards as an older release wrote them")


# ── Topics: tagged by id, read by their label ────────────────────────────
#
# A card tags a topic by its id, as every other card does (ADR-017 §4), so
# households in two languages tag one subject alike. Where a person or the
# model reads a card's topics, they read the label in the household's
# language.

TREEHOUSE_TOPIC = '\n[topic.treehouse]\nnames = { de = "Baumhaus", en = "Treehouse" }\n'


def a_german_household_with_a_treehouse_topic(memory: RunningMemory, monkeypatch) -> None:
    """The household speaks German, and its ontology has the topic Treehouse,
    "Baumhaus" in German: in the memory repo and in the curator's clone of it."""
    monkeypatch.setenv("LANGUAGE", "de")
    memory.edited_in_forgejo({"ontology.toml": ONTOLOGY + TREEHOUSE_TOPIC}, by="stackadmin",
                             subject="ontology: add the treehouse")


def card_fields(memory: RunningMemory) -> list[dict]:
    """The frontmatter of each diary card in the memory repo."""
    from stack.frontmatter import parse
    return [parse(text) for _, text in sorted(memory.pages(ENTRIES).items())]


def test_a_card_tags_its_topic_by_id_and_the_preview_shows_its_label(
        memory, room, diary, monkeypatch):
    """Given a German household whose ontology has the topic Treehouse
    ("Baumhaus"), when the diary compiles an entry the model reads as about
    it, then the card's tags hold the topic's id, `treehouse`, and the
    preview shows the topic as the household reads it, "Baumhaus"."""
    a_german_household_with_a_treehouse_topic(memory, monkeypatch)
    room.says("homer", TREEHOUSE)
    diary.runs()

    printed = diary.runs("--preview", "1")

    assert [card["tags"] for card in card_fields(memory)] == [["treehouse"]]
    [entry] = diary.answer["entries"]
    assert entry["before"]["topics"] == entry["after"]["topics"] == ["Baumhaus"]
    assert "Baumhaus" in printed


def test_a_correction_shows_the_model_the_topics_by_their_label(
        memory, room, diary, model, monkeypatch):
    """Given a German household's card tagged `treehouse`, when Marge
    corrects it in its thread, then the model reads the card's topic as
    "Baumhaus", in the household's words like the rest of the card, and the
    corrected card is tagged `treehouse` again."""
    a_german_household_with_a_treehouse_topic(memory, monkeypatch)
    room.says("homer", TREEHOUSE)
    diary.runs()
    [card] = card_fields(memory)

    diary.runs("correct", card["entry_id"], "--by", "marge", "--event", "$fix1:simpson",
               "--text", "Bart helped too.")

    [prompt] = model.corrections
    assert "Topics: Baumhaus" in prompt.splitlines()
    [corrected] = card_fields(memory)
    assert corrected["tags"] == ["treehouse"] and "digest" not in corrected


def test_after_migration_0010_a_rebuild_rewrites_no_card(memory, room, diary, monkeypatch):
    """Given a card an older release tagged with the topic's label,
    "Baumhaus", when migration memory/0010 has run and the admin rebuilds
    the diary, then no card is written: the migrated card is the one the
    compile writes today. Without the migration the rebuild rewrites it
    only to change its tags."""
    from stacklets.memory.lib import tag_diary_topics_by_id
    from stacklets.memory.memory_api import MemoryAPI

    a_german_household_with_a_treehouse_topic(memory, monkeypatch)
    room.says("homer", TREEHOUSE)
    diary.runs()
    tagged_by_label_as_before_the_update(memory, {"treehouse": "Baumhaus"})
    diary.runs("--rebuild", "--dry-run")
    assert "cards: 1 to write, 0 to remove" in diary.progress

    tag_diary_topics_by_id(MemoryAPI(memory.curator, token=TOKEN, where="the curator"),
                           memory.vault)
    diary.runs("--rebuild", "--dry-run")

    assert "cards: 0 to write, 0 to remove" in diary.progress
    assert [card["tags"] for card in card_fields(memory)] == [["treehouse"]]


def tagged_by_label_as_before_the_update(memory: RunningMemory, labels: dict[str, str]) -> None:
    """The cards as the compile signed them when it tagged a topic by its
    label: each id in `labels` replaced by the label it maps to; pulled by
    the curator."""
    from stack.digest import signed

    def by_label(text: str) -> str:
        for topic, label in labels.items():
            text = text.replace(f"\n  - {topic}\n", f"\n  - {label}\n")
        return signed(text)

    memory.edited_in_forgejo({path: by_label(text) for path, text in memory.pages(ENTRIES).items()},
                             by="memory-bot", subject="cards as an older release tagged them")
