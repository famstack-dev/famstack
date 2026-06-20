# Family Email Tools — design

> Status: design draft
> Created: 2026-06-20
> Target: ingestion ships independent of the agent; drafting rides on the
>   Family Agent runtime (agent v0.4, see [plan.md](plan.md))
> Depends on:
>   - [plan.md](plan.md) — the agent runtime + tool surface that drafting needs
>   - [../brain/family-memory.md](../brain/family-memory.md) — the vault shape email files into
>   - [../brain/knowledge-architecture.md](../brain/knowledge-architecture.md) — the event bus filings ride

## Goal

Bring family email into the Family Brain and let famstack do real work with it:

1. **Ingest** family email into the vault — searchable, classified, alongside
   documents and captures.
2. **Surface email in Matrix** — family email shows up in a Matrix room, with
   the room ↔ mailbox binding configured the famstack-native way (room state,
   set by the bot on invite).
3. **Derive tasks** from email (deadlines, forms to return, payments due).
4. **Draft replies** the agent composes from vault context; a human approves
   before anything is sent.

The constraint that shapes the whole design: **wire established components,
write as little new code as possible.** Two of the three layers above already
exist in the shipped brain; only drafting is genuinely new.

## The reframe: three layers, two already exist

Email is not a new subsystem. It is:

1. **Another ingestion source.** An email is structurally a document/capture:
   a body, a subject, a sender, a date. The archivist's classify→mirror
   pipeline already turns a `SourceContent` into a vault entry. Email points at
   the pipeline we already ship — it does not get its own brain.
2. **Tasks for free.** The classifier already extracts `action_items`
   ("deadlines, payments due, forms to return" — `pipeline.py` prompt) and
   renders them in the `> [!summary]` briefing. An email that says "send the
   form back by Friday" *already* becomes an action item. The only new bit is
   rolling those up into a tasks view — the same single-walk rollup the wiki
   and grocery list use.
3. **Drafting — the new capability.** Composing a reply is a tool call by the
   **Family Agent** (the layer designed in [plan.md](plan.md), not yet built).
   Drafting is low-risk; *sending* is the one privileged outbound action, and
   it is human-in-the-loop.

