"""Documents-room vs. capture-room routing in the archivist.

The archivist treats one room as the "documents" room — uploads + URLs
there flow into Paperless. Every other room is capture mode: URLs and
pasted text become summarized notes filed under the sender's own
entity bucket (`<sender>/notes/...` or `<sender>/bookmarks/...`), no
Paperless write.

These tests cover the integration glue between the bot and the
`room_context` builder: that the configured alias survives init, and
that `_room_context()` returns a snapshot whose `is_documents_room`
flag matches the alias on the room. The pure builder is exercised in
`test_room_context.py`; the end-to-end flow (extractor → classifier →
mirror) is exercised by `test_extractors.py` and `test_git_mirror.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from archivist import ArchivistBot  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────

BOT_ID = "@archivist-bot:server"


def _build_bot(tmp_path, **overrides) -> ArchivistBot:
    """Build an ArchivistBot without touching Matrix.

    Constructor reads env + settings only; no network/IO. The instance
    is fine for testing pure methods like `_room_context`.
    """
    return ArchivistBot(
        homeserver="http://homeserver",
        user_id=BOT_ID,
        password="x",
        session_dir=tmp_path,
        **overrides,
    )


def _room(*, canonical_alias=None, members=None, room_id="!r:server", name=None):
    return SimpleNamespace(
        room_id=room_id,
        canonical_alias=canonical_alias,
        name=name,
        users={uid: object() for uid in (members or [])},
    )


# ── Tests ────────────────────────────────────────────────────────────────

class TestDefaultDocumentsAlias:
    """`documents_room_alias` defaults to "documents" so existing
    bot.toml configs need no edits — the alias matches `room =
    "documents"` already declared there. Init normalizes the setting
    once so per-event lookups can do a cheap string equality check."""

    def test_default_is_documents(self, tmp_path):
        bot = _build_bot(tmp_path)
        assert bot.documents_room_alias == "documents"

    def test_setting_overrides(self, tmp_path):
        bot = _build_bot(tmp_path, documents_room_alias="archive")
        assert bot.documents_room_alias == "archive"

    def test_setting_is_normalized(self, tmp_path):
        bot = _build_bot(tmp_path, documents_room_alias="#archive:home.local")
        assert bot.documents_room_alias == "archive"


class TestIsDocumentsRoom:
    """`_is_documents_room(ctx)` is the archivist's bot-specific routing
    flag — alias on the context matches the configured docs alias.
    Lives on the subclass because the framework's RoomContext doesn't
    know about Paperless."""

    def test_room_matching_alias_is_documents(self, tmp_path):
        bot = _build_bot(tmp_path)
        room = _room(
            canonical_alias="#documents:server",
            members=[BOT_ID, "@homer:server"],
        )
        ctx = bot._room_context(room)
        assert bot._is_documents_room(ctx) is True

    def test_room_with_other_alias_is_not_documents(self, tmp_path):
        bot = _build_bot(tmp_path)
        room = _room(
            canonical_alias="#homer-notes:server",
            members=[BOT_ID, "@homer:server"],
        )
        ctx = bot._room_context(room)
        assert bot._is_documents_room(ctx) is False

    def test_dm_with_no_alias_is_not_documents(self, tmp_path):
        bot = _build_bot(tmp_path)
        room = _room(canonical_alias=None, members=[BOT_ID, "@homer:server"])
        ctx = bot._room_context(room)
        assert bot._is_documents_room(ctx) is False
        assert ctx.is_dm is True

    def test_unset_alias_disables_documents_routing(self, tmp_path):
        """An instance without Paperless can leave `documents_room_alias`
        empty in bot.toml. Every room should fall through to capture
        mode — captures don't depend on a running Paperless."""
        bot = _build_bot(tmp_path, documents_room_alias="")
        room = _room(
            canonical_alias="#documents:server",
            members=[BOT_ID, "@homer:server"],
        )
        ctx = bot._room_context(room)
        assert bot._is_documents_room(ctx) is False


