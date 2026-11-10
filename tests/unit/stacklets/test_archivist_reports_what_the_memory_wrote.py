"""A capture's answer in the chat says what the memory wrote, and an email's correction reaches its card.

The archivist files a note, a bookmark, a voice memo, a photo or an email
into the family memory, and answers in the chat. When a family member
corrects a filing in its thread, it refiles the card and answers with what
changed. Both answers are the family's only view of what happened, so they
must report what the memory wrote, not what the classifier said: a
correction the memory refuses is never answered "Reclassified".

An email is a thread of messages folded into one card. A correction
changes the card's title, people and tags, and leaves every message on
the card as it is.

The archivist runs through its real capture pipeline and mirror into the
curator of memory's test instance (`running_memory`), and its answer
through `_reply_for_capture` and `_confirm_email_filing`, with the Matrix
side a stand-in that records what was sent. The classifier is a
stand-in: what it says is not under test. The household is the Simpsons.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from stack.frontmatter import parse
from stack.memory import MemoryAPI
from stacklets.docs.bot.archivist import ArchivistBot
from stacklets.docs.bot.capture_pipeline import CapturePipeline
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.pipeline import LLMTimeoutError
from stacklets.docs.bot.vault_context import VaultContext
from tests.memory.unit.instance import running_memory

BOT_ID = "@archivist-bot:simpson"
MARGE = "@marge:simpson"
FILES = {f"family/people/{pid}.md": f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n---\n"
         for pid, name in (("homer", "Homer Simpson"), ("marge", "Marge Simpson"),
                           ("bart", "Bart Simpson"), ("lisa", "Lisa Simpson"))}
THREAD = "<trip@springfield-elementary.test>"
SCHOOL = "skinner@springfield-elementary.test"
# The provenance tags the archivist seeds on a filed email.
SEEDS = [f"Sender: {SCHOOL}", "Mailbox: family", "Folder: INBOX"]
# A 1x1 PNG, for the photo.
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


class Classifier:
    """The classifier's answers, in turn; `meanwhile` runs while it thinks."""

    def __init__(self, *answers: dict, meanwhile=None):
        self.answers, self.meanwhile = list(answers), meanwhile

    async def classify_capture(self, **_):
        if self.meanwhile:
            self.meanwhile()
        return dict(self.answers.pop(0) if len(self.answers) > 1 else self.answers[0])


class Silent:
    """A classifier that times out on everything it is asked."""

    def __getattr__(self, name):
        async def times_out(*_args, **_kwargs):
            raise LLMTimeoutError("the model took too long")
        return times_out


class Words:
    async def extract(self, text):
        return SimpleNamespace(text=text, source_uri=None, title_hint=None)


class Page:
    """The page behind a link, read cleanly."""

    async def fetch(self, url):
        return SimpleNamespace(url=url, verdict=None, content=SimpleNamespace(
            text="Krusty Burger opens a new branch.", source_uri=url, title_hint="Krusty Burger"))


class Quiet:
    """The notifier a link capture acknowledges through."""

    async def acknowledge(self):
        pass


class Tags:
    def top(self, n):
        return []

    def record(self, tags, when):
        pass

    def save(self):
        pass


class Chat:
    """The Matrix side of the archivist: what it sent and how it reacted.

    `sent` holds each message's text (the plain body), `html` its HTML when
    the archivist gave its own, and `meta` the extra content keys.
    """

    def __init__(self):
        self.sent: list[str] = []
        self.html: list[str | None] = []
        self.meta: list[dict | None] = []
        self.reactions: list[str] = []


