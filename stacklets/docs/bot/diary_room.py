"""The archivist in the memories room: the diary's face in the chat.

The memories room is the family's own. The archivist says nothing about
what is posted there. Once a day it posts each new diary card as a
notice in a thread on the entry's first message. The first time, one
notice in the room says how many memories were added and how to correct
a card; after that the cards are the announcement. After two weeks
without a new memory, one notice invites the next, once per quiet
stretch. A family member who replies in a card's thread, in writing or
by voice, is correcting that card: the archivist hands the words to the
memory stacklet, which applies them to the card in the vault and
commits them under that person's name, and then posts the corrected
card in the same thread.

The diary itself belongs to the memory stacklet. This module only talks
to the family and calls `stack memory diary` through the same entry
point the curator uses, so the compile, the cards and the vault writes
stay in one place. The curator compiles the diary every night whether
or not the archivist exists; the archivist's daily job compiles again
(cached, so cheap) and announces what is not announced yet.

Notices are `m.notice`, which Element's default push rules keep silent:
nothing here wakes a phone.

The daily job is deliberately simple: a check once a minute against a
local time of day and the date of the last run, which also catches up
after a night the machine slept through. A framework mechanism for jobs
like this one is planned; until then it lives here.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger

from stack.links import go_topic, public
from reply_presenter import _fact_lines
from text_utils import strip_reply_fallback

ENTRYPOINT = "/stacklets/memory/bot/cli_entrypoint.py"

CARD_FILED = "diary.filed"
CARD_RECLASSIFIED = "diary.reclassified"

# When the daily job runs, local time.
DEFAULT_JOB_AT = "07:30"
# How long one compile may take. The first one over a large room
# transcribes every recording in it.
JOB_TIMEOUT_S = 3 * 3600
CORRECT_TIMEOUT_S = 600
# On the first run, the room may hold years of memories. Only the recent
# ones get a card notice; the rest are counted in the summary.
FIRST_RUN_LOOKBACK_DAYS = 14
# The first announcement introduces the diary cards and how to correct
# one; after that each card in its thread is announcement enough.
EXPLAIN_TIMES = 1
# How long the diary may stay without a new memory before the room gets
# one notice that invites the next.
NUDGE_AFTER = timedelta(days=14)


# ── Pure parts ────────────────────────────────────────────────────────


# After a failed run, the job waits this long before trying again: the
# usual cause is the AI server being away, and a compile every minute
# until it is back helps nobody.
RETRY_AFTER = timedelta(minutes=30)


def household_now(now: datetime | None = None) -> datetime:
    """The time where the family lives.

    The bot runner's clock is UTC; the household's timezone comes from
    `TIMEZONE`, as it does for the diary compile. Without one, UTC.
    """
    now = now or datetime.now(timezone.utc)
    try:
        zone = ZoneInfo(os.environ.get("TIMEZONE", "").strip() or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return now.astimezone(zone)



def job_due(at: str, last_run: str, now: datetime) -> bool:
    """Whether the daily job should run now.

    `at` is "HH:MM" local time, `last_run` the local date of the last run
    ("" if never), `now` the local time. Due once a day, at or after `at`,
    so a machine that slept through the hour runs the job when it wakes.
    """
    try:
        hour, minute = (int(x) for x in at.split(":", 1))
    except ValueError:
        return False
    if last_run == now.date().isoformat():
        return False
    return (now.hour, now.minute) >= (hour, minute)


def read_report(stdout: str) -> dict | None:
    """The report `stack memory diary --json` prints on its last line."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                report = json.loads(line)
            except json.JSONDecodeError:
                return None
            return report if isinstance(report, dict) else None
    return None


def to_announce(report: dict, *, since_ms: int) -> tuple[list[dict], int]:
    """The cards to post, and how many unposted cards are older than that.

    A card is posted once, when its thread holds no card notice yet. On
    the first run `since_ms` holds back the room's backlog: those cards
    are counted, not posted one by one.
    """
    fresh, older = [], 0
    for card in report.get("cards") or []:
        if card.get("posted"):
            continue
        if int(card.get("at") or 0) >= since_ms:
            fresh.append(card)
        else:
            older += 1
    return fresh, older