class TestRoomContextIntegration:
    """`_room_context` is now inherited from MicroBot. These tests pin
    the integration — the bot's user id flows through to DM detection."""

    def test_three_member_room_is_not_dm(self, tmp_path):
        bot = _build_bot(tmp_path)
        room = _room(members=[BOT_ID, "@homer:server", "@marge:server"])
        ctx = bot._room_context(room)
        assert ctx.is_dm is False

    def test_two_member_room_with_bot_is_dm(self, tmp_path):
        bot = _build_bot(tmp_path)
        room = _room(members=[BOT_ID, "@homer:server"])
        ctx = bot._room_context(room)
        assert ctx.is_dm is True


class TestBotMentionDetection:
    """`_is_bot_mentioned` reads two signals: the MSC3952 mentions list
    (modern clients) and a plain mxid substring (older clients, hand
    typed). Either is enough; both absent means no mention."""

    @staticmethod
    def _event(body="hi", *, mentions_user_ids=None, source_present=True):
        source = {"content": {}}
        if mentions_user_ids is not None:
            source["content"]["m.mentions"] = {"user_ids": mentions_user_ids}
        return SimpleNamespace(
            body=body,
            source=source if source_present else None,
        )

    def test_mxid_in_mentions_list_detected(self, tmp_path):
        bot = _build_bot(tmp_path)
        ev = self._event(body="please search Pollos", mentions_user_ids=[BOT_ID])
        assert bot._is_bot_mentioned(ev) is True

    def test_other_mxid_in_mentions_list_ignored(self, tmp_path):
        # A mention of someone else in the same message is not a ping
        # for the bot.
        bot = _build_bot(tmp_path)
        ev = self._event(
            body="hey @homer look at this", mentions_user_ids=["@homer:server"],
        )
        assert bot._is_bot_mentioned(ev) is False

    def test_mxid_substring_in_body_detected(self, tmp_path):
        # Clients that don't populate m.mentions still encode the mxid
        # in the plain body when the user tab-completes a mention.
        bot = _build_bot(tmp_path)
        ev = self._event(body=f"{BOT_ID}: search Pollos")
        assert bot._is_bot_mentioned(ev) is True

    def test_localpart_mention_does_not_trip(self, tmp_path):
        # Casual mention of the bot's localpart in chat must not be
        # treated as an address — only the full mxid counts.
        bot = _build_bot(tmp_path)
        ev = self._event(body="the archivist-bot did it again")
        assert bot._is_bot_mentioned(ev) is False

    def test_plain_message_is_not_mention(self, tmp_path):
        bot = _build_bot(tmp_path)
        ev = self._event(body="Pollos")
        assert bot._is_bot_mentioned(ev) is False

    def test_missing_mentions_field_falls_through(self, tmp_path):
        # An event with `m.mentions = {}` (no user_ids key) must not crash.
        bot = _build_bot(tmp_path)
        ev = SimpleNamespace(
            body="hi",
            source={"content": {"m.mentions": {}}},
        )
        assert bot._is_bot_mentioned(ev) is False