This is also the pattern `IDEAS.md` keeps arriving at independently (the
Mediathek note's "build a generic `cast_to_tv` tool, one tool many callers").
Email drafting is just another caller of the agent's tool surface.

## Wire, don't build: himalaya as the email surface

Don't write IMAP/SMTP/MIME. Wire **himalaya** (`github.com/pimalaya/himalaya`),
a mature CLI email client. Verified against its docs:

- Rust binary, **Homebrew-installable** (matches our Apple-Silicon install story).
- Backends: **IMAP, SMTP**, JMAP, Maildir.
- **`--json` output** on every command — machine-readable envelopes (id,
  message-id, flags, subject, from, to, date, attachments).
- Commands we need: `envelope list` / `envelope search`, `message read <id>`
  (raw RFC 5322 or JSON), `message add -m drafts` (save a draft), `message send`.
- OAuth2 via an external token broker; TOML multi-account config.

himalaya becomes the implementation behind a `stack mail` CLI. This preserves
the framework invariant **"the `stack` CLI is the sanctioned agent interface"**:
the agent calls `stack mail …` as a tool, exactly like every other capability.
We own the glue (config rendering, the email→`SourceContent` adapter, the
approve loop); himalaya owns the protocol.

### Email → the existing ingestion contract

`SourceContent` (verified in `extractors.py`) is `text` + `title_hint` +
`source_uri`. Email maps cleanly:

| `SourceContent` | from email |
|---|---|
| `text` | message body (`himalaya message read --json` → plaintext/markdown) |
| `title_hint` | subject |
| `source_uri` | `message-id` (stable, dedupe key) |

Feed that into the existing `CapturePipeline` path (it already has
`capture_url` / `capture_text` / `capture_binary`; email is one more
`capture_*` sibling) and an email becomes a vault entry with a `> [!summary]`
briefing and `## Action items` — no new intelligence written.

## Architecture

```
  IMAP mailbox
     │  himalaya (Maildir sync)
     ▼
  `stack mail sync`  (host cron, like the memory curator)
     │  new messages → SourceContent(text=body, title_hint=subject, source_uri=message-id)
     ▼
  CapturePipeline (EXISTING)  → classify → mirror to vault → dev.famstack.event
     │
     ├─ vault entry: <sender|family>/mail/YYYY/MM/<slug>-<hash>.md  (type: email)
     │     with `## Action items` already extracted
     ├─ tasks rollup (deriver/wiki pattern) → <vault>/tasks.md
     └─ mail bot routes the filing event → the Matrix room whose
        dev.famstack.capture {kind: mailbox, account, folder} binding matches
        (sender + subject + briefing + vault link)

  Family Agent runtime (agent v0.4, restricted container)
     ├─ reads:  vault (incl. the email + related facts)        :ro
     ├─ tool:   stack mail draft / stack mail send
     └─ LLM:    local (oMLX), no cloud
     │  composes a reply → saves a draft → posts the draft into Matrix
     ▼
  Human reviews in Matrix → approves → `stack mail send <draft-id>`  (SMTP)
```

## Routing email into Matrix rooms

Family email should *appear in Matrix* — in the room the family chose for it —
not just file silently into the vault. famstack already has the exact
mechanism: topic rooms bind a room to intent via a **`dev.famstack.capture`**
room-state event carrying a `kind`, and the bot welcomes itself + bootstraps on
join (`topic_rooms.make_room_state`, `archivist._send_room_welcome_if_needed`).
The state schema already anticipates kinds beyond `topic` (the documents-room
binding, "future" kinds). Email is one more kind on the same rails.

**The binding is room state, set by the bot — not static `stack.toml`.** This
matches the topic-rooms pattern and the self-explaining-UX rule (bots configure
on invite, not via a config file the family never sees). Split of
responsibilities:

- **Credentials** (per-account IMAP/SMTP) live in config + the secret store —
  himalaya's multi-account TOML, rendered from `stack.toml` + secrets. This is
  machine config, not room state.
- **The room ↔ (account, folder) binding** lives in `dev.famstack.capture`
  room state with `kind: "mailbox"`, `{account, folder}`. Set by the mail bot
  when invited to a room.

### Bind-on-invite flow (mirrors topic rooms)

1. A family member creates a room (e.g. `#Post` or `#Schule`) and invites the
   mail bot.
2. The bot joins, **introduces itself** (existing welcome path), and checks for
   a `dev.famstack.capture` mailbox binding.
3. If unbound, it asks the one question it needs: *which account and folder
   route here?* (e.g. `family@… / INBOX`, or `family@… / Schule`). Answered by
   a short reply or a `bind <account> <folder>` command.
4. The bot writes the room-state binding. From then on, mail in that
   (account, folder) posts to that room.

One account's folders can fan out to different rooms (INBOX → `#Post`, a
"Schule" label → `#Schule`); the binding is per-room, so it composes.

### How a message reaches the room

`stack mail sync` (host cron) fetches IMAP → Maildir and files to the vault as
above, emitting a `dev.famstack.event` whose `data` carries `account` + `folder`.
The mail bot — which lives in bot-runner and *can* read room state — routes that
event to the room whose `dev.famstack.capture` binding matches, posting the
sender + subject + the `> [!summary]` briefing + a link to the vault entry.
Same path documents and captures already take to their rooms; the binding just
says *which* room. Because the room is bound, a reply in it ("draft an answer",
"remind me Friday") is scoped to that mailbox — the topic-rooms reply-chain
pattern, reused.

## What we reuse vs. build

**Reuse (no new code):**
- The classify pipeline + `action_items` extraction.
- `vault_entry` / `mirror_format` rendering and the OKF-conformant frontmatter.
- The `dev.famstack.event` bus (`build_capture_event`).
- The single-walk rollup pattern (wiki/grocery) for the tasks view.
- The **`dev.famstack.capture` room-state binding** + the bot's welcome /
  bind-on-invite path (`make_room_state`, `_send_room_welcome_if_needed`) — add
  a `kind: mailbox`, don't invent a new config surface.
- `stacklets/core/tools-server/` as the host for the agent's `mail` tool.
- himalaya for *all* email I/O.

**Build (small, additive):**
- `stacklets/mail/` — wraps himalaya as `stack mail` (sync/list/read/draft/send),
  renders himalaya's TOML config from `stack.toml` + secrets.
- A thin email→`SourceContent` adapter + a `capture_email` entry on the pipeline.
- A **mail bot** (MicroBot, same pattern as the archivist — *not* the agent
  runtime): binds rooms on invite (`kind: mailbox` room state) and routes
  filing events to the bound room.
- A tasks rollup page (needs `_index_vault` to also surface `action_items` — it
  currently captures summary/persons/topics/tags but not action items; small
  extension).
