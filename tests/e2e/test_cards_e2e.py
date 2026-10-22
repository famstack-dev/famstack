"""The 0.5 card flows on the live rig, against the real model.

Each test drives the stack the way the family does (a message in a notes
room, a reply, `stack memory card edit`, `stack memory write`) and checks
what the model's wording cannot change: which files exist, who made which
commit, what the sidecar records, where a link leads. Nothing is stubbed,
so these run in the instance's own AI mode (`local`), in the first step
of `stacktests e2e`.

Cards the model titles cannot be found by a title prefix, so each test
finds its card by the commit that filed it and removes it by path.

Run with `-s` to stream the BDD narration live:

    tests/e2e/stacktests pytest -s tests/e2e/test_cards_e2e.py
"""

from __future__ import annotations

import asyncio
import http.client
import tempfile
import time
from datetime import datetime
from pathlib import Path

import yaml

from tests.e2e.test_capture_binary_e2e import (
    ARCHIVIST_MXID,
    MEMORY_OWNER,
    MEMORY_REPO,
    _create_notes_room,
    _now_ms,
    _wait_for_bot_reply,
)

RESOLVER = ("localhost", 42000)


# ── Helpers ───────────────────────────────────────────────────────────────

def _frontmatter(text: str) -> dict:
    _, fm, _ = text.split("---\n", 2)
    return yaml.safe_load(fm) or {}


