# Bot Interaction Patterns

> Status: Design note — future direction, no implementation yet
> Created: 2026-06-09
> Author: Homer + Claude
> Depends on: [topic-rooms.md](topic-rooms.md) (room-state-as-intent, the seed of this design), [knowledge-architecture.md](knowledge-architecture.md), [wiki-engine.md](wiki-engine.md)

## Why this exists

The archivist's `_on_text` in `stacklets/docs/bot/archivist.py` has accumulated a routing ladder of seven branches: help commands, scan begin / end, `show N`, bare URL, embedded URL with hint (just shipped), mentioned-or-docs (search), paste-shaped, else-ignored. Each branch keys on a different heuristic — content shape, mention, room type, length, prefix substring. The ladder grew organically: every time a real user message fell through, a new branch was added.

The 2026-06-09 topic-rooms test surfaced the failure mode this pattern produces. Homer sent `Interesting facts: <url>` in a topic room. `is_just_url` rejected it (text before the URL). `looks_like_paste` rejected it (under 100 characters). The `ignored` branch swallowed the whole message. The URL was the obvious payload; the bot saw nothing to do.

The fix (`f93c684`) added an embedded-URL branch with `_first_url`. That branch will catch most variations of "comment + URL" — but more silent-drop bugs are almost certainly lurking in the ladder. The right move is not another branch. It is a structural rethink: stop sniffing content for intent, and lean on signals the user is already in control of.

This note records the shape of that rethink. Not in scope for the topic-rooms branch. Captured here so the next time the routing produces a silent-drop bug, the answer is "do the rethink" instead of "add another branch."

## What we learned from maubot

Maubot is the dominant Matrix bot framework. I read its handler surface and audited its ~30 official plugins to see how the ecosystem handles "is this for the bot, about the bot, or a thing to save?"

Three patterns are exposed; the first two carry almost all the weight:

| Pattern | Decorator | Triggers | Where it's used |
|---|---|---|---|
| **Explicit command** | `@command.new(name="x")` | `!x <args>` (configurable prefix) | The dominant pattern. ~30/30 plugins. Hierarchical via `@subcommand`, typed args via `@argument`. |
| **Passive regex** | `@command.passive(regex=...)` | Message body matches a tight pattern | Used for narrow signatures — `sed` for `s/foo/bar/`, `karma` for `name++`, `dice` for `1d20`. Not used for vague content shape. |
| **Raw event handler** | `@event.on(EventType.REACTION)` | Any reaction event in the room | Primitive only. Zero canonical plugins use this for capture UX. |

The signal is clear: **the Matrix-native answer is to make the user explicit.** Either a command prefix (`!save https://...`) or a tight pattern (`URL pattern, nothing else`). Heuristic content sniffing — what the archivist does today — is outside the maubot mainstream.

Slack and Discord went the other direction historically: reaction-based capture (Slack's `Reacji Channeler`, Discord's `Pinned Bot`) became a beloved pattern. Drop 🔖 on a message; the bot picks it up. Matrix exposes the primitive but nobody has built the polished version of this UX as a plugin. Open territory.

## The three-layer direction

The archivist should stop reading the user's mind and let the user point. Three layers, each fitting a different intent shape; each layer Matrix-native; each layer composes with the next.

### Layer 1: emoji reactions (per-message routing knob)

The cheapest, most immediately satisfying piece. The user picks a message, picks an emoji, the bot acts. No content sniffing, no command memorisation. Built on `EventType.REACTION`. The user controls disambiguation by choosing which emoji.

Reactions run in **both directions** — the user reacts to signal intent to the bot, and the bot reacts to signal state back to the user. The same primitive serves both halves of the conversation.

#### User reacts to the bot's filing or to their own messages

| Emoji | Action |
|---|---|
| 🔖 / 📌 | Bookmark this message to the current topic — URL if present, text otherwise |
| 🗑 / ❌ | Redact + ignore — undo a wrong capture, mark the message as not-for-the-bot |
| 👎 | "Bot, your classification was wrong" — opens a reply-thread correction prompt |
| 📅 | Surface this for the calendar / reminder bot (once it lands) |