- The `draft_email` / `send_email` agent tool — on the chosen runtime, once the
  agent Phase 0 spike picks one.

## The `stack mail` contract (agent-facing)

Stable exit codes, `--json` output, idempotent — the same contract every
agent-callable command honors.

| Command | Does | Outbound? |
|---|---|---|
| `stack mail sync` | fetch new IMAP mail to Maildir, hand new messages to the classify pipeline; dedupe by `message-id` | no (read) |
| `stack mail list [--json] [--query …]` | envelope list / search | no |
| `stack mail read <id> [--json]` | message body | no |
| `stack mail draft --to … --subject … --in-reply-to <id> --body-file …` | save a draft (himalaya `message add -m drafts`); never sends | no |
| `stack mail send <draft-id>` | send a saved draft over SMTP | **YES — gated** |

## Drafting: the safe loop (no autonomous send)

1. Agent composes a reply from vault context (the thread + cited facts).
2. `stack mail draft` saves it to the Drafts folder.
3. Agent posts the draft text into Matrix for review, with citations.
4. A human edits/approves.
5. `stack mail send <draft-id>` sends it.

No autonomous outbound. This matches [plan.md](plan.md)'s posture (restricted
container, local LLM, no cloud calls) and German trust norms (a machine sending
mail unsupervised is a non-starter). SMTP credentials live in the secret store /
Keychain, not in the agent container; only the host-side `stack mail send` can
read them.

## Sequencing

- **Phase A — ingestion + Matrix surfacing (no agent runtime needed).**
  `stacklets/mail/` + `stack mail sync/list/read` + the email→pipeline adapter +
  the mail bot (bind-on-invite room state + route filings to the bound room) +
  tasks rollup. Uses the *existing* MicroBot/room-state machinery, not the agent
  runtime. Ships standalone value: family email appears in the right Matrix
  room, is searchable in the brain, and its tasks are extracted. This is the
  natural next move and sets the agent up with email context to reason over.
- **Phase B — agent runtime.** The [plan.md](plan.md) Phase 0 spike (nanobot
  vs pi), run against the **email-draft flow** as the concrete test instead of
  generic Q&A.
- **Phase C — drafting.** `draft_email` / `send_email` tools on the chosen
  runtime, plus the approve loop above.

Phase A is independent and immediately useful; Phase C is the "agent does real
work" milestone and depends on the runtime.

## Invariants / safety

- **All email protocol work goes through himalaya.** No handwritten IMAP/SMTP/MIME.
- **Local LLM** for classification and drafting. No cloud.
- **Ingestion is read-only on the mailbox by default.** Track ingested
  `message-id`s in a cache (like the mirror cache) rather than mutating server
  flags, unless the user opts into mark-as-seen.
- **Sending is the only privileged action** — human-approved, credentials
  isolated to the host, never in the agent container.
- **Never file into the vault without classification** — consistent with
  documents and captures; an email gets the same briefing + privacy bucket.
- **Idempotent sync** — re-running `stack mail sync` never double-files
  (message-id dedupe), same discipline as document reprocess.

## Open questions

1. **Account model.** One shared family mailbox, or per-person accounts?
   Per-person routes naturally to per-person buckets (like captures by sender).
2. **What to ingest.** All mail floods the vault with newsletters/receipts. A
   himalaya `envelope search` filter, an allowlist, or a cheap "is this worth
   filing?" classifier gate? Lean toward a gate so the vault stays signal.
3. **Tasks surface.** A dedicated `tasks.md` rollup, per-person task lists, or a
   Matrix `what's on my plate?` query answered live from `action_items`?
4. **Drafting context budget.** How much vault context feeds the draft LLM?
   Start minimal — the thread plus explicitly cited facts.
5. **Stacklet boundary.** New `mail` stacklet (distinct domain + credentials,
   emitting into the shared vault/event bus) vs. a source inside `docs`. Lean
   new stacklet.
6. **Bidirectional rooms?** A bound room shows incoming mail; should *posting*
   in it (or a reply-chain) be able to *send* mail too (via the human-approved
   `stack mail send`)? Natural, but it couples Phase A's bot to Phase C's
   drafting — keep read-only in Phase A, revisit once drafting exists.
7. **Binding UX.** Free-text question-and-answer on invite, a `bind <account>
   <folder>` command, or a room-name convention like topic rooms' `Thema:`
   prefix (e.g. `Post:`)? Account+folder is hard to encode in a name, so lean
   on the bot setting state from a short reply.