def card_text(card: dict, t, *, corrected: bool = False, hint: bool = False) -> str:
    """The notice that shows a card in its thread.

    Shaped like the archivist's other filing replies (`reply_presenter`):
    the title, one row of topics and people, the summary, the facts. `t`
    is the archivist's translator, bound to the household language.
    """
    head = "diary_card_corrected" if corrected else "diary_card"
    lines = [t(head, title=card.get("title", ""), date=card.get("date_label")
               or card.get("date", ""))]
    about = [*card.get("tags", []), *card.get("persons", [])]
    if about:
        lines += ["", "  " + " | ".join(about)]
    if card.get("summary"):
        lines += ["", f"  {card['summary']}"]
    lines += _fact_lines(card.get("facts", []))
    if hint:
        lines += ["", f"  {t('diary_card_hint')}"]
    return "\n".join(lines)


def summary_text(count: int, *, link: str, t) -> str:
    """The introduction in the main timeline: how many memories were added
    and how to correct a card."""
    text = t("diary_intro_one" if count == 1 else "diary_intro", count=count)
    return f"{text} [{t('diary_open')}]({link})" if link else text


def quiet_since(report: dict, *, nudged_for: int, now: datetime) -> int | None:
    """The time of the newest memory, if the diary is due for a nudge.

    Due when the newest memory is at least `NUDGE_AFTER` old and that
    same memory has not had its nudge yet (`nudged_for`). A newer memory
    starts a new quiet stretch. An empty diary is never nudged.
    """
    newest = max((int(c.get("at") or 0) for c in report.get("cards") or []), default=0)
    if not newest or newest == nudged_for:
        return None
    if now - datetime.fromtimestamp(newest / 1000, timezone.utc) < NUDGE_AFTER:
        return None
    return newest


