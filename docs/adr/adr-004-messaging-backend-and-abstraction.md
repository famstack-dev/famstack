# ADR-004: Messaging backend choice and abstraction layer

**Status:** Accepted

**Date:** 2026-03-18

---

## Context

The `messages` stacklet is the nervous system of famstack — bots, notifications,
and the family chat all run through it. Today it means Matrix/Synapse + Element,
hardwired at every level: the stacklet manifest, the CLI (`_matrix.py`), and the
`bots` stacklet (planned) will all import `matrix-nio` or hit Synapse HTTP
endpoints directly.

Before we go deeper, we need to answer two questions:

1. **Is Matrix the right choice?** Or should we support alternatives like
   Rocket.Chat or Nextcloud Talk?
2. **Should bots and CLI code program against the messaging backend directly,
   or against an abstraction?**

We evaluated all three platforms honestly. This ADR records what we found and
what we decided.

---

## Platform Evaluation

### Matrix / Synapse + Element — the current choice

**What it does well:**

- **Voice messages.** Element X has the best voice message UX of the three:
  press-hold or drag-to-lock recording, animated waveforms, variable playback
  speed. This matters daily — the family sends voice messages constantly.
- **Mobile apps.** Element X is native (Swift/Kotlin via Rust SDK), approaching
  iMessage quality. Push notifications work reliably through Element's free
  gateway with no registration or user limits.
- **Zero cloud dependency.** Fully self-contained. No mandatory cloud
  registration, no phone-home, no push notification limits. This is the only
  platform where "self-hosted" actually means self-hosted.
- **E2EE.** Production-grade (Olm/Megolm, audited, encrypts files). We keep
  it off by default on the LAN, but it's there if needed.
- **Bot ecosystem.** Most flexible. Bots are first-class Matrix users with full
  protocol access. Multiple SDKs (Python, Rust, JS, Go). We already have
  working bots (server-bot, archivist-bot, scribe, LLM bots).
- **Open protocol.** Clients are independent of the server — Element X,
  FluffyChat, SchildiChat all interoperate. Nobody's locked in.
- **Bridges.** The mautrix ecosystem can bridge WhatsApp, Signal, Telegram
  if desired later.

**What hurts:**

- **Resource usage.** Synapse + PostgreSQL is the heaviest option (~2-4 GB).
- **`server_name` is permanent.** Can never be changed after the first user.
  A real footgun for a project that wants to be forkable.
- **Database grows.** `state_groups_state` table bloats over time. Needs
  periodic maintenance with `synapse-compress-state`.
- **Foundation finances.** The Matrix.org Foundation ran a $356K deficit in 2024
  and warned about shutting down services. Element the company is healthier,
  but the protocol governance is at risk.
- **Client ecosystem is paper-thin.** More on this below.

### Rocket.Chat — evaluated and rejected

Rocket.Chat is a mature Slack-like platform with a good REST API and Docker
deployment. On paper it's a credible alternative. In practice, three things
disqualify it for famstack:

**1. Mandatory cloud registration.** Since v6.5, Rocket.Chat requires workspace
registration with their cloud service. This was introduced silently — no
changelog entry, automatic Pro trial activation, and a temporary 25-user limit
that confused everyone (GitHub issue #31149, overwhelmingly negative reactions).
This is antithetical to famstack's privacy-first principle.

**2. Push notifications are cloud-dependent.** The official mobile apps use
Rocket.Chat's push gateway (Apple/Google certificates are tied to their app
signing keys). Free tier: 10,000 pushes/month. Push notification content
privacy (hiding message text from the gateway) is a **premium-only feature**.
Self-hosting push requires forking the mobile app and building with your own
certificates — a non-trivial effort.

**3. E2EE is not production-ready.** Still labeled "beta" in their own docs.
File uploads are NOT encrypted. PBKDF2 uses only 1,000 iterations (OWASP
recommends 600,000+). No public security audit. Bots cannot read encrypted
messages at all.

Other concerns: MongoDB is required (replica set mandatory even for single
node), the free "Starter" tier caps at 50 users, and the v6.5 licensing
incident damaged community trust.

**Verdict: No.** The cloud registration requirement turns "self-hosted" into
"self-hosted but phone-home." The push notification model puts a cloud
middleman between our family's messages, with content privacy paywalled.

### Nextcloud Talk — interesting for the future, not ready as primary

Nextcloud Talk is a chat app built into Nextcloud. If you already run Nextcloud,
adding Talk is one click.

**What's good:**

- **Simplest backup.** Talk data is just rows in the Nextcloud database + files
  in the data directory. Nothing extra to worry about. Standard Nextcloud
  backup captures everything.