async def _card_filed_by(code, subject_prefix: str, author: str, since: float,
                         timeout: int = 180) -> str | None:
    """The card file of the newest commit by `author` whose subject starts
    with `subject_prefix`, made after `since` (epoch seconds)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for commit in code.list_commits(MEMORY_OWNER, MEMORY_REPO, limit=10):
            info = commit.get("commit") or {}
            message = str(info.get("message") or "")
            who = str((info.get("author") or {}).get("name") or "")
            at = datetime.fromisoformat(str((info.get("author") or {}).get("date") or
                                            "1970-01-01T00:00:00+00:00")).timestamp()
            if message.startswith(subject_prefix) and who == author and at >= since - 5:
                cards = [f["filename"] for f in commit.get("files") or []
                         if f["filename"].endswith(".md")]
                if cards:
                    return cards[0]
        await asyncio.sleep(3)
    return None


async def _commit_on(code, path: str, subject_prefix: str, author: str,
                     timeout: int = 180) -> bool:
    """Whether a commit by `author` whose subject starts with `subject_prefix`
    touches `path`, waiting up to `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for commit in code.list_commits(MEMORY_OWNER, MEMORY_REPO, path=path, limit=5):
            info = commit.get("commit") or {}
            if str(info.get("message") or "").startswith(subject_prefix) \
                    and (info.get("author") or {}).get("name") == author:
                return True
        await asyncio.sleep(3)
    return False


def _remove(code, *paths: str) -> None:
    for path in paths:
        try:
            code.delete_file(MEMORY_OWNER, MEMORY_REPO, path, message=f"chore: test cleanup {path}")
        except Exception:
            pass


def _go(card_id: str) -> tuple[int, str]:
    """`GET /go/<card_id>` on core's resolver, without following the redirect."""
    conn = http.client.HTTPConnection(*RESOLVER, timeout=10)
    try:
        conn.request("GET", f"/go/{card_id}")
        answer = conn.getresponse()
        return answer.status, answer.getheader("Location") or ""
    finally:
        conn.close()


async def _go_until(card_id: str, status: int, timeout: int = 120) -> tuple[int, str]:
    """`_go` until it answers `status` (the brain takes a push to follow)."""
    deadline = time.monotonic() + timeout
    answer = _go(card_id)
    while answer[0] != status and time.monotonic() < deadline:
        await asyncio.sleep(3)
        answer = _go(card_id)
    return answer


# ── A person's edit survives a reply ──────────────────────────────────────

async def test_marges_tag_survives_homers_reply_to_the_note(bdd, code, homer, test_stack, scope):
    """A note is filed from the chat; Marge adds a tag in the card editor;
    Homer replies to the filing with a correction.

    Then the refile is Homer's `reply:` commit, his words are a reply
    source in the sidecar, and Marge's tag is still on the card: a
    rebuild keeps a person's edit (ADR-018 §7).
    """
    bdd.scenario("Marge's tag survives Homer's reply to the note")
    marges_tag = scope.tag("camping-gear")
    marker = scope.uid

    room_id = await _create_notes_room(homer, f"Homer Cards {scope.uid}")
    bdd.given(f"Homer's notes room, with {ARCHIVIST_MXID}")

    since, sent_ms = time.time(), _now_ms()
    words = ("For the camping trip next weekend: bring three sleeping bags, the big "
             f"tent and the camping stove, and ask Flanders for his lantern. [{marker}]")
    await homer.room_send(room_id=room_id, message_type="m.room.message",
                          content={"msgtype": "m.text", "body": words})
    bdd.when("Homer writes a note in the room")

    path = await _card_filed_by(code, "capture:", "homer", since)
    assert path, "no `capture:` commit by homer within 180s; check the bot-runner logs"
    side = path.removesuffix(".md") + ".json"
    scope.on_cleanup.append(lambda _uid: _remove(code, path, side))
    card_id = _frontmatter(code.get_file(MEMORY_OWNER, MEMORY_REPO, path)["content"])["card_id"]
    bdd.ok(f"filed {path} ({card_id})")

    filed = await _wait_for_bot_reply(homer, room_id, after_ts_ms=sent_ms,
                                      envelope_type="capture.filed", timeout=120)
    assert filed is not None, "the archivist posted no capture.filed confirmation"

    bdd.given("Marge adds a tag with the card editor")
    # The card editor runs in the curator, which knows a card once it has
    # pulled the push that filed it: a few seconds, as in the card browser.
    deadline = time.monotonic() + 60
    while True:
        edit = test_stack.run("memory", "card", "edit", card_id, "--add", f"tags={marges_tag}",
                              "--as", "marge")
        if "no card" not in str(edit.get("_stderr", "")) or time.monotonic() > deadline:
            break
        await asyncio.sleep(3)
    assert edit.get("ok", True) and not edit.get("error"), edit
    assert await _commit_on(code, path, "edit:", "marge", timeout=60), "no `edit:` commit by marge"

    correction = f"Correction: we need four sleeping bags, Bart is coming too. [{marker}]"
    await homer.room_send(room_id=room_id, message_type="m.room.message", content={
        "msgtype": "m.text", "body": correction,
        "m.relates_to": {"m.in_reply_to": {"event_id": filed.event_id}}})
    bdd.when("Homer replies to the filing with a correction")

    assert await _commit_on(code, path, "reply:", "homer"), \
        "no `reply:` commit by homer on the note within 180s"
    bdd.then("the refile is Homer's reply commit")

    card = _frontmatter(code.get_file(MEMORY_OWNER, MEMORY_REPO, path)["content"])
    assert marges_tag in (card.get("tags") or []), \
        f"Marge's tag {marges_tag!r} did not survive the refile: {card.get('tags')}"
    sources = code.load_sidecar(MEMORY_OWNER, MEMORY_REPO, path)["sources"]
    assert [s["role"] for s in sources][:1] == ["original"], sources
    assert any(s["role"] == "reply" and s["person"] == "homer" for s in sources), sources
    bdd.ok("Marge's tag kept; Homer's correction is a reply source")


# ── Links by card id ──────────────────────────────────────────────────────

async def test_a_card_id_opens_its_page(code):
    """`/go/<card_id>` leads to the page that shows the card: a person to
    their page, every correspondent with a page to that page (its own
    path, not its card's), and an id no card has ever carried to a 404."""
    status, location = await _go_until("person-homer", 302)
    assert status == 302 and location.rstrip("/").endswith("/homer/about"), (status, location)

    # Correspondents that are cards now and have a page; a page left from
    # a card that was removed answers 410, which is right for it.
    cards = {e["path"].split("/")[-1].removesuffix(".md")
             for e in code.list_tree(MEMORY_OWNER, MEMORY_REPO)
             if e.get("type") == "blob" and e["path"].startswith("family/correspondents/")
             and e["path"].count("/") == 2 and e["path"].endswith(".md")}
    pages = {e["path"].split("/")[-2] for e in code.list_tree(MEMORY_OWNER, "brain")
             if e.get("type") == "blob" and "/correspondents/" in e["path"]
             and e["path"].endswith("/about.md")}
    for slug in sorted(cards & pages)[:5]:
        status, location = _go(f"correspondent-{slug}")
        assert status == 302 and location.endswith(f"/correspondents/{slug}/about"), \
            (slug, status, location)

    assert _go("note-0000000000")[0] == 404


# ── stack memory write files a note as the chat does ─────────────────────

async def test_stack_memory_write_files_a_new_note_with_its_sources(bdd, code, test_stack, scope):
    """Homer writes a new note with `stack memory write`: the capture
    pipeline files it (Homer's `capture:` commit), its sidecar names his
    words as the original from the command line, and `/go` opens it."""
    bdd.scenario("stack memory write files a new note like the chat")
    marker = scope.uid
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(f"The grill needs new charcoal before Saturday, and Flanders still has "
                 f"our tongs. [{marker}]\n")
    since = time.time()

    answer = test_stack.run("memory", "write", f"homer/notes/{marker}.md", "--by", "homer",
                            "--from", fh.name, "--json")
    Path(fh.name).unlink(missing_ok=True)
    assert not answer.get("error"), answer

    path = await _card_filed_by(code, "capture:", "homer", since, timeout=120)
    assert path and path.startswith("homer/notes/"), path
    side = path.removesuffix(".md") + ".json"
    scope.on_cleanup.append(lambda _uid: _remove(code, path, side))
    [source] = code.load_sidecar(MEMORY_OWNER, MEMORY_REPO, path)["sources"]
    assert (source["role"], source["channel"], source["person"]) == ("original", "cli", "homer"), source
    card_id = _frontmatter(code.get_file(MEMORY_OWNER, MEMORY_REPO, path)["content"])["card_id"]
    assert (await _go_until(card_id, 302))[0] == 302, f"/go/{card_id} does not open the note"
    bdd.ok(f"{path} ({card_id}) filed with its source")


# ── The diary tags by topic id ────────────────────────────────────────────

def test_no_diary_card_tags_a_topic_by_its_label(test_stack, code):
    """After migration `memory/0010` every diary card names a topic by its
    id (`medical`), never by its label (`Gesundheit`). A tag no rule reads
    (an old `Person:` tag on a card the family owns) is reported by the
    format check and left alone; that is not a label."""
    from stack.ontology import Ontology
    from stack.tags import tag_for

    ontology = Ontology.loads(code.get_file(MEMORY_OWNER, MEMORY_REPO, "ontology.toml")["content"])
    report = test_stack.run("memory", "check-format", "--json")
    labels = [(entry["path"], tag) for entry in report.get("invalid_tags") or []
              if "/diary/" in entry["path"]
              for tag in entry["tags"] if tag_for(tag, ontology) in ontology.topics]
    assert labels == [], f"diary cards still tag topics by label: {labels}"
