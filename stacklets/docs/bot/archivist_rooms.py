"""archivist_rooms: what the archivist knows about the room a message came from.

    rooms = ArchivistRooms(bot, diary=diary, services=lambda: services)
    rooms.is_documents_room(ctx)              # the documents room feeds Paperless
    await rooms.topic_binding(room, sender)   # TopicBinding of a "Topic: ..." room, or None
    rooms.scope_bucket(room)                  # "marge" in a DM, "family" with two people
    await rooms.welcome_if_needed(room, ctx)  # the room's welcome, once

The archivist routes by room. One room is the documents room and feeds
Paperless; every other room is a capture room. A room whose name starts
with `Topic:` or `Thema:` is a topic room, and its captures file into
the topic's folder. Who is in a room decides the scope: one person means
that person's own bucket, two or more the shared one. Bots never count.

The topic binding is the bot's own `dev.famstack.capture` timeline
event, not room state, because a bot has power level 0 in a room a
person created and cannot write state there. It is read back newest
first, cached for the process, and followed when a topic is renamed.

The welcome works the same way: the room's own history says whether
the bot has posted there, because a read needs no power level. The
`help` command shows the same text, so a person re-reads what fits
where they ask.

The bot (a MicroBot) is the chat port: its client, `t`, `send`,
`room_option` and its settings. Nothing here imports the bot's module.
"""

from __future__ import annotations

from typing import Callable

from loguru import logger

from stack.bot import RoomContext
from stack.cards.lookup import renamed_topic
from stack.vault import RESERVED_TOPIC_IDS