A 🔖 on Homer's message means "save this." A 👎 on the bot's `Filed: Duff Insurance camping addendum` means "you got the topic wrong." A 🗑 on the bot's own capture confirmation means "undo." Each binding maps to an existing handler the archivist already implements; the reaction is just a different trigger.

#### Bot reacts to the user's message to signal processing state

The current archivist posts a separate `📷 Received photo from {sender}. Analyzing...` (and ditto for documents, voice memos, URL fetches) before the final filing reply. The intermediate message is noise — it carries no information the final reply does not, and it doubles the timeline traffic per capture.

Replace with **👀 reaction on the source message** the moment the bot starts work. Same liveness signal, attached directly to the message being processed, no separate timeline event per capture.

| Surface today | Replaced by |
|---|---|
| `received_photo` / `received_document` / `received_voice` | 👀 reaction on the user's upload |
| `reading_classifying` (mid-flow) | (covered by the same 👀 — no separate signal needed) |
| `capture_fetching` (`Reading example.com...`) | 👀 reaction on the user's URL message |
| `page_received` per page in batch mode | 👀 → ✅ per page (eye while processing, checkmark when stored), removes seven `Page N received` lines |

**What stays as text:**

- `scan_started` — instructional, tells the user the next move ("send pages, then `)` to combine"). Keep.
- `filed` / `captured` / `already_filed` — carry the link and title. Keep.
- All error messages and `welcome` / `help`. Keep.

**Closure semantics for 👀:** Leave the eyes in place after success. They double as a visual marker in scrollback that the bot processed this message. The final detailed filing reply takes over the closure role. On failure the error reply does the same; the 👀 just means "we tried."

#### Cost

~2-3h for the user-to-bot bindings (the four-emoji table) plus unit tests around reaction-event parsing. Add ~1-2h to flip the existing "Received X, processing..." messages to 👀 reactions on the source. Both halves use the same `EventType.REACTION` plumbing — the bot half is just emitting reactions instead of subscribing to them.

### Layer 2: direct mentions (NLU command surface)

@-mentioning the bot removes ambiguity — the user is talking to it. The current archivist treats @-mentions as search-only. Generalize: the LLM parses intent from the mention text and dispatches to existing handlers.

Bounded intent set (so the prompt stays tight and dispatch stays deterministic):

| Intent | Example | Dispatch |
|---|---|---|
| save | `@archivist save this https://...` | `_handle_capture` |
| forget | `@archivist forget that Duff Insurance stuff` | redaction + tombstone |
| search | `@archivist what did we note about camping?` | `_handle_search` (existing) |
| reclassify | `@archivist this is actually Marge's, not Homer's` | `_handle_reply_reprocess` (existing) |
| remind | `@archivist remind me about the Duff Insurance renewal in November` | reminder bot when it lands |
| status | `@archivist what have you filed today?` | digest |

A thin LLM pass classifies the mention's intent into one of those slots, then dispatches to the existing handler. The user does not learn a command syntax — they ask naturally; the LLM bridges to the existing surface.

**Cost:** ~3-4h to wire if the intent prompt is bounded and dispatch is unchanged. The handlers all exist; the new layer is just an intent classifier on the mention text.

### Layer 3: natural conversation (reply chains)

The Matrix-native pattern for multi-turn UX is to ride the reply graph. The archivist already does this for reclassification corrections — the user replies to a `Filed: ...` message with a fix, the bot reads the reply, re-runs classification. State lives in the chain; no persistence outside Matrix.

Extending the pattern:

- The bot asks clarifying questions when uncertain ("Filed under Camping — was this for the upcoming trip, or just gear reference?")
- The user replies "yes," "no, separate," or "actually file it under Insurance"
- The bot reads the reply, applies the correction or confirmation
- The reply graph IS the conversational state — no state machine code in the bot

Two preconditions: a confidence threshold on classifications (only ask when uncertain), and a question template the LLM can fill ("Filed under X — looks like a Y. Right?"). The reply handler already exists.

**Cost:** ~5-6h, mostly in the prompt design and the question template library.

## Order of work

Each layer is independently shippable; later layers compose with earlier ones; nothing forces sequencing except that earlier layers prove the direction before the next layer's cost is committed.

