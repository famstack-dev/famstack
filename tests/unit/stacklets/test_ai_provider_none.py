"""`[ai] provider = "none"`: the stack runs without an AI, on purpose.

"none" is the admin's choice to start without an AI and add it later.
The stack then hands no AI, speech or transcription address to anything
it starts, and tells the bot runner and the memory's curator with one
flag (`AI_OFF=true`): the archivist files without tags and the curator
compiles without the model. `stack up` refuses a stacklet that needs the
AI. The listing marks the AI as off, the stacklets that need it as
waiting for it and the memory as running with parts waiting, which the
pages show. An empty `[ai]` (no route chosen yet) keeps today's address.

    [ai]
    provider = "none"

Driven through `Stack` as `stack up` and `stack list` use it, over the
repo's own stacklets, and through the archivist as the bot runner loads it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from stack import Stack

REPO = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")


def _stack(tmp_path, ai: str) -> Stack:
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\n\n[messages]\nserver_name = "simpson"\n' + ai)
    return Stack(REPO, tmp_path / "data", instance_dir=tmp_path)


NONE = '\n[ai]\nprovider = "none"\n'


# ── What the stack hands to what it starts ───────────────────────────────

@pytest.mark.parametrize("sid, key", [("core", "OPENAI_URL"), ("memory", "OPENAI_URL"),
                                      ("agent", "AGENT_OPENAI_URL")])
def test_with_provider_none_nothing_gets_an_ai_address(tmp_path, sid, key):
    assert _stack(tmp_path, NONE).env(sid)[key] == ""


def test_with_provider_none_the_bots_are_told_the_ai_is_off(tmp_path):
    """One flag says "off" (`AI_OFF`), for the bot runner and the curator,
    rendered from `[ai] provider`; an empty address is no signal."""
    stck = _stack(tmp_path, NONE)

    assert stck.env("core")["AI_OFF"] == "true"
    assert stck.env("memory")["AI_OFF"] == "true"


@pytest.mark.parametrize("sid, key", [("core", "SPEECH_URL"), ("core", "WHISPER_URL"),
                                      ("memory", "WHISPER_URL"), ("agent", "AGENT_WHISPER_URL")])
def test_with_provider_none_nothing_gets_a_speech_address(tmp_path, sid, key):
    """No transcription or speech server either: core's `WHISPER_URL` keeps
    only its path, which the client reads as none (`Whisper.from_env`)."""
    said = _stack(tmp_path, NONE).env(sid)[key]

    assert said.removesuffix("/audio/transcriptions") == ""


def test_an_empty_ai_keeps_the_address_it_had(tmp_path):
    """No route chosen yet is not "none": the address stays as before."""
    stck = _stack(tmp_path, "")

    assert stck.env("core")["OPENAI_URL"].startswith("http://")
    assert stck.env("core")["SPEECH_URL"].startswith("http://")
    assert stck.env("core")["AI_OFF"] == ""


# ── What the listing says ────────────────────────────────────────────────

def _rows(stck: Stack) -> dict:
    return {s["id"]: s for s in stck.list()["stacklets"]}


def test_the_listing_says_the_ai_is_off_and_who_waits_for_it(tmp_path):
    rows = _rows(_stack(tmp_path, NONE))

    assert rows["ai"]["ai_off"] is True
    assert {sid for sid, s in rows.items() if s.get("waits_for_ai")} == {"agent", "chatai"}
    assert {sid for sid, s in rows.items() if s.get("ai_waits")} == {"memory"}


def test_with_an_empty_ai_nothing_waits_for_the_ai(tmp_path):
    rows = _rows(_stack(tmp_path, ""))

    assert not rows["ai"].get("ai_off")
    assert not any(s.get("waits_for_ai") or s.get("ai_waits") for s in rows.values())


# ── The archivist ────────────────────────────────────────────────────────

async def test_the_archivist_says_why_a_letter_has_no_tags(tmp_path, monkeypatch):
    """Given the AI is off, when a letter is filed, then the reply says it
    is filed and searchable, and has no tags because no AI is set up."""
    monkeypatch.setenv("AI_OFF", "true")
    monkeypatch.setenv("OPENAI_URL", "http://host.docker.internal:42060/v1")
    from stacklets.docs.bot.archivist import ArchivistBot
    from stacklets.docs.bot.document_pipeline import FilingOutcome
    bot = ArchivistBot(homeserver="http://h", user_id="@archivist-bot:simpson", password="x",
                       session_dir=tmp_path, documents_room_alias="documents")
    bot._answer, bot._react = AsyncMock(), AsyncMock()

    await bot._reply_for_outcome("!documents:simpson", FilingOutcome(
        status="enriched", display_name="Steuerbescheid 2025.pdf", doc_id=7,
        link="http://localhost:42020/documents/7", has_text=True, classify_enabled=False), None)

    said = bot._answer.await_args.args[1]
    assert "Steuerbescheid 2025.pdf" in said
    assert "no AI is set up" in said
    # The room reads it: the person who set up famstack sets up the AI.
    assert "Whoever set up famstack" in said and "overview" in said
    assert "dashboard" not in said.lower()


def test_the_archivists_german_words_say_ubersicht():
    """The German page is the "Übersicht"; the chat names it so."""
    import yaml

    de = yaml.safe_load((REPO / "stacklets/docs/bot/messages/archivist.yml")
                        .read_text(encoding="utf-8"))["de"]

    for key in ("filed_without_ai", "ai_off"):
        assert "Übersicht" in de[key] and "Dashboard" not in de[key], de[key]


# ── The ai stacklet ──────────────────────────────────────────────────────

def test_starting_the_ai_stacklet_with_provider_none_says_how_to_turn_it_on(tmp_path):
    """`stack up ai` with "none" starts nothing and names the two routes."""
    from stack.hooks import build_hook_ctx
    from stacklets.ai.hooks import on_start

    stck = _stack(tmp_path, NONE)
    with pytest.raises(RuntimeError, match="stack ai switch managed"):
        on_start.run(build_hook_ctx("ai", stack=stck))


def test_installing_the_ai_stacklet_with_provider_none_installs_nothing(tmp_path):
    """`stack up ai` runs `on_install` before `on_start`: with "none" it
    refuses there too, before it builds or downloads anything."""
    from stack.hooks import build_hook_ctx
    from stacklets.ai.hooks import on_install

    stck = _stack(tmp_path, NONE)
    with pytest.raises(RuntimeError, match="stack ai switch managed"):
        on_install.run(build_hook_ctx("ai", stack=stck))


def test_checking_the_ai_with_provider_none_asks_nothing(tmp_path):
    """`stack ai check` with "none" says the stack runs without an AI,
    even when `[ai]` still names an address from before."""
    stck = _stack(tmp_path, '\n[ai]\nprovider = "none"\nopenai_url = "http://127.0.0.1:9/v1"\n')

    said = stck.run_cli_command("ai", "check", [])

    assert "without an AI" in said["error"]


@pytest.mark.parametrize("sid", ["agent", "chatai"])
def test_a_stacklet_that_needs_the_ai_does_not_start_without_one(tmp_path, sid):
    """`stack up agent` and `stack up chatai` with "none" start nothing
    and name the two routes: both would only ask an empty address."""
    stck = _stack(tmp_path, NONE)
    (tmp_path / "data").mkdir()

    said = stck.up(sid)

    assert "waits for the AI" in said.get("error", ""), said
    assert "stack ai switch managed" in said.get("hint", "")
