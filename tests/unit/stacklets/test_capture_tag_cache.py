# ruff: noqa: F811  (a fixture imported from another test module is the test's parameter)
"""The archivist keeps no capture tag cache: one person's tags never reach another's prompt.

The archivist once kept the tags of every filed capture in one file,
`capture-tags.json` in its data dir, and offered the most used ones in the
next capture's prompt, whoever sent it. One file for the whole bot holds
the tags of every bucket, so Marge's private "Homers 40th" reached the
prompt of Homer's family note, and no filter on one shared file keeps it
private. So the cache is neither read nor written. A file a bot left from
before stays where it is, unread.

These tests build the archivist's services as the bot does
(`archivist_services.build_services`) with its data dir in the test's
folder, and file through its capture pipeline into the real curator of
memory's test instance (`running_memory`), with one model standing in for
the AI (`tests.household`). The household is the Simpsons.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from stacklets.docs.bot.archivist import ArchivistBot
from stacklets.docs.bot.archivist_services import build_services
from tests.unit.stacklets.test_capture_names_people_and_tags import (  # noqa: F401  (fixtures)
    household, memory)

CACHE = "capture-tags.json"


@pytest.fixture
def session(tmp_path):
    """The archivist's data dir, as the bot runner mounts it."""
    folder = tmp_path / "archivist-session"
    folder.mkdir()
    return folder


@pytest.fixture
def services(memory, household, session, monkeypatch):
    """The archivist's services as the bot builds them, with the household's
    model and the memory's curator."""
    monkeypatch.setenv("OPENAI_URL", household.url)
    monkeypatch.setenv("OPENAI_KEY", "k")
    monkeypatch.setenv("MEMORY_API_URL", memory.environ["MEMORY_API_URL"])
    monkeypatch.setenv("CARDS_WRITE_TOKEN", memory.environ["CARDS_WRITE_TOKEN"])

    async def built():
        bot = ArchivistBot(homeserver="http://h", user_id="@archivist-bot:simpson", password="x",
                           session_dir=session)
        bot.shared_bucket = "family"
        services = build_services(bot)
        return bot, services

    bot, services = asyncio.run(built())
    yield services
    # The bot's shutdown: its HTTP session, which Paperless's client shares.
    asyncio.run(bot._aclose())


def files(services, words: str, *, by: str, bucket: str | None = None) -> str:
    outcome = asyncio.run(services.capture.capture_text(
        text=words, sender_mxid=f"@{by}:simpson", bucket=bucket))
    assert outcome.status == "captured", outcome
    return outcome.vault_path


def test_marges_tags_never_reach_homers_prompt(services, household):
    """Given Marge filed a note in her DM that the model tagged "Homers 40th
    Party Cake", when Homer files a family note, then his prompt holds no
    word of her tag."""
    household.says(persons=["Homer"], tags=["Homers 40th Party Cake"])
    files(services, "Order the cake for Saturday", by="marge")
    household.says(persons=["Homer"], tags=["Camper Van"])

    files(services, "We drove to the lake", by="homer", bucket="family")

    assert "Party Cake" not in household.prompts[-1]
    assert "40th" not in household.prompts[-1]


def test_a_cache_file_left_from_before_is_not_read_and_stays(services, household, session):
    """Given a cache file from before that holds "Homers 40th", when Homer
    files a family note, then his prompt holds none of it, and the file is
    left as it was."""
    old = json.dumps({"Homers 40th": {"count": 9, "last_seen": "2026-11-01"}})
    (session / CACHE).write_text(old, encoding="utf-8")
    household.says(persons=["Homer"], tags=["Camper Van"])

    files(services, "We drove to the lake", by="homer", bucket="family")

    assert "40th" not in household.prompts[-1]
    assert (session / CACHE).read_text(encoding="utf-8") == old


def test_a_capture_writes_no_cache_file(services, household, session):
    """Given a bot with no cache file, when Marge files a tagged note, then
    the bot's data dir holds no `capture-tags.json` afterwards."""
    household.says(persons=["Marge"], tags=["Camper Van"])

    files(services, "Fabric for the quilt", by="marge")

    assert not (session / CACHE).exists()