1. **Emoji reactions first.** Cheapest. Most immediately satisfying. Validates "user-controlled routing" without changing existing flows. If the family naturally reaches for 🔖, the direction is right. If they never do, before-and-after metrics on silent-drops tell us nothing has improved and the next layer's cost is unjustified.
2. **Direct-mention NLU second.** Once reactions feel right, mentions become the power-user surface for things reactions can't express ("forget everything tagged camping from last week"). The LLM intent classifier is fully isolated — its output dispatches to handlers that already work.
3. **Conversational reply-chain UX third.** Biggest lift, biggest payoff. Builds on (1) and (2): a reaction or mention initiates a conversation, the bot's question opens a thread, the user's reply continues it. Won't ship until the deriver work lands because uncertainty signals (the trigger for asking a clarifying question) come from there.

## What this displaces, what it composes with

This direction does **not** rip out the existing content-heuristic routing. It pushes it down a layer:

- **Today's `_on_text` ladder becomes the fallback.** Bare URLs still file as bookmarks when nothing else triggers. The default room behavior stays. The new layers add explicit-intent on top.
- **Topic rooms still set room default.** The room-state-as-intent piece in [topic-rooms.md](topic-rooms.md) is exactly the right primitive at the room scope. The new layers act at the message scope.
- **The documents room stays as it is.** Files going to Paperless via the documents room's content-shape detection is established UX; no need to change it.

The three-layer direction is **additive**. It gives the user better-controlled paths into the same handlers, not replacements for them.

## Open questions

1. **Emoji binding registry.** Hardcode the 🔖 / 🗑 / 👎 / 📅 mapping in `bot.toml`, or make it user-configurable? Default-hardcoded for v1; configurable later if households diverge.
2. **Mention NLU model choice.** A small fast model (Qwen3 4B) for intent classification, or the same classifier the household uses for capture? The intent task is tiny; a smaller model would be cheaper and faster, but routing two models in the bot adds operational overhead. Tentative call: reuse the household's main classifier — one less moving part.
3. **Clarifying-question threshold.** What confidence cut-off triggers the bot to ask? Probably: ask when the top-classification probability is below 0.7 AND there is a second classification above 0.3 (i.e. genuine ambiguity, not just "nothing fits well"). Worth empirically tuning.
4. **Tombstones for forget / redact.** When the user reacts 🗑 or says "forget that," does the file get redacted with a tombstone (audit trail preserved), or fully removed? Forgejo keeps the history regardless; the question is whether the markdown file shows the redaction or is silently deleted from the visible vault. Tentative call: tombstone with a `redacted: true` frontmatter flag and an empty body — preserves the audit trail Forgejo gives for free.
5. **Cross-bot reactions.** What if scribe-bot reacts on a message the archivist filed? Reactions from known bots should be ignored (no recursive capture). Same filter the human-counter in topic rooms uses.
6. **Cost ceiling.** Each layer adds LLM calls (the mention NLU, the clarifying questions). Topic rooms already capture a lot; layering NLU on top could double the LLM cost per active household. Worth budgeting before the full three layers ship.

## A small first move

When the topic-rooms branch is merged and ready for testing, a one-day spike on layer 1 (emoji reactions) is the natural next step:

- Wire an `EventType.REACTION` handler in the archivist
- Bind 🔖 to capture the reacted message (URL if present, text otherwise) into the current topic
- Bind 🗑 to redact the reacted capture (if it's a previous bot filing)
- Manual tests in the topic room; no e2e rig changes

That alone gives the family a real-feeling per-message knob without ripping out anything. If the UX feels right, layers 2 and 3 follow. If it doesn't, the spike throws away cleanly.

## Status of this document

A design note, not a prescriptive plan. Captures the direction agreed in the 2026-06-09 session as a marker so future-Homer and future-Claude do not have to rederive the conclusion. When the first layer ships, this document graduates into a proper design doc — until then, it sits here as the canonical reference for "we know the routing is fragile; here's the direction we are taking when we get to it."

## Related

- [[topic-rooms]] — room-state-as-intent, the seed of this design
- [[knowledge-architecture]] — the broader event bus and storage layout
- [[wiki-engine]] — the deriver work the reply-chain UX will lean on for confidence signals