- **File sharing.** Seamless integration with Nextcloud Files — share from your
  cloud storage into a conversation with zero friction.
- **Bot support.** Well-designed webhook system with HMAC-SHA256 auth. Built-in
  Matterbridge for bridging to Slack, Teams, Matrix, IRC.
- **No cloud dependency.** Push proxy is self-hostable. No mandatory registration.
- **Company health.** Nextcloud GmbH is bootstrapped, profitable, with EU
  public sector contracts. Stable governance.

**What's not ready:**

- **Voice messages are basic.** No variable playback speed, no waveform
  visualization, just a simple audio player. A step down from Element X.
- **Mobile app is "good enough" but not a daily driver.** It's a chat tab
  inside a file management platform. Push notifications are the most common
  complaint — delayed or missed without careful configuration.
- **No real E2EE for chat.** Call encryption is partial (not enabled by
  default, not supported on all clients). Chat messages are not end-to-end
  encrypted — they're stored in the server database.
- **Requires full Nextcloud.** You can't run Talk standalone. Deploying
  an entire file sync + calendar + office suite just for messaging is
  disproportionate.
- **Group calls need HPB.** The High Performance Backend (signaling server +
  NATS + optionally Janus) is a separate multi-component system. Without it,
  video calls degrade above ~4 participants.

**Verdict: Not yet.** If Nextcloud joins the stack for files and calendars
(it's on the roadmap), Talk becomes a compelling secondary channel — especially
for the backup story. But as the primary family messaging platform, it doesn't
match Element X's polish for daily use.

---

## The Client Problem

Matrix's biggest weakness isn't the protocol or server — it's the mobile
client ecosystem.

The entire non-Element mobile client landscape for iOS + Android:

| Client | iOS | Android | Maintained by |
|--------|-----|---------|---------------|
| Element X | Yes | Yes | Element (company, ~50+ engineers) |
| FluffyChat | Yes | Yes | **One person** (krille-chan) |
| SchildiChat | No | Yes | Small team (Element X fork) |
| Commet | No | Yes (iOS planned) | Small team |

That's it. Two clients cover both platforms. One is the reference client from
the protocol company. The other is maintained by a single developer in Germany.

**Why so few?** The Matrix Client-Server spec is hundreds of pages. A minimum
viable client needs: sync (initial + incremental + sliding), room state
management, DAG-based event ordering, power levels, room versions, membership
transitions, redactions, read receipts, typing indicators, media upload/download
via `mxc://` URLs, push rules — and that's before E2EE (Olm, Megolm, key
backup, cross-signing, device verification). Building all of this to
production quality takes years. Most projects stall or die before reaching
"daily driver."

There is no business model for "alternative Matrix client." The people who
could build one need to pay rent, and a free consumer chat app doesn't do that.

**FluffyChat** is worth trying alongside Element X. It's explicitly designed
to feel like WhatsApp — lighter, more playful, less "enterprise." Same
homeserver, same rooms, different vibe. The family can mix clients.

**Practical takeaway for famstack:** Matrix's open protocol means client
choice is possible in theory. In practice, you get Element X or FluffyChat.
That's not great, but it's better than Rocket.Chat (one client, period) or
Nextcloud Talk (one client, tied to the Nextcloud app).

---

## Encryption Policy

E2EE is **off by default** and should stay that way.

**Already configured:**
- Synapse: `encryption_enabled_by_default_for_room_type: "off"`
- Element Web: `"UIFeature.e2ee": false`

**Rationale:** famstack is LAN-only. TLS (via Caddy) protects the wire. E2EE
adds device verification friction, key backup complexity, and "unable to
decrypt" failures — all with zero security benefit when the server is in your
basement. Disabling it removes an entire category of operational pain.

**Note for famstack (the product):** When `messages` ships as a stacklet, the
Synapse config template should set `encryption_enabled_by_default_for_room_type: "off"`
and the Element config should set `"UIFeature.e2ee": false`. Users who want
E2EE (e.g., for federation over the internet) can flip it on in config.

**Caveat:** Element X mobile may ignore the web config's `UIFeature.e2ee`
setting and still offer to enable encryption on new DMs. Worth testing and
documenting as a known limitation.

---

## The Abstraction Layer

### Decision

Program against an abstraction. Use the `BaseChannel` / `MessageBus` pattern
from `familykit-nanobot`, which already solves this for 13 platforms.

### Why — even if we never switch backends

The strongest argument for the abstraction is **not** "swap Matrix for
Rocket.Chat." Rocket.Chat is out. The arguments that hold:

1. **Nextcloud Talk as a secondary channel.** When Nextcloud joins the stack,
   bots could post to both Matrix and Talk rooms. The abstraction enables this
   with zero bot changes.