@pytest.fixture
def memory(tmp_path, monkeypatch):
    with running_memory(tmp_path / "data", FILES, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons") as memory:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        yield memory


def _pipeline(tmp_path, memory_api: MemoryAPI, classifier) -> CapturePipeline:
    vault = VaultContext(language="en", shared_bucket="family")
    mirror = GitMirror(data_dir=tmp_path / "archivist", vault=vault, memory=memory_api)
    return CapturePipeline(
        url_extractor=Page(), text_extractor=Words(), classifier=classifier,
        mirror=mirror, capture_tags=Tags(), paperless=None,
        bot_name="archivist-bot", classify_max_chars=10000, capture_keep_body=False,
        capture_tag_prompt_size=50, vault=vault)


def _archivist(tmp_path) -> tuple[ArchivistBot, Chat]:
    """The archivist bot with a Matrix side that records what it sends."""
    bot = ArchivistBot(homeserver="http://h", user_id=BOT_ID, password="x",
                       session_dir=tmp_path / "session",
                       services=SimpleNamespace(vault=VaultContext(language="en",
                                                                   shared_bucket="family")))
    chat = Chat()

    async def _send(room_id, text, reply_to=None, *, metadata=None, html=None, **_):
        chat.sent.append(text)
        chat.html.append(html)
        chat.meta.append(metadata)

    async def _react(room_id, event_id, key):
        chat.reactions.append(key)

    bot._send, bot._react = _send, _react
    return bot, chat


async def _an_email(pipeline, n: int, *, title: str = "Field trip", body: str = ""):
    """Message `n` of the school's field trip thread, filed as the archivist files it."""
    return await pipeline.capture_email(
        subject=title, body=body or f"Message {n}: the field trip is on Friday.",
        message_id=f"<trip-{n}@springfield-elementary.test>", thread_root=THREAD,
        sender_mxid="@mail-bot:simpson", from_addr=SCHOOL, captured_at="2026-11-02",
        bucket="family", seed_topics=SEEDS)


def _messages_of(card: str) -> str:
    """The thread's messages on an email card: everything from its first message on."""
    return card[card.index("<!-- mid:"):]


FILED = {"title": "Field trip", "persons": ["Bart"], "tags": ["school"], "summary": "Trip on Friday."}


# ── A correction of an email reaches its card ────────────────────────────

async def test_a_tag_added_in_an_emails_thread_is_on_its_card_by_the_person(tmp_path, memory):
    """Given a filed email, when Marge writes "Add tag gemuesekiste2" in its
    thread, then the card has the tag, in one commit by Marge, its thread's
    message is on it unchanged, and its sender, mailbox and folder tags stay."""
    api = MemoryAPI.from_container(memory.environ)
    filed = await _an_email(_pipeline(tmp_path, api, Classifier(FILED)), 1)
    before = memory.text(filed.vault_path)
    commits = len(memory.authors())

    corrected = await _pipeline(tmp_path, api, Classifier(
        {**FILED, "tags": ["school", "gemuesekiste2"]})).reprocess(
        vault_path=filed.vault_path, user_hint="Add tag gemuesekiste2", sender_mxid=MARGE,
        initial_classification=filed.envelope["data"], reply_text="Add tag gemuesekiste2")

    assert corrected.status == "reclassified"
    card = memory.text(filed.vault_path)
    tags = parse(card)["tags"]
    assert "gemuesekiste2" in tags and "school" in tags
    assert {t for t in parse(before)["tags"] if t.startswith(("sender-", "mailbox-", "folder-"))} \
        <= set(tags)
    assert _messages_of(card) == _messages_of(before)
    assert parse(card)["card_id"] == parse(before)["card_id"]
    assert memory.authors()[commits:] == ["marge <marge@simpson>"]


async def test_a_new_title_keeps_all_three_messages_of_the_thread(tmp_path, memory):
    """Given an email whose thread holds three messages, when a correction
    changes its title, then the card has the new title and the same three
    messages, in their order."""
    api = MemoryAPI.from_container(memory.environ)
    pipeline = _pipeline(tmp_path, api, Classifier(FILED))
    for n in (1, 2, 3):
        filed = await _an_email(pipeline, n)
    before = memory.text(filed.vault_path)
    assert before.count("<!-- mid:") == 3

    corrected = await _pipeline(tmp_path, api, Classifier(
        {**FILED, "title": "Field trip to the power plant"})).reprocess(
        vault_path=filed.vault_path, user_hint="It is the trip to the power plant",
        sender_mxid=MARGE, initial_classification=filed.envelope["data"])

    assert corrected.status == "reclassified"
    card = memory.text(filed.vault_path)
    assert parse(card)["title"] == "Field trip to the power plant"
    assert "\n# Field trip to the power plant\n" in card
    assert _messages_of(card) == _messages_of(before)
    assert [f"<!-- mid:<trip-{n}@springfield-elementary.test> -->" in card for n in (1, 2, 3)] \
        == [True] * 3


async def test_a_person_added_by_a_correction_is_on_the_emails_card(tmp_path, memory):
    """Given an email about Bart, when Marge writes "Lisa goes too", then
    the card is about Bart and Lisa, and links Lisa's card."""
    api = MemoryAPI.from_container(memory.environ)
    filed = await _an_email(_pipeline(tmp_path, api, Classifier(FILED)), 1)

    await _pipeline(tmp_path, api, Classifier({**FILED, "persons": ["Bart", "Lisa"]})).reprocess(
        vault_path=filed.vault_path, user_hint="Lisa goes too", sender_mxid=MARGE,
        initial_classification=filed.envelope["data"])

    card = memory.text(filed.vault_path)
    assert parse(card)["persons"] == ["bart", "lisa"]
    assert "people/lisa" in card.split("<!-- mid:")[0]


async def test_a_correction_that_only_reorders_the_tags_writes_nothing(tmp_path, memory):
    """Given a filed email, when the classifier answers a correction with
    the card's own tags in another order, then nothing is written: the
    card has no commit that only moves its tags around."""
    api = MemoryAPI.from_container(memory.environ)
    filed = await _an_email(_pipeline(tmp_path, api, Classifier(
        {**FILED, "tags": ["school", "field-trip"]})), 1)
    before, commits = memory.text(filed.vault_path), len(memory.authors())

    corrected = await _pipeline(tmp_path, api, Classifier(
        {**FILED, "tags": ["field-trip", "school"]})).reprocess(
        vault_path=filed.vault_path, user_hint="Add tag gemuesekiste2", sender_mxid=MARGE,
        initial_classification=filed.envelope["data"])

    assert corrected.status == "reclassified"
    assert memory.text(filed.vault_path) == before
    assert len(memory.authors()) == commits


# ── A correction changes only what it names ──────────────────────────────
# The classifier sees the first message's summary and the card's tags,
# never the card's people or the later messages, so its answer leaves out
# what it did not see. These stand-ins answer as such a model does.

async def _a_thread_about_bart_then_lisa(tmp_path, api):
    """The field trip thread: message 1 about Bart, message 2 adds Lisa and the permission slip."""
    pipeline = _pipeline(tmp_path, api, Classifier(
        FILED, {**FILED, "persons": ["Lisa"], "tags": ["permission-slip"]}))
    first = await _an_email(pipeline, 1)
    await _an_email(pipeline, 2, body="Message 2: Lisa joins, bring the permission slip.")
    return first


async def test_a_correction_keeps_the_person_a_later_message_added(tmp_path, memory):
    """Given a thread whose second message added Lisa and a tag, when Marge
    writes "Add tag gemuesekiste2" and the model answers about Bart only,
    then Lisa and the tag stay on the card, and the new tag is added."""
    api = MemoryAPI.from_container(memory.environ)
    first = await _a_thread_about_bart_then_lisa(tmp_path, api)
    assert parse(memory.text(first.vault_path))["persons"] == ["bart", "lisa"]

    corrected = await _pipeline(tmp_path, api, Classifier(
        {**FILED, "tags": ["school", "gemuesekiste2"]})).reprocess(
        vault_path=first.vault_path, user_hint="Add tag gemuesekiste2", sender_mxid=MARGE,
        initial_classification=first.envelope["data"], reply_text="Add tag gemuesekiste2")

    card = parse(memory.text(first.vault_path))
    assert card["persons"] == ["bart", "lisa"]
    assert {"school", "permission-slip", "gemuesekiste2"} <= set(card["tags"])
    assert [(c.field, c.kind, c.value) for c in corrected.changes] == \
        [("tag", "added", "gemuesekiste2")]


async def test_a_tag_the_correction_names_is_removed_and_nothing_else(tmp_path, memory):
    """Given that thread, when Marge writes "Remove tag school", then school
    is gone, and the card keeps Lisa and the permission slip."""
    api = MemoryAPI.from_container(memory.environ)
    first = await _a_thread_about_bart_then_lisa(tmp_path, api)

    await _pipeline(tmp_path, api, Classifier({**FILED, "tags": []})).reprocess(
        vault_path=first.vault_path, user_hint="Remove tag school", sender_mxid=MARGE,
        initial_classification=first.envelope["data"], reply_text="Remove tag school")

    card = parse(memory.text(first.vault_path))
    assert "school" not in card["tags"] and "permission-slip" in card["tags"]
    assert card["persons"] == ["bart", "lisa"]


async def test_a_tag_the_card_already_has_is_answered_as_nothing_changed(tmp_path, memory):
    """Given the second message gave the card the permission slip tag, when
    Marge writes "Add tag permission-slip" in the first message's thread,
    then nothing is written, and the answer says nothing changed: it names
    no change the memory did not write."""
    api = MemoryAPI.from_container(memory.environ)
    first = await _a_thread_about_bart_then_lisa(tmp_path, api)
    before, commits = memory.text(first.vault_path), len(memory.authors())

    corrected = await _pipeline(tmp_path, api, Classifier(
        {**FILED, "tags": ["school", "permission-slip"]})).reprocess(
        vault_path=first.vault_path, user_hint="Add tag permission-slip", sender_mxid=MARGE,
        initial_classification=first.envelope["data"], reply_text="Add tag permission-slip")

    assert memory.text(first.vault_path) == before and len(memory.authors()) == commits
    bot, chat = _archivist(tmp_path)
    await bot._reply_for_capture("!mail:simpson", corrected, "$fix",
                                 corrected_from=first.envelope["data"])
    [answer] = chat.sent
    line = answer.splitlines()[0]
    assert line.endswith("nothing changed on the card.") and "added" not in answer


async def test_an_answer_without_a_title_keeps_the_cards_title(tmp_path, memory):
    """Given a filed email, when the model answers a correction without a
    title, then the card keeps its own title, not a stand-in one."""
    api = MemoryAPI.from_container(memory.environ)
    filed = await _an_email(_pipeline(tmp_path, api, Classifier(FILED)), 1)

    await _pipeline(tmp_path, api, Classifier(
        {"persons": ["Bart"], "tags": ["school", "gemuesekiste2"]})).reprocess(
        vault_path=filed.vault_path, user_hint="Add tag gemuesekiste2", sender_mxid=MARGE,
        initial_classification=filed.envelope["data"], reply_text="Add tag gemuesekiste2")

    assert parse(memory.text(filed.vault_path))["title"] == "Field trip"


async def test_a_title_over_several_lines_is_written_as_one(tmp_path, memory):
    """Given a filed email, when the model answers a title that holds a line
    break and a heading, then the card's title is one line, and the thread
    still starts at its first message."""
    api = MemoryAPI.from_container(memory.environ)
    filed = await _an_email(_pipeline(tmp_path, api, Classifier(FILED)), 1)
    before = memory.text(filed.vault_path)

    await _pipeline(tmp_path, api, Classifier(
        {**FILED, "title": "Power plant trip\n## Not a message"})).reprocess(
        vault_path=filed.vault_path, user_hint="It is the power plant trip", sender_mxid=MARGE,
        initial_classification=filed.envelope["data"], reply_text="It is the power plant trip")

    card = memory.text(filed.vault_path)
    assert parse(card)["title"] == "Power plant trip ## Not a message"
    assert "\n## Not a message" not in card
    assert _messages_of(card) == _messages_of(before)


# ── A correction without the model writes nothing ────────────────────────

async def test_a_correction_the_model_does_not_answer_leaves_the_email_as_it_is(tmp_path, memory):
    """Given a thread about Bart and Lisa, when the model times out on Marge's
    correction, then the card is as it was, and the answer is that it was
    not changed, with the reason: its people and tags are not stripped."""
    api = MemoryAPI.from_container(memory.environ)
    first = await _a_thread_about_bart_then_lisa(tmp_path, api)
    before, commits = memory.text(first.vault_path), len(memory.authors())

    corrected = await _pipeline(tmp_path, api, Silent()).reprocess(
        vault_path=first.vault_path, user_hint="Add tag gemuesekiste2", sender_mxid=MARGE,
        initial_classification=first.envelope["data"], reply_text="Add tag gemuesekiste2")

    assert corrected.status == "not_changed" and "model" in corrected.failure_reason
    assert memory.text(first.vault_path) == before and len(memory.authors()) == commits


@pytest.mark.parametrize("kind", ["note", "bookmark", "email"])
async def test_a_correction_without_a_model_writes_nothing_for_any_capture(tmp_path, memory, kind):
    """Given a filed note, bookmark or email, when the archivist runs with no
    model set up, or the model fails, then a correction writes nothing and
    is answered as not changed."""
    api = MemoryAPI.from_container(memory.environ)
    filed = await CAPTURES[kind](_pipeline(tmp_path, api, Classifier(FILED)))
    before, commits = memory.text(filed.vault_path), len(memory.authors())

    for classifier in (None, Silent()):
        corrected = await _pipeline(tmp_path, api, classifier).reprocess(
            vault_path=filed.vault_path, user_hint="Add tag gemuesekiste2", sender_mxid=MARGE,
            initial_classification=filed.envelope["data"], reply_text="Add tag gemuesekiste2")

        assert corrected.status == "not_changed" and corrected.failure_reason
        assert memory.text(filed.vault_path) == before and len(memory.authors()) == commits


# ── A refused correction says so ─────────────────────────────────────────

async def test_a_correction_the_memory_refuses_changes_nothing_and_says_so(tmp_path, memory):
    """Given Homer gave the email's card a field an email card does not
    have, in Forgejo's editor, when Marge corrects it, then the memory
    refuses the correction: the card stays as Homer left it, and the
    archivist answers that the card was not changed, with the reason, and
    names no change it did not write."""
    api = MemoryAPI.from_container(memory.environ)
    filed = await _an_email(_pipeline(tmp_path, api, Classifier(FILED)), 1)
    path = filed.vault_path
    memory.edited_in_forgejo({path: memory.text(path).replace("type: email\n",
                                                              "type: email\nfiled_by: homer\n")},
                             by="homer", subject="edit: Field trip")
    homers = memory.text(path)

    corrected = await _pipeline(tmp_path, api, Classifier(
        {**FILED, "tags": ["school", "gemuesekiste2"]})).reprocess(
        vault_path=path, user_hint="Add tag gemuesekiste2", sender_mxid=MARGE,
        initial_classification=filed.envelope["data"])

    assert corrected.status == "not_changed"
    assert "filed_by" in corrected.failure_reason
    assert memory.text(path) == homers
    bot, chat = _archivist(tmp_path)
    await bot._reply_for_capture("!mail:simpson", corrected, "$fix",
                                 corrected_from=filed.envelope["data"])
    [answer] = chat.sent
    assert "not changed" in answer and corrected.failure_reason in answer
    assert "Reclassified" not in answer and "gemuesekiste2" not in answer
    assert chat.reactions == ["❌"]


# ── A refused first filing says so, for every kind of capture ────────────

def _offline(tmp_path) -> MemoryAPI:
    """A curator that does not answer."""
    return MemoryAPI("http://127.0.0.1:9", token="t", where="the curator")


CAPTURES = {
    "note": lambda p: p.capture_text(text="Pack sunscreen for the trip", sender_mxid=MARGE),
    "voice memo": lambda p: p.capture_text(text="Pick up Maggie at five", sender_mxid=MARGE,
                                           transcribed=True),
    "bookmark": lambda p: p.capture_url(url="https://krusty.test/news", sender_mxid=MARGE,
                                        notifier=Quiet()),
    "photo": lambda p: p.capture_binary(file_data=PIXEL, mime="image/png", filename="tent.png",
                                        source_uri="mxc://simpson/tent", sender_mxid=MARGE),
    "email": lambda p: _an_email(p, 1),
}


@pytest.mark.parametrize("kind", CAPTURES)
async def test_a_capture_the_memory_does_not_write_is_answered_as_not_filed(
        tmp_path, memory, kind):
    """Given the memory does not write a capture, then the archivist
    answers that it was not filed, with the reason, and puts ❌ on the
    message: never "✅ Captured". The family's people are read from the
    instance's clone; the curator the archivist writes to does not answer."""
    outcome = await CAPTURES[kind](_pipeline(tmp_path, _offline(tmp_path), Classifier(FILED)))

    assert outcome.status == "not_filed" and outcome.failure_reason
    assert outcome.envelope is None
    bot, chat = _archivist(tmp_path)
    if kind == "email":
        email = SimpleNamespace(event_id="$email", source={"content": {}})
        await bot._confirm_email_filing("!mail:simpson", email, outcome)
    else:
        await bot._reply_for_capture("!notes:simpson", outcome, "$capture")
    [answer] = chat.sent
    assert "not filed" in answer.lower() and outcome.failure_reason in answer
    assert "Captured" not in answer
    assert chat.reactions == ["❌"]


# ── Resting state: a write the memory makes is answered as before ────────

async def test_a_note_the_memory_writes_is_answered_with_its_card(tmp_path, memory):
    """Given the memory writes a note, then the archivist reacts ✅ and
    answers with the note's card, its title first."""
    outcome = await CAPTURES["note"](_pipeline(
        tmp_path, MemoryAPI.from_container(memory.environ), Classifier(FILED)))

    assert outcome.status == "captured" and outcome.vault_path
    bot, chat = _archivist(tmp_path)
    await bot._reply_for_capture("!notes:simpson", outcome, "$capture")
    [answer] = chat.sent
    assert answer.startswith("\U0001F4DD Field trip")
    assert chat.reactions == ["✅"]


# ── The answer is the card at a glance ──────────────────────────────────
# The type's icon and the title as the link
# to the card, then people and day, the summary cut short, the first three
# to-dos, the tags last; a correction names its changes in one line and
# folds the card as it is now away in the HTML, with every tag.

LINKS = "https://home.example/go"
LONG = {
    "title": "Class 4b ski week in Lindenberg",
    "persons": ["Bart", "Marge"],
    "summary": ("Class 4b goes on its ski week to Lindenberg from 18 to 22 January. "
                "The trip costs EUR 385 per child, paid in two parts to the trip account. "
                "Each child needs a signed consent form and a copy of the health insurance "
                "card. Ski rental must be ordered with the form.\n\n"
                "A parents' evening about the trip is on 24 November."),
    "facts": ["The hostel is in Lindenberg"],
    "action_items": [{"action": f"Step {n}", "due": f"2026-11-{19 + n}"} for n in range(1, 7)],
    "tags": ["school", "ski-week", "class-4b", "forms", "payment", "packing-list",
             "health", "winter"],
}


async def test_a_long_email_is_answered_as_a_short_card_linked_to_the_whole(tmp_path, memory):
    """Given an email with a long summary, six to-dos and eight tags, when it
    is filed, then the answer starts with the email icon and the title linked
    to the card, cuts the summary after its first paragraph's sentences that
    fit, shows three to-dos then "and 3 more", the tags last, four then +4,
    no facts, no bare link, and mentions nobody."""
    filed = await _an_email(_pipeline(tmp_path, MemoryAPI.from_container(memory.environ),
                                      Classifier(LONG)), 1)
    bot, chat = _archivist(tmp_path)
    bot.link_base_url = LINKS

    await bot._reply_for_capture("!mail:simpson", filed, "$email")

    [answer] = chat.sent
    blocks = answer.split("\n\n")
    card_id = filed.envelope["data"]["card_id"]
    assert blocks[0] == f"\U0001F4E7 [Class 4b ski week in Lindenberg]({LINKS}/card/{card_id})"
    assert blocks[1].startswith("\U0001F464 Bart, Marge · \U0001F4C5 ")
    assert blocks[2].startswith("> Class 4b goes on its ski week") and blocks[2].endswith(" …")
    assert "parents' evening" not in answer, "the second paragraph is the card's, not the chat's"
    todos = blocks[3].splitlines()
    # The year follows when it is not this year's, so only the start is fixed.
    assert [t[:len("- [ ] Step 1 📅 Fri 20 Nov")] for t in todos[:3]] == [
        "- [ ] Step 1 \U0001F4C5 Fri 20 Nov", "- [ ] Step 2 \U0001F4C5 Sat 21 Nov",
        "- [ ] Step 3 \U0001F4C5 Sun 22 Nov"]
    assert todos[3:] == ["- and 3 more"]
    assert blocks[-1] == "\U0001F3F7\uFE0F school · ski-week · class-4b · forms +4"
    assert "The hostel is in Lindenberg" not in answer
    assert not any(line.startswith("http") for line in answer.splitlines())
    assert chat.meta[0]["m.mentions"] == {}


async def test_a_correction_is_one_line_and_the_whole_card_folded_away(tmp_path, memory):
    """Given a filed email, when a correction renames it and adds Lisa and a
    tag, then the answer is one line naming the changes with the new title
    linked, followed by the card as it is now with every tag: shown open in
    the plain body, folded into "The card now" in the HTML."""
    api = MemoryAPI.from_container(memory.environ)
    filed = await _an_email(_pipeline(tmp_path, api, Classifier(LONG)), 1)
    corrected = await _pipeline(tmp_path, api, Classifier(
        {**LONG, "title": "Ski week 4b", "persons": ["Bart", "Marge", "Lisa"],
         "tags": [*LONG["tags"], "lisa-skis"]})).reprocess(
        vault_path=filed.vault_path, user_hint="Lisa comes too, tag lisa-skis",
        sender_mxid=MARGE, initial_classification=filed.envelope["data"],
        reply_text="Lisa comes too, tag lisa-skis")
    bot, chat = _archivist(tmp_path)
    bot.link_base_url = LINKS

    await bot._reply_for_capture("!mail:simpson", corrected, "$fix",
                                 corrected_from=filed.envelope["data"])

    [answer], [html] = chat.sent, chat.html
    link = f"{LINKS}/card/{filed.envelope['data']['card_id']}"
    line, card = answer.split("\n\n", 1)
    assert line == (f"\U0001F501 Updated [Ski week 4b]({link}): "
                    "renamed, Lisa added, tag lisa-skis added")
    assert card.startswith(f"\U0001F4E7 [Ski week 4b]({link})")
    assert card.endswith("winter · lisa-skis"), "a correction's card shows every tag"
    assert html.index("Updated") < html.index("<details><summary>The card now</summary>")
    assert "lisa-skis</p>\n</details>" in html
    assert chat.meta[0]["m.mentions"] == {}
