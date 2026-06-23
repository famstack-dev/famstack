"""Unit tests for MailBot — config, rendering, state, and the poll cycle.

No real Matrix and no real IMAP: a fake fetcher feeds ParsedEmail objects and
`post_source_message` is replaced with a recorder. Exercises the bot's own
logic — account parsing from env, the twofold mapping, thread tracking, dedup,
and state persistence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (_ROOT / "stacklets" / "core" / "bot-runner", _ROOT / "stacklets" / "core" / "bot"):
    sys.path.insert(0, str(_p))

from stack.email_message import ParsedEmail  # noqa: E402
from mail import MailBot  # noqa: E402


def _email(*, subject="Hi", from_addr="office@school.example", mid="root@h",
           date="2026-06-21", body="hello", in_reply_to=None, references=None):
    return ParsedEmail(
        subject=subject, from_name=None, from_addr=from_addr, message_id=mid,
        date=date, body=body, references=references or [], in_reply_to=in_reply_to,
    )


def _bot(tmp_path):
    return MailBot(
        homeserver="http://hs", user_id="@mail-bot:hs", password="x",
        session_dir=str(tmp_path),
    )


class _FakeFetcher:
    """Mirrors MailFetcher.fetch_new: returns messages not in the seen set."""

    def __init__(self, messages):
        self.messages = messages

    def fetch_new(self, seen):
        return [m for m in self.messages if m.message_id not in seen]


def _record_posts(bot):
    posts: list[dict] = []

    async def rec(room_id, *, body, source, raw_content, fields,
                  thread_root_event_id=None):
        posts.append({
            "room": room_id, "body": body, "source": source,
            "raw_content": raw_content, "fields": fields,
            "root": thread_root_event_id,
        })
        return f"$e{len(posts)}"

    bot.post_source_message = rec
    return posts


# ── Config ───────────────────────────────────────────────────────────────

class TestConfig:

    def test_loads_account_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAIL_ACCOUNTS_JSON",
            '[{"name":"family","imap_host":"imap.x","imap_user":"f@x",'
            '"folder":"INBOX","room":"!post:hs"}]')
        monkeypatch.setenv("MAIL_FAMILY_IMAP_PASSWORD", "secret")
        bot = _bot(tmp_path)
        assert len(bot._accounts) == 1
        account, room = bot._accounts[0]
        assert room == "!post:hs"
        assert account.host == "imap.x"
        assert account.user == "f@x"
        assert account.password == "secret"
        assert account.folder == "INBOX"

    def test_password_embedded_in_rendered_json(self, tmp_path, monkeypatch):
        # The installer embeds the secret-store password in the JSON; no
        # separate env var needed.
        monkeypatch.delenv("MAIL_FAMILY_IMAP_PASSWORD", raising=False)
        monkeypatch.setenv("MAIL_ACCOUNTS_JSON",
            '[{"name":"family","imap_host":"imap.x","imap_user":"f@x",'
            '"imap_password":"rendered-secret","room":"!r:hs"}]')
        bot = _bot(tmp_path)
        assert len(bot._accounts) == 1
        assert bot._accounts[0][0].password == "rendered-secret"

    def test_account_skipped_without_password(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAIL_ACCOUNTS_JSON",
            '[{"name":"family","imap_host":"imap.x","imap_user":"f@x","room":"!r:hs"}]')
        monkeypatch.delenv("MAIL_FAMILY_IMAP_PASSWORD", raising=False)
        bot = _bot(tmp_path)
        assert bot._accounts == []  # no secret -> not configured

    def test_no_config_is_idle(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MAIL_ACCOUNTS_JSON", raising=False)
        bot = _bot(tmp_path)
        assert bot._accounts == []

    def test_bad_json_does_not_crash(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAIL_ACCOUNTS_JSON", "{not json")
        bot = _bot(tmp_path)
        assert bot._accounts == []


# ── Rendering ─────────────────────────────────────────────────────────────

class TestRendering:

    def test_human_body_has_subject_sender_and_text(self, tmp_path):
        bot = _bot(tmp_path)
        body = bot._human_body(_email(subject="Elternabend", from_addr="o@s",
                                      body="Bitte Formular zurueck."))
        assert "📧 **Elternabend**" in body
        assert "**From** o@s" in body
        assert "**Date** 2026-06-21" in body
        assert "Bitte Formular zurueck." in body
        # Blank line between header and body so markdown renders a real break.
        assert "\n\n" in body

    def test_human_body_shows_name_and_address(self, tmp_path):
        bot = _bot(tmp_path)
        p = _email(subject="S", body="b")
        p.from_name = "Springfield School"
        p.from_addr = "office@school.example"
        body = bot._human_body(p)
        assert "**From** Springfield School (office@school.example)" in body

    def test_raw_content_is_message_text(self, tmp_path):
        bot = _bot(tmp_path)
        assert bot._raw_content(_email(body="the body")) == "the body"

    def test_human_body_defangs_links(self, tmp_path):
        bot = _bot(tmp_path)
        body = bot._human_body(_email(
            subject="S",
            body="Login at [your bank](https://evil.example/x)",
        ))
        assert "your bank (`https://evil.example/x`)" in body
        assert "](http" not in body  # not a clickable markdown link

    def test_raw_content_drops_quoted_history(self, tmp_path):
        bot = _bot(tmp_path)
        body = (
            "Yes that works, see you Friday.\n\n"
            "On Fri, Nov 16, 2012 at 1:48 PM, Office <o@s> wrote:\n"
            "> Please confirm the parents-evening time.\n"
            "> Thanks\n"
        )
        # Only the new reply survives; the quoted ancestor is gone (the
        # Matrix thread already holds it).
        assert bot._raw_content(_email(body=body)) == "Yes that works, see you Friday."

    def test_strip_reply_falls_back_when_empty(self, tmp_path):
        bot = _bot(tmp_path)
        # A quote-only body that parses to nothing falls back to the original.
        assert bot._strip_reply("") == ""

    def test_source_fields_carry_email_descriptors(self, tmp_path):
        from stack.mail_fetcher import MailAccount
        bot = _bot(tmp_path)
        acc = MailAccount(host="h", port=993, user="u", password="p",
                          folder="INBOX", name="family")
        f = bot._source_fields(_email(from_addr="o@s", mid="root@h",
                                      subject="S", date="2026-06-21"), acc)
        assert f == {
            "from": "o@s", "subject": "S", "message_id": "root@h",
            "thread_root": "root@h", "captured_at": "2026-06-21",
            "account": "family", "folder": "INBOX",
        }


# ── State ─────────────────────────────────────────────────────────────────

class TestPollerStart:
    """The poller starts in register_callbacks (every launch), not in the
    once-ever on_first_sync welcome hook."""

    @pytest.mark.asyncio
    async def test_register_callbacks_starts_poller(self, tmp_path):
        from stack.mail_fetcher import MailAccount
        bot = _bot(tmp_path)
        bot._accounts = [(MailAccount(host="h", port=993, user="u",
                                      password="p"), "!r:hs")]
        bot._fetcher_factory = lambda acc: _FakeFetcher([])
        bot.register_callbacks(None)
        try:
            assert bot._poll_task is not None
        finally:
            if bot._poll_task:
                bot._poll_task.cancel()

    @pytest.mark.asyncio
    async def test_no_accounts_stays_idle(self, tmp_path):
        bot = _bot(tmp_path)
        bot._accounts = []
        bot.register_callbacks(None)
        assert bot._poll_task is None

    def test_does_not_define_on_first_sync(self):
        # on_first_sync is the once-ever welcome hook; the poller must not
        # ride it, so MailBot leaves it to the framework.
        assert "on_first_sync" not in MailBot.__dict__


class TestJoinWelcome:

    @pytest.mark.asyncio
    async def test_announces_bound_mailbox_and_folder(self, tmp_path):
        from stack.mail_fetcher import MailAccount
        bot = _bot(tmp_path)
        acc = MailAccount(host="h", port=993, user="family@example.org",
                          password="p", folder="INBOX")
        bot._accounts = [(acc, "!post:hs")]
        sent = []

        async def _send(room_id, text, *a, **k):
            sent.append((room_id, text))

        bot._send = _send
        await bot.on_room_joined("!post:hs")
        assert sent[0][0] == "!post:hs"
        assert "family@example.org" in sent[0][1]
        assert "INBOX" in sent[0][1]

    @pytest.mark.asyncio
    async def test_unbound_room_says_so(self, tmp_path):
        bot = _bot(tmp_path)
        bot._accounts = []
        sent = []

        async def _send(room_id, text, *a, **k):
            sent.append((room_id, text))

        bot._send = _send
        await bot.on_room_joined("!random:hs")
        assert "no mailbox" in sent[0][1].lower()


class TestState:

    def test_seen_and_threads_round_trip(self, tmp_path):
        bot = _bot(tmp_path)
        bot._seen.add("root@h")
        bot._threads["root@h"] = "$e1"
        bot._save_state()
        reborn = _bot(tmp_path)
        assert reborn._seen == {"root@h"}
        assert reborn._threads == {"root@h": "$e1"}


# ── Poll cycle ─────────────────────────────────────────────────────────────

class TestPoll:

    @pytest.mark.asyncio
    async def test_posts_each_message_and_tracks_thread(self, tmp_path):
        bot = _bot(tmp_path)
        from stack.mail_fetcher import MailAccount
        account = MailAccount(host="h", port=993, user="u", password="p")
        bot._accounts = [(account, "!room:hs")]
        root = _email(mid="root@h", date="2026-06-21", body="first")
        reply = _email(mid="reply@h", date="2026-06-22", body="the reply",
                       in_reply_to="root@h")
        bot._fetcher_factory = lambda acc: _FakeFetcher([reply, root])  # unsorted
        posts = _record_posts(bot)

        await bot._poll_once()

        # Oldest-first: the root posts before its reply (fragment is the
        # last body line, after the subject/sender header + blank line).
        assert [p["body"].splitlines()[-1] for p in posts] == ["first", "the reply"]
        # First message of the thread has no parent; reply threads under it.
        assert posts[0]["root"] is None
        assert posts[1]["root"] == "$e1"
        assert bot._threads == {"root@h": "$e1"}
        assert bot._seen == {"root@h", "reply@h"}

    @pytest.mark.asyncio
    async def test_second_poll_dedups(self, tmp_path):
        bot = _bot(tmp_path)
        from stack.mail_fetcher import MailAccount
        bot._accounts = [(MailAccount(host="h", port=993, user="u", password="p"),
                          "!room:hs")]
        msgs = [_email(mid="root@h"), _email(mid="reply@h", in_reply_to="root@h")]
        bot._fetcher_factory = lambda acc: _FakeFetcher(msgs)
        posts = _record_posts(bot)

        await bot._poll_once()
        assert len(posts) == 2
        await bot._poll_once()
        assert len(posts) == 2  # nothing new -> no re-post

    @pytest.mark.asyncio
    async def test_failed_post_leaves_message_unseen(self, tmp_path):
        bot = _bot(tmp_path)
        from stack.mail_fetcher import MailAccount
        bot._accounts = [(MailAccount(host="h", port=993, user="u", password="p"),
                          "!room:hs")]
        bot._fetcher_factory = lambda acc: _FakeFetcher([_email(mid="root@h")])

        async def fail(*a, **k):
            return None  # post failed

        bot.post_source_message = fail
        await bot._poll_once()
        assert bot._seen == set()  # not marked seen -> retried next cycle