2. **Testability.** A `MemoryChannel` for unit tests means testing health check
   formatting, document classification, and transcription logic without a
   running Synapse. Today, every bot test requires a live Matrix server.

3. **Code hygiene.** Separating "what the bot does" (health checks, Paperless
   upload, transcription) from "how it talks to messaging" (nio sync loop,
   mxc:// media, E2EE key trust) is good architecture regardless of backend
   portability. The original `MicroBot` base class mixes both
   concerns in a way that makes bots hard to understand and modify.

4. **The client ecosystem fragility.** If Element X's direction diverges from
   what works for a family server, having an abstraction means we could move
   the server side without rewriting every bot. The protocol is great; the
   client situation is precarious.

### What already exists

`familykit-nanobot` has a clean, battle-tested channel abstraction:

```
nanobot/
  bus/events.py       → InboundMessage, OutboundMessage (protocol-agnostic)
  bus/queue.py        → MessageBus (async publish/consume)
  channels/base.py    → BaseChannel ABC: start(), stop(), send()
  channels/manager.py → ChannelManager (plugin registry + dispatcher)
  channels/matrix.py  → MatrixChannel (729 lines, E2EE, media, threads)
  + 12 more channels  → Telegram, Discord, Slack, WhatsApp, etc.
```

### What's new: admin interface

The nanobot abstraction covers runtime messaging (send/receive). But famstack
also needs server provisioning (create users, create rooms, join users).
These are also abstractable — 7 of 8 CLI operations have direct equivalents
across platforms:

```python
class BaseServerAdmin(ABC):
    async def login(self, username, password) -> bool: ...
    async def create_user(self, username, password, ...) -> bool: ...
    async def list_users(self) -> list[dict]: ...
    async def create_room(self, name, alias, ...) -> str | None: ...
    async def resolve_room(self, alias) -> str | None: ...
    async def join_user(self, room_id, user_id) -> bool: ...
    async def send(self, room, message) -> tuple[bool, str]: ...
```

Only `add_space_child()` (Matrix Spaces — hierarchical room grouping) is
truly platform-specific. It stays on the concrete `MatrixServerAdmin` class.

### Extraction plan

**Phase 1 — Extract library.** Move bus + channel base from `familykit-nanobot`
into `familykit-messaging`. Both nanobot and famstack depend on it. The
existing `MatrixChannel` (729 lines, battle-tested) comes along.

**Phase 2a — Admin interface.** Define `BaseServerAdmin`. Write
`MatrixServerAdmin` by refactoring `_matrix.py` — the code already exists.

**Phase 2b — Adapt bots.** One at a time, starting with server-bot (simplest:
send-only). Business logic unchanged, only the transport layer moves.

**Phase 2c — Adapt CLI.** Repoint `stack messages setup/send` from raw
Synapse calls to `BaseServerAdmin`.

**Phase 3 — New backend (when needed).** Write `NextcloudTalkChannel` +
`NextcloudTalkServerAdmin` when Nextcloud joins the stack.

### Multi-identity

Nanobot assumes one bot per channel. Famstack has multiple bot identities.
Solution: one `MatrixChannel` instance per bot, each with its own credentials.
Maps 1:1 to today's architecture (each bot is its own container).

---

## Decision

1. **Keep Matrix/Synapse + Element** as the messaging backend. It's the best
   fit for voice messages, mobile UX, bot support, and zero cloud dependency.
2. **Reject Rocket.Chat** for mandatory cloud registration, push notification
   limits, and damaged community trust.
3. **Plan for Nextcloud Talk** as a future secondary channel when Nextcloud
   joins the stack.
4. **Build the abstraction layer** by extracting from `familykit-nanobot`.
   Motivated by testability, code hygiene, and future optionality — not by
   an imminent backend switch.
5. **Keep E2EE off by default.** LAN-only servers get no benefit from it.
   Document how to enable it for users who federate.
6. **Try FluffyChat** alongside Element X. The family might prefer its lighter,
   more WhatsApp-like feel.

## Consequences

- The `messages` stacklet stays Matrix-first. No immediate code changes.
- Bots and CLI will eventually program against `BaseChannel` / `BaseServerAdmin`
  instead of `matrix-nio` / raw Synapse API — but this is a gradual migration,
  not a rewrite.
- `familykit-messaging` becomes a shared package between nanobot and famstack.
- Adding Nextcloud Talk later requires one `BaseChannel` impl + one
  `BaseServerAdmin` impl. Zero bot changes.
- The client choice is Element X or FluffyChat. Both work with any homeserver.
  The family can mix.