class TestShouldReact:
    """The single seam for per-room mode gating. Two cases are
    contractually always-on (mention + DM); the third (group room, no
    mention) goes through `_room_mode_allows_react` which is the
    placeholder for future config."""

    async def test_mention_in_group_room_always_reacts(self, tmp_path):
        """An @-tag must never be ignored, regardless of room mode.
        Even if the room is configured react-only, the mention bypasses
        the mode lookup entirely."""
        bot = _build_bot(tmp_path)
        room = _room(
            canonical_alias="#family-chat:server",
            members=[BOT_ID, "@homer:server", "@marge:server", "@bart:server"],
        )
        ctx = bot._room_context(room)
        # Pin the contract by forcing the mode gate to deny — mention
        # must still win, never routing through the mode lookup.
        async def _deny(_ctx):
            return False
        bot._room_mode_allows_react = _deny
        assert await bot._should_react(ctx, mentioned=True) is True

    async def test_dm_always_reacts(self, tmp_path):
        """A 2-member room with the bot is a private chat. There's
        nobody else for the message to be aimed at, so the mode gate
        doesn't apply."""
        bot = _build_bot(tmp_path)
        room = _room(members=[BOT_ID, "@homer:server"])
        ctx = bot._room_context(room)
        async def _deny(_ctx):
            return False
        bot._room_mode_allows_react = _deny
        assert await bot._should_react(ctx, mentioned=False) is True

    async def test_documents_room_with_mention_reacts(self, tmp_path):
        # Mention beats every other consideration, including docs-room
        # routing — the upstream handlers still see ctx.is_documents_room
        # and dispatch accordingly.
        bot = _build_bot(tmp_path)
        room = _room(
            canonical_alias="#documents:server",
            members=[BOT_ID, "@homer:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        assert await bot._should_react(ctx, mentioned=True) is True

    async def test_group_room_no_mention_consults_mode(self, tmp_path):
        """The only branch that talks to the mode lookup. Pin the wiring
        so an accidental rewrite that hard-codes True in `_should_react`
        bypasses the seam."""
        bot = _build_bot(tmp_path)
        room = _room(
            canonical_alias="#family-chat:server",
            members=[BOT_ID, "@homer:server", "@marge:server", "@bart:server"],
        )
        ctx = bot._room_context(room)
        async def _allow(_ctx):
            return True
        bot._room_mode_allows_react = _allow
        assert await bot._should_react(ctx, mentioned=False) is True
        async def _deny(_ctx):
            return False
        bot._room_mode_allows_react = _deny
        assert await bot._should_react(ctx, mentioned=False) is False

    async def test_room_mode_reads_process_config(self, tmp_path):
        """`_room_mode_allows_react` reflects the room's `process` config:
        unset/auto → react to messages; `react` → ignore plain messages
        (reactions become the only trigger)."""
        bot = _build_bot(tmp_path)
        room = _room(
            canonical_alias="#whatever:server",
            members=[BOT_ID, "@a:server", "@b:server"],
        )
        ctx = bot._room_context(room)

        async def _auto(_room_id):
            return {}
        bot.get_room_config = _auto
        assert await bot._room_mode_allows_react(ctx) is True

        async def _react(_room_id):
            return {"process": "react"}
        bot.get_room_config = _react
        assert await bot._room_mode_allows_react(ctx) is False


class TestReactionDispatch:
    """`_on_reaction` routes a user's emoji to a registered handler. v1
    binding: 🔖 / 📌 bookmark the reacted message (the same capture
    auto-mode makes), attributed to the message author and keyed on the
    target event id so a drain replay or a second reactor dedups."""

    def _bot(self, tmp_path, *, target):
        bot = _build_bot(tmp_path)
        cap, txt = [], []

        async def _cap(room_id, url, sender, reply_to, *,
                       capture_id=None, user_hint=None):
            cap.append({"url": url, "sender": sender, "reply_to": reply_to,
                        "capture_id": capture_id, "hint": user_hint})

        async def _txt(room_id, text, sender, reply_to, *, capture_id=None):
            txt.append({"text": text, "sender": sender, "reply_to": reply_to,
                        "capture_id": capture_id})

        bot._handle_capture = _cap
        bot._handle_text_capture = _txt

        async def _get_event(room_id, event_id):
            return SimpleNamespace(event=target)

        bot._client = SimpleNamespace(room_get_event=_get_event)
        return bot, cap, txt

    @staticmethod
    def _reaction(key="🔖", reacts_to="$tgt", sender="@homer:server"):
        return SimpleNamespace(
            key=key, reacts_to=reacts_to, sender=sender,
            source={"content": {}},
        )

    @staticmethod
    def _target(body, sender="@marge:server"):
        return SimpleNamespace(
            sender=sender, body=body, source={"content": {"body": body}},
        )

    async def test_bookmark_url_message_captures(self, tmp_path):
        bot, cap, txt = self._bot(
            tmp_path, target=self._target("https://example.com/gear"),
        )
        await bot._on_reaction(_room(room_id="!r:server"), self._reaction())
        assert len(cap) == 1 and not txt
        assert cap[0]["url"] == "https://example.com/gear"
        assert cap[0]["sender"] == "@marge:server"   # message author, not reactor
        assert cap[0]["capture_id"] == "$tgt"         # idempotent on target id

    async def test_bookmark_text_message_captures_as_note(self, tmp_path):
        bot, cap, txt = self._bot(
            tmp_path, target=self._target("remember the boiler service in March"),
        )
        await bot._on_reaction(_room(), self._reaction())
        assert len(txt) == 1 and not cap
        assert txt[0]["text"].startswith("remember the boiler")
        assert txt[0]["capture_id"] == "$tgt"

    async def test_bookmark_embedded_url_passes_hint(self, tmp_path):
        bot, cap, txt = self._bot(
            tmp_path, target=self._target("camping gear list https://example.com/x"),
        )
        await bot._on_reaction(_room(), self._reaction())
        assert len(cap) == 1
        assert cap[0]["url"] == "https://example.com/x"
        assert cap[0]["hint"] == "camping gear list"

    async def test_variation_selector_emoji_still_dispatches(self, tmp_path):
        bot, cap, _ = self._bot(tmp_path, target=self._target("https://example.com"))
        await bot._on_reaction(_room(), self._reaction(key="🔖\uFE0F"))
        assert len(cap) == 1

    async def test_pushpin_emoji_dispatches(self, tmp_path):
        bot, cap, _ = self._bot(tmp_path, target=self._target("https://example.com"))
        await bot._on_reaction(_room(), self._reaction(key="📌"))
        assert len(cap) == 1

    async def test_unregistered_emoji_ignored(self, tmp_path):
        bot, cap, txt = self._bot(tmp_path, target=self._target("https://example.com"))
        await bot._on_reaction(_room(), self._reaction(key="👍"))
        assert not cap and not txt

    async def test_bot_reactor_ignored(self, tmp_path):
        bot, cap, txt = self._bot(tmp_path, target=self._target("https://example.com"))
        await bot._on_reaction(
            _room(), self._reaction(sender="@archivist-bot:server"),
        )
        assert not cap and not txt

    async def test_bot_authored_target_not_bookmarked(self, tmp_path):
        # 🔖 on the bot's own filing must not re-capture the filing.
        bot, cap, txt = self._bot(
            tmp_path,
            target=self._target("Filed: passport", sender="@archivist-bot:server"),
        )
        await bot._on_reaction(_room(), self._reaction())
        assert not cap and not txt


class TestOutcomeGlyph:
    """After a capture/filing finishes, the bot marks the source message
    with a terminal glyph alongside the 👀: ✅ when something was filed,
    ❌ on a genuine failure, nothing for a silent drop. The detailed
    reply lives in a thread, so this is the at-a-glance timeline signal."""

    CHECK = "✅"
    CROSS = "❌"

    def _bot(self, tmp_path):
        bot = _build_bot(tmp_path)
        reacts = []

        async def _react(room_id, eid, emoji):
            reacts.append(emoji)

        async def _noop(*a, **k):
            return None

        bot._react = _react
        bot._answer = _noop
        bot._send = _noop
        return bot, reacts

    async def test_capture_success_checks(self, tmp_path, monkeypatch):
        bot, reacts = self._bot(tmp_path)
        monkeypatch.setattr("archivist.render_capture_reply", lambda *a, **k: "x")
        o = SimpleNamespace(
            status="captured", source_title_hint="t", classification={},
            display_link="http://x", transcript=None, envelope=None,
        )
        await bot._reply_for_capture("!r:server", o, "$tgt")
        assert reacts == [self.CHECK]

    async def test_capture_extract_failed_crosses(self, tmp_path):
        bot, reacts = self._bot(tmp_path)
        o = SimpleNamespace(status="extract_failed", failure_reason="url")
        await bot._reply_for_capture("!r:server", o, "$tgt")
        assert reacts == [self.CROSS]

    async def test_capture_empty_gets_no_glyph(self, tmp_path):
        bot, reacts = self._bot(tmp_path)
        await bot._reply_for_capture("!r:server", SimpleNamespace(status="empty"), "$tgt")
        assert reacts == []

    async def test_filing_success_checks(self, tmp_path):
        bot, reacts = self._bot(tmp_path)
        o = SimpleNamespace(status="filed_no_details", display_name="x", link="y")
        await bot._reply_for_outcome("!r:server", o, "$tgt")
        assert reacts == [self.CHECK]

    async def test_filing_ocr_failed_crosses(self, tmp_path):
        bot, reacts = self._bot(tmp_path)
        o = SimpleNamespace(status="ocr_failed", display_name="x")
        await bot._reply_for_outcome("!r:server", o, "$tgt")
        assert reacts == [self.CROSS]


class TestPastePredicate:
    """`_looks_like_paste` is the gate between "chat in a capture room"
    and "this is content to summarize and file." The heuristic is
    length-based: a paste is at least 100 stripped characters.

    Anything shorter is treated as conversation and ignored. This is
    a deliberate undershoot — users who want short notes captured
    will paste them with enough context to clear the threshold."""

    @pytest.mark.parametrize("text", [
        # A typical Reddit-style paste — multi-paragraph, multi-line.
        ("Came across this thread on local inference benchmarks.\n\n"
         "Top comment claims 60 tok/s on M2 Pro with 8B quantized.\n"
         "Source: https://reddit.com/r/LocalLLaMA/comments/xyz"),
        # A long single-line paste — no newline but well over 100 chars.
        "This is a long-form paste typed as one continuous line by a "
        "user who really wanted to capture this thought in full, " * 2,
    ])
    def test_recognizes_paste(self, text):
        assert ArchivistBot._looks_like_paste(text)

    @pytest.mark.parametrize("text", [
        "ok",
        "thanks!",
        "?",
        "what's the status",
        "hello bot",
        "scan",
        # Multi-line short reply — still chat-shaped.
        "yes\nno",
    ])
    def test_short_chat_not_paste(self, text):
        assert not ArchivistBot._looks_like_paste(text)

    def test_empty_not_paste(self):
        assert not ArchivistBot._looks_like_paste("")
        assert not ArchivistBot._looks_like_paste("   \n\n  ")


# ── Reply fallback stripping ──────────────────────────────────────────────

class TestStripReplyFallback:
    """Matrix injects a `>`-quoted fallback at the top of a reply body so
    clients without rich-reply support still see context. The bot wants
    only the user's actual text — what comes after the fallback."""

    def test_strips_single_line_quoted_fallback(self):
        from archivist import _strip_reply_fallback
        body = (
            "> <@bot:test.local> Filed: Duff Insurance Kfz-Versicherung (#42)\n"
            "\n"
            "this is for Marge, not Homer"
        )
        assert _strip_reply_fallback(body) == "this is for Marge, not Homer"

    def test_strips_multi_line_quoted_fallback(self):
        from archivist import _strip_reply_fallback
        body = (
            "> <@bot:test.local> Filed: Duff Insurance (#42)\n"
            "> \n"
            "> Insurance | Homer | Invoice | Duff Insurance | 2026-03-15\n"
            "\n"
            "wrong year, it's actually 2025"
        )
        assert _strip_reply_fallback(body) == "wrong year, it's actually 2025"

    def test_no_quoted_block_returns_body_as_is(self):
        # A direct message without a reply — strip should leave it alone.
        from archivist import _strip_reply_fallback
        assert _strip_reply_fallback("plain question") == "plain question"

    def test_only_quoted_block_returns_empty(self):
        # If the body has no content after the fallback, the user's reply
        # is empty. Handler upstream should ignore an empty hint.
        from archivist import _strip_reply_fallback
        assert _strip_reply_fallback("> <@bot> Filed (#42)") == ""


# ── Mention as routing signal ─────────────────────────────────────────────

class TestMentionRoutesToSearch:
    """Mention promotes any free-text message to a search query, in any
    room. The mxid is stripped first so command matching and search see
    the actual content the user typed.

    These tests poke `_on_text` directly with a fake room/event and
    intercept handler calls. The dispatcher logic is the bit we're
    pinning; the handlers themselves are exercised elsewhere.
    """

    @staticmethod
    def _text_event(body, sender="@homer:server", *, mentions=None):
        content = {}
        if mentions is not None:
            content["m.mentions"] = {"user_ids": mentions}
        return SimpleNamespace(
            body=body,
            sender=sender,
            event_id="$evt:server",
            server_timestamp=1,
            source={"content": content},
        )

    @staticmethod
    def _room_obj(*, alias="#family-chat:server", members=None):
        return SimpleNamespace(
            room_id="!room:server",
            canonical_alias=alias,
            name=None,
            users={uid: object() for uid in (members or [BOT_ID, "@homer:server", "@marge:server"])},
        )

    @pytest.fixture
    def bot_with_recorder(self, tmp_path):
        """Build a bot wired with stubs that record handler dispatches.

        Avoids any I/O. The handlers under test (_handle_search,
        _handle_capture, _handle_text_capture) are replaced with
        recorders so we can assert which branch fired with which query.
        """
        bot = _build_bot(tmp_path)
        calls: list[tuple[str, str]] = []

        async def _record_search(room_id, query, reply_to=None, *, sender=None):
            calls.append(("search", query))

        async def _record_capture(room_id, url, sender, reply_to=None,
                                  *, capture_id=None):
            calls.append(("capture_url", url))

        async def _record_text_capture(room_id, text, sender, reply_to=None,
                                       *, capture_id=None):
            calls.append(("capture_text", text))

        async def _record_url(room_id, url, reply_to=None, **kw):
            calls.append(("paperless_url", url))

        async def _record_send(*a, **kw):
            calls.append(("send", a[1] if len(a) > 1 else ""))

        bot._handle_search = _record_search
        bot._handle_capture = _record_capture
        bot._handle_text_capture = _record_text_capture
        bot._handle_url = _record_url
        bot._send = _record_send
        # Reply-to lookup needs the client; short-circuit it.
        bot._reply_target_doc_id = lambda *_a, **_kw: _none_coro()
        bot._reply_target_capture_path = lambda *_a, **_kw: _none_coro()
        # The per-room welcome path runs ahead of routing decisions in
        # `_on_text` / `_on_file`. These tests focus on the routing
        # dispatch, not the welcome -- stub it out so the recorded
        # calls list stays clean. The welcome itself has its own test
        # file: tests/stacklets/test_archivist_welcome.py.
        bot._send_room_welcome_if_needed = lambda *_a, **_kw: _none_coro()
        return bot, calls

    @pytest.mark.asyncio
    async def test_mention_in_group_room_routes_to_search(self, bot_with_recorder):
        """A short free-text query in a group room is normally ignored,
        but a mention promotes it to search. The mxid is stripped so
        the search sees just "Pollos"."""
        bot, calls = bot_with_recorder
        room = self._room_obj()
        event = self._text_event(f"{BOT_ID} Pollos", mentions=[BOT_ID])
        await bot._on_text(room, event)
        assert calls == [("search", "Pollos")]

    @pytest.mark.asyncio
    async def test_paste_without_mention_still_captures(self, bot_with_recorder):
        """The mention is the gate. Without it, a long paste in a
        non-docs room continues to route to text capture — mention is
        an additive signal, not a replacement for the capture flow."""
        bot, calls = bot_with_recorder
        room = self._room_obj()
        long_text = "x" * 150
        event = self._text_event(long_text)
        await bot._on_text(room, event)
        assert calls == [("capture_text", long_text)]

    @pytest.mark.asyncio
    async def test_mention_overrides_capture_for_long_text(self, bot_with_recorder):
        """A long pasted query *with* a mention is the user explicitly
        asking — search wins over the capture default."""
        bot, calls = bot_with_recorder
        room = self._room_obj()
        body = f"{BOT_ID} " + ("did marge mention pollos lately " * 8)
        event = self._text_event(body, mentions=[BOT_ID])
        await bot._on_text(room, event)
        assert len(calls) == 1
        kind, query = calls[0]
        assert kind == "search"
        assert BOT_ID not in query  # mxid stripped
        assert "pollos" in query.lower()

    @pytest.mark.asyncio
    async def test_bare_mention_routes_to_help(self, bot_with_recorder):
        """A ping with nothing else falls back to the welcome message
        so the user gets something useful instead of an empty search."""
        bot, calls = bot_with_recorder
        room = self._room_obj()
        event = self._text_event(f"{BOT_ID}", mentions=[BOT_ID])
        await bot._on_text(room, event)
        assert calls and calls[0][0] == "send"  # welcome text via _send

    @pytest.mark.asyncio
    async def test_mention_with_url_still_routes_url(self, bot_with_recorder):
        """URL routing isn't changed by mention — `@bot https://x` in
        a non-docs room still bookmarks the URL."""
        bot, calls = bot_with_recorder
        room = self._room_obj()
        event = self._text_event(f"{BOT_ID} https://example.com/x", mentions=[BOT_ID])
        await bot._on_text(room, event)
        assert calls == [("capture_url", "https://example.com/x")]


async def _none_coro():
    return None