from .document_pipeline import utc_now_isoformat
from .pipeline import DEFAULT_LANGUAGE
from .topic_rooms import (
    TopicBinding,
    binding_from_state,
    is_reserved,
    make_room_state,
    parse_topic_name,
    renamed_state,
    scope_from_members,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .archivist_services import ArchivistServices

__all__ = ["ArchivistRooms"]


class ArchivistRooms:
    """The archivist's view of its rooms: kind, topic, scope and welcome.

    Holds two caches for the life of the process: the rooms already
    welcomed and the topic bindings already read, so each room costs at
    most one history query for each.

        rooms = ArchivistRooms(bot, diary=diary, services=lambda: services)
        binding = await rooms.topic_binding(room, "@marge:simpson")
        binding.bucket if binding else rooms.scope_bucket(room)
    """

    def __init__(self, bot, *, diary, services: Callable[[], "ArchivistServices | None"]):
        self._bot = bot
        self._diary = diary
        # The vault the rename check looks into; None before the bot started.
        self._services = services
        # Rooms known to be welcomed this session: caches the own-messages
        # history query in `welcome_if_needed`.
        self._welcomed_rooms: set[str] = set()
        # Topic bindings resolved this session: caches the own-messages
        # history query in `topic_binding` the same way.
        self._topic_bindings: dict[str, TopicBinding] = {}

    def _ai_status(self) -> str:
        if self._bot.openai_url:
            return self._bot.t("ai_on")
        return self._bot.t("ai_off")


    # ── Documents-room routing ───────────────────────────────────────────
    #
    # One room is the documents room: file uploads + URL pastes go through
    # Paperless. Every other room the bot is joined to runs the capture
    # pipeline — URLs and pasted text become summarized markdown entries
    # filed under the sender's own entity bucket (`<sender>/notes/...`
    # or `<sender>/bookmarks/...`), no Paperless write.
    #
    # Family members can spin up their own per-person rooms ("homer
    # notes", "marge notes") or DM the bot directly. As soon as the
    # bot accepts the invite, that room is in capture mode by default.
    # No allowlist to curate.
    #
    # Resolution is per-event, not pinned at startup. The framework's
    # `_room_context` reads `room.canonical_alias` from the current nio
    # state on every callback; we compare against the configured alias
    # in the routing dispatcher. This survives restarts, alias renames,
    # and the documents room being joined late.

    def is_documents_room(self, ctx) -> bool:
        """Archivist-specific routing flag: alias matches our docs alias.

        Sits next to the documents-room routing comment because that's
        what it's *for*; the framework's RoomContext stays generic and
        knows nothing about Paperless. An empty alias (deployments
        without Paperless) collapses every room to capture mode.
        """
        return bool(self._bot.documents_room_alias) and ctx.alias == self._bot.documents_room_alias


    # ── Topic-room routing ─────────────────────────────────────────────
    #
    # A Matrix room whose name starts with `Thema:` (de) or `Topic:` (en)
    # is a topic room. The archivist reads `dev.famstack.capture` state on
    # every capture; when no state exists yet, it parses the room name
    # and bootstraps inline (the lazy path). Future on_invite hook can
    # call the same bootstrap eagerly. Either way the result is a
    # `TopicBinding` the capture pipeline threads into routing + tag
    # seeding. Full design: docs/design/brain/topic-rooms.md.

    def _reserved_topic_slugs(self) -> set[str]:
        """Slugs the archivist refuses to bootstrap as topics.

        Topics nest inside their owning bucket (shared under
        `<shared_bucket>/`, personal under `<localpart>/`), so the
        slug only has to avoid collisions with within-bucket reserved
        directories: the capture-type folders the mirror writes
        (`notes`, `bookmarks`, `documents`), the correspondent index
        used in the shared bucket, the family's `lists`, the `_unfiled`
        rescue folder, and the derived `about` page.

        Top-level vault names (`family`, `homer`, `marge`, `meta`,
        `wiki`, `archive`) no longer need to appear here -- the new
        layout makes top-level collisions impossible because topic
        folders never live at the top level.
        """

        return set(RESERVED_TOPIC_IDS)


    async def _read_topic_state(self, room_id: str) -> dict | None:
        """Fetch the room's `dev.famstack.capture` binding, or None.

        The binding lives as the bot's own custom TIMELINE event, not
        room state: message events send at PL 0, while state events
        need PL 50 — which the bot never has in a user-created topic
        room, so a state-event binding silently failed to persist and
        the scope re-derived (and could flap) on every capture. Read
        back via a sender+type-filtered `/messages` page, newest
        first, so the latest binding wins if the room is ever rebound
        (future promotion handler). None on miss or any nio error;
        bootstrap treats both as "no binding yet".
        """

        if self._bot._client is None:
            return None
        try:
            resp = await self._bot._client.room_messages(
                room_id,
                limit=3,
                message_filter={
                    "senders": [self._bot.user_id],
                    "types": ["dev.famstack.capture"],
                },
            )
        except Exception as e:
            logger.debug(
                "[archivist] topic binding read failed for {}: {}",
                room_id, e,
            )
            return None
        for ev in getattr(resp, "chunk", None) or []:
            source = getattr(ev, "source", None) or {}
            content = source.get("content")
            if isinstance(content, dict):
                return content
        return None


    async def _write_topic_state(self, room_id: str, content: dict) -> bool:
        """Post the `dev.famstack.capture` binding as the bot's own
        timeline event. True on success, False on any nio error (the
        bootstrap is best-effort; the binding still applies in memory
        for the current capture). Custom event types are invisible in
        Element, so the room stays clean."""

        if self._bot._client is None:
            return False
        try:
            await self._bot._client.room_send(
                room_id, "dev.famstack.capture", content,
            )
            return True
        except Exception as e:
            logger.warning(
                "[archivist] topic binding write failed for {}: {}",
                room_id, e,
            )
            return False


    def human_members(self, room) -> list[str]:
        """Room members that are humans — every bot account excluded.

        Uses the framework's `-bot` convention (MicroBot.is_bot_user), so a
        room with mail-bot + archivist-bot + one person counts as one human,
        not three. This is the basis of scope/visibility: one human → personal,
        two or more → shared.
        """
        users = getattr(room, "users", None) or {}
        return [u for u in users if not self._bot.is_bot_user(u)]


    def count_humans(self, room) -> int:
        return len(self.human_members(room))


    def scope_owner_localpart(self, room, sender_mxid: str, scope) -> str:
        """Localpart that owns a personal-scope topic bucket.

        For a human paste the sender *is* the sole human, so the sender works.
        But a bot-posted source (email) has a bot sender — so a personal topic
        must nest under the room's lone human, not the bot. Resolve that here:
        the single human member when personal, else the sender (shared scope,
        or any non-single-human fallback).
        """
        if scope == "personal":
            humans = self.human_members(room)
            if len(humans) == 1:
                return humans[0].split(":")[0].lstrip("@").lower()
        return sender_mxid.split(":")[0].lstrip("@").lower()


    def scope_bucket(self, room) -> str:
        """Bucket for bot-posted content from a room with no topic binding.

        The membership rule without a topic slug: a sole human (a DM, a
        one-person private room) files under that person; two or more humans
        file under the shared bucket. This is what makes email delivered to a
        DM land in that person's bucket — a DM is just a room with one human.
        Human-sent captures keep their own sender-based routing; this is only
        the fallback for bot-posted content (which has a bot sender).
        """
        humans = self.human_members(room)
        if len(humans) == 1:
            return humans[0].split(":")[0].lstrip("@").lower()
        return self._bot.shared_bucket


    def write_in(self, ctx: RoomContext) -> str | None:
        """The language the room chose for its filings (`!config language`),
        or None for the household language.

        Read from the message's context (`_message_context`), the config
        routing already decided on, so a filing costs no second read:
        `{"language": "en"}` gives "en", `{}` or "default" gives None.
        """
        choice = self._bot.room_option(ctx, "language")
        return None if choice in ("", DEFAULT_LANGUAGE) else choice


    async def topic_binding(self, room, sender_mxid: str) -> TopicBinding | None:
        """The room's topic binding, following a rename of its topic.

        A rename (`stack memory topic rename`) moves the topic's folder
        and lists the old id on the topic card. Every binding is checked
        against the vault, the cached one too, so no capture files into
        the old folder once it is gone: the archivist posts a binding for
        the new id and files there (`_followed`).
        """

        binding = await self._bound_topic(room, sender_mxid)
        if binding is None:
            return None
        return await self._followed(room.room_id, binding)


    async def _followed(self, room_id: str, binding: TopicBinding) -> TopicBinding:
        """`binding`, or the binding to the id a rename gave its topic.

        One directory check on the vault while the bound folder is there.
        Once it is gone, the topic card that lists the bound id among its
        former ids names the topic now (`renamed_topic`). The new binding is
        posted like any other, newest wins, and cached once it landed.
        """

        services = self._services()
        root = services.vault.root() if services is not None else None
        owner = binding.bucket.rpartition("/")[0]
        found = renamed_topic(owner, binding.slug, root=root) if root and owner else None
        if found is None:
            return binding
        content = renamed_state(binding, topic=found[0], title=found[1])
        renamed = binding_from_state(content) or binding
        wrote = await self._write_topic_state(room_id, content)
        logger.info("[archivist] topic {!r} was renamed: room {} now files into {!r}",
                    binding.slug, room_id, renamed.bucket)
        if wrote:
            self._topic_bindings[room_id] = renamed
        else:
            self._topic_bindings.pop(room_id, None)
        return renamed


    async def _bound_topic(self, room, sender_mxid: str) -> TopicBinding | None:
        """Read existing topic state, or bootstrap if the room name
        matches the prefix pattern.

        Returns the routing binding for the capture pipeline, or None
        when the room isn't a topic room (no matching name, no state).
        Bootstrap is idempotent and best-effort: a write failure
        leaves the in-memory binding live for the current capture and
        the next one re-tries.
        """

        if room is None:
            return None
        cached = self._topic_bindings.get(room.room_id)
        if cached is not None:
            return cached
        state = await self._read_topic_state(room.room_id)
        binding = binding_from_state(state)
        if binding is not None:
            self._topic_bindings[room.room_id] = binding
            return binding
        return await self._bootstrap_topic(room, sender_mxid)

    async def _bootstrap_topic(self, room, sender_mxid: str) -> TopicBinding | None:
        """Bind a room whose name reads as a topic, and write its state.

        Returns None when the name is no topic or names a reserved bucket.
        """
        room_name = (
            getattr(room, "name", None)
            or getattr(room, "display_name", None)
            or ""
        )
        parsed = parse_topic_name(room_name)
        if parsed is None:
            return None
        if is_reserved(parsed.slug, self._reserved_topic_slugs()):
            logger.info(
                "[archivist] refusing topic bootstrap: slug {!r} from "
                "room {!r} collides with a reserved bucket name",
                parsed.slug, parsed.display_name,
            )
            return None

        scope = scope_from_members(self.count_humans(room))
        # Personal scope nests under the room's sole human, not the message
        # sender, so a bot-posted email in a one-person room lands under that
        # person, not under @mail-bot.
        owner_localpart = self.scope_owner_localpart(room, sender_mxid, scope)
        content = make_room_state(
            parsed=parsed, scope=scope,
            bootstrapped_by=sender_mxid,
            sender_localpart=owner_localpart,
            shared_bucket=self._bot.shared_bucket,
            bootstrapped_at=utc_now_isoformat(),
        )
        wrote = await self._write_topic_state(room.room_id, content)
        logger.info(
            "[archivist] bootstrapped topic {!r} → bucket {!r} (scope={}, "
            "bootstrapped_by={})",
            parsed.display_name, content["bucket"], scope, sender_mxid,
        )
        binding = binding_from_state(content)
        # Cache only when the binding event landed: a failed write must
        # leave the next capture free to re-bootstrap and retry it.
        if binding is not None and wrote:
            self._topic_bindings[room.room_id] = binding
        return binding


    def room_by_id(self, room_id: str):
        """Look up the live nio Room object for a given room id.

        Capture handlers receive ``room_id`` (a string) from their
        dispatchers; the topic bootstrap needs the Room object for
        its display name and member list. Returns None when the
        client isn't connected or the room isn't tracked locally.
        """

        if self._bot._client is None:
            return None
        rooms = getattr(self._bot._client, "rooms", None) or {}
        return rooms.get(room_id)


    # ── Per-room welcome ─────────────────────────────────────────────
    #
    # The archivist greets every room it enters with a context-aware
    # welcome (documents / topic / DM / generic capture) the first time
    # it sees an event there. The gate is the room's own history: a
    # sender-filtered `/messages` query asks Synapse whether the bot
    # has ever posted in the room. A read needs no power level, so the
    # gate works in user-created rooms where the bot (PL 0) cannot
    # write state events -- the failure mode of the previous
    # state-event gate, which silently re-welcomed on every message.
    # An in-memory cache keeps it to one history query per room per
    # process lifetime. The `help` / `hilfe` command serves the same
    # welcome text directly so the user re-reads exactly what fits
    # where they are asking. Project rule: self-explaining UX, see
    # memory `project_self_explaining_ux.md`.

    @staticmethod
    def _display_name(room) -> str:
        return (
            getattr(room, "name", None)
            or getattr(room, "display_name", None)
            or ""
        )


    def welcome_kind_for(self, room, ctx) -> str:
        """Decide which welcome variant fits this room.

        Topic rooms win over every other classification: a `Thema:` /
        `Topic:` prefix is the strongest signal of user intent and
        overrides documents-room aliases or DM shape. The remaining
        order is documents > personal (DM) > capture (fallback).
        """

        name = self._display_name(room)
        if parse_topic_name(name) is not None:
            return "topic"
        if self.is_documents_room(ctx):
            return "documents"
        if getattr(ctx, "is_dm", False):
            return "personal"
        return "capture"


    def welcome_text_for(self, room, ctx) -> str:
        """Render the welcome text for this room's kind, filling vars.

        The documents and personal variants reuse the existing welcome
        plumbing (ai_status, paperless URL); the topic variant computes
        the bucket path the family will see for captures here so the
        welcome doubles as a navigation hint to Forgejo.
        """

        kind = self.welcome_kind_for(room, ctx)
        parsed = parse_topic_name(self._display_name(room))
        if kind == "topic" and parsed is not None:
            scope = scope_from_members(self.count_humans(room))
            if scope == "shared":
                bucket = f"{self._bot.shared_bucket}/{parsed.slug}"
            else:
                users = getattr(room, "users", None) or {}
                humans = [u for u in users if u != self._bot.user_id]
                if humans:
                    localpart = humans[0].split(":")[0].lstrip("@").lower()
                    bucket = f"{localpart}/{parsed.slug}"
                else:
                    bucket = parsed.slug
            return self._bot.t(
                "welcome_topic",
                display=parsed.display_name,
                slug=parsed.slug,
                bucket=bucket,
            )
        if kind == "documents":
            return self._bot.t(
                "welcome_documents",
                url=self._bot.paperless_public_url,
                ai_status=self._ai_status(),
            )
        if kind == "personal":
            return self._bot.t("welcome_personal")
        return self._bot.t("welcome_capture")


    async def _bot_has_posted_in(self, room_id: str) -> bool:
        """True when the bot's own messages already exist in the room.

        One sender-filtered `/messages` page, scanned backwards from
        the latest event — the homeserver does the filtering, so an
        active room does not force deep pagination. Returns False on
        any nio error: a transient hiccup then re-welcomes (rare,
        visible, recoverable), which beats silently never greeting a
        brand-new room.
        """

        if self._bot._client is None:
            return False
        try:
            resp = await self._bot._client.room_messages(
                room_id,
                limit=3,
                message_filter={
                    "senders": [self._bot.user_id],
                    "types": ["m.room.message"],
                },
            )
        except Exception as e:
            logger.debug(
                "[archivist] welcome history check failed for {}: {}",
                room_id, e,
            )
            return False
        chunk = getattr(resp, "chunk", None) or []
        return any(
            getattr(ev, "sender", None) == self._bot.user_id for ev in chunk
        )


    async def welcome_if_needed(self, room, ctx) -> None:
        """Post the room-appropriate welcome on first encounter.

        Idempotent without write permission: the gate asks the
        homeserver whether the bot has posted in the room before. The
        in-memory cache is marked before the send, so a burst of
        events in a fresh room cannot produce a second welcome while
        the first send is in flight.
        """
        # The memories room gets no welcome: the diary's first daily
        # notices explain what the cards are, in the family's own room.
        if self._bot._diary.is_room(ctx):
            return

        if room is None:
            return
        room_id = room.room_id
        if room_id in self._welcomed_rooms:
            return
        if await self._bot_has_posted_in(room_id):
            self._welcomed_rooms.add(room_id)
            return
        self._welcomed_rooms.add(room_id)
        text = self.welcome_text_for(room, ctx)
        await self._bot._send(room_id, text)