def envelope(card: dict, kind: str, *, actor: str) -> dict:
    """The famstack event the card notice carries: the item it files."""
    return {
        "source": "docs",
        "type": kind,
        "summary": card.get("title", ""),
        "data": {k: card.get(k) for k in (
            "entry_id", "path", "root", "date", "title", "persons", "tags")},
        "actor": actor,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


# ── The room ──────────────────────────────────────────────────────────


class DiaryRoom:
    """The archivist's behaviour in the memories room.

    Holds a reference to the bot for sending, reacting and reading
    threads; everything the diary knows comes from the memory CLI.
    """

    def __init__(self, bot, *, alias: str, job_at: str, state_path: Path):
        self.bot = bot
        self.alias = alias
        self.job_at = job_at
        self.state_path = Path(state_path)
        self._running = asyncio.Lock()

    def is_room(self, ctx) -> bool:
        return bool(self.alias) and ctx.alias == self.alias

    # ── State ──

    def _state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, state: dict) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("[archivist] could not save diary state: {}", e)

    def _t(self, key: str, **kwargs) -> str:
        return self.bot.t(key, **kwargs)

    # ── Routing ──

    async def on_text(self, room, event) -> None:
        """A message in the memories room, voice already decoded to text.

        Only a reply in one of our card threads is ours: it corrects the
        card. Everything else is the family's and gets no answer.
        """
        if self.bot.get_thread_root(event) is None:
            return
        if not await self.bot._thread_is_ours(room.room_id, event):
            return
        anchor = await self.bot._correction_anchor(room.room_id, event)
        if anchor is None:
            return
        _, found = anchor
        if not str(found.get("type", "")).startswith("diary."):
            return
        words = strip_reply_fallback(getattr(event, "body", "") or "").strip()
        if words:
            await self.correct(room.room_id, event, found, words)

    # ── Correcting ──

    async def correct(self, room_id: str, event, found: dict, words: str) -> None:
        data = found.get("data") or {}
        entry_id, root = data.get("entry_id"), data.get("root")
        if not entry_id:
            return
        await self.bot._react(room_id, event.event_id, "👀")
        by = event.sender.split(":")[0].lstrip("@")
        rc, out, err = await _diary_cli(
            "correct", entry_id, "--by", by, "--event", event.event_id,
            "--text", words, timeout=CORRECT_TIMEOUT_S)
        card = read_report(out) if rc == 0 else None
        if card is None:
            logger.warning("[archivist] diary correction failed rc={}: {}", rc,
                           err.strip().splitlines()[-1:] or "")
            await self.bot._answer(room_id, self._t("diary_correct_failed"),
                                   event.event_id, msgtype="m.notice")
            return
        await self.bot._send(
            room_id, card_text(card, self._t, corrected=True),
            reply_to=event.event_id, thread_root_event_id=root or card.get("root"),
            metadata={self.bot.FAMSTACK_EVENT_KEY: envelope(
                card, CARD_RECLASSIFIED, actor=self.bot.user_id)},
            msgtype="m.notice")
        await self.bot._react(room_id, event.event_id, "✅")

    # ── The daily job ──

    def may_run(self, now: datetime) -> bool:
        """False while a failed run is less than `RETRY_AFTER` ago."""
        failed = self._state().get("failed_at", "")
        try:
            return not failed or now - datetime.fromisoformat(failed) >= RETRY_AFTER
        except ValueError:
            return True

    async def loop(self) -> None:
        """Check once a minute whether the daily job is due, and run it.

        Waits before the first check: the loop starts with the bot, and
        the bot has to finish its first sync before it can post.
        """
        while True:
            await asyncio.sleep(60)
            try:
                now = household_now()
                if (job_due(self.job_at, self._state().get("last_run", ""), now)
                        and self.may_run(now)):
                    await self.run_job(now)
            except Exception as e:  # noqa: BLE001 - the loop must survive a bad day
                logger.exception("[archivist] diary job failed: {}", e)

    async def run_job(self, now: datetime | None = None) -> dict | None:
        """Compile the diary, post the cards not posted yet, and the summary."""
        now = now or household_now()
        async with self._running:
            state = self._state()
            if "since_ms" not in state:
                since = now - timedelta(days=FIRST_RUN_LOOKBACK_DAYS)
                state["since_ms"] = int(since.timestamp() * 1000)
            rc, out, err = await _diary_cli("--json", timeout=JOB_TIMEOUT_S)
            report = read_report(out) if rc == 0 else None
            if report is None:
                logger.warning("[archivist] diary job: compile failed rc={}: {}", rc,
                               err.strip().splitlines()[-1:] or "")
                state["failed_at"] = now.isoformat()
                self._save(state)
                return None
            state.pop("failed_at", None)
            room_id = report.get("room_id", "")
            fresh, older = to_announce(report, since_ms=int(state["since_ms"]))
            explain = int(state.get("explained", 0)) < EXPLAIN_TIMES
            for card in fresh:
                await self.bot._send(
                    room_id, card_text(card, self._t, hint=explain),
                    reply_to=card["root"], thread_root_event_id=card["root"],
                    metadata={self.bot.FAMSTACK_EVENT_KEY: envelope(
                        card, CARD_FILED, actor=self.bot.user_id)},
                    msgtype="m.notice")
            # Only the introduction carries a count. The backlog is in
            # it once: its cards never get a notice, so a daily count
            # would announce them again every morning.
            added = len(fresh) + older
            newest = quiet_since(report, nudged_for=int(state.get("nudged_for", 0)), now=now)
            if explain and added and room_id:
                link = public(go_topic("diary"), self.bot.link_base_url)
                await self.bot._send(room_id, summary_text(
                    added, link=link, t=self._t), msgtype="m.notice")
                state["explained"] = int(state.get("explained", 0)) + 1
            elif newest and room_id:
                await self.bot._send(room_id, self._t("diary_nudge"), msgtype="m.notice")
            # The introduction invites the next memory as well, so it
            # stands in for the nudge of the quiet stretch it opens.
            if newest:
                state["nudged_for"] = newest
            state["last_run"] = now.date().isoformat()
            self._save(state)
            logger.info("[archivist] diary job: {} card(s) posted, {} counted", len(fresh), older)
            return report


async def _diary_cli(*args: str, timeout: float) -> tuple[int, str, str]:
    """Run `stack memory diary` through its entry point, as the curator does."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, ENTRYPOINT, "diary", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy())
    except Exception as e:  # noqa: BLE001
        return 1, "", f"could not start: {e}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return 1, "", f"timed out after {timeout}s"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
