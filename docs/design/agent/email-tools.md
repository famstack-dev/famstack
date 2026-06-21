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

### himalaya contract (pinned against v1.2.0, not the README)

Pinned by running himalaya 1.2.0 against a fabricated local maildir. The README
was wrong on several points, so these are the real shapes:

- **JSON flag is `-o json`** (`--output {plain,json}`), *not* `--json`.
- **Maildir backend needs `backend.maildirpp = true`** or it can't resolve INBOX.
- **`envelope list -o json`** is a *bare array* (not `{"envelopes": […]}`), and
  carries **no Message-ID**:
  ```json
  [{"id":"2","flags":[],"subject":"…",
    "from":{"name":"…","addr":"…"},"to":{"name":null,"addr":"…"},
    "date":"2026-06-20 10:00+00:00","has_attachment":false}]
  ```
  `from`/`to` are single objects with **`addr`** (not arrays, not `email`); `id`
  is a maildir sequence number, **not stable** across syncs.
- **`message read <id> -o json`** returns a single JSON *string* of the rendered
  message, body wrapped in himalaya MML (`<#part …>BODY<#/part>`). Awkward, and
  there is no `--raw`.

**Decision: ingestion does not parse himalaya's JSON at all.** himalaya's job is
IMAP↔Maildir transport (and SMTP send). The Maildir it produces is plain
**RFC822**, so the mail bot reads the message files directly with Python's
stdlib `email` module — clean Subject / From / Message-ID / Date / body, the
stable cross-sync key (Message-ID) included. himalaya's `envelope list -o json`
is used only by the human `stack mail list` CLI. This keeps the ingestion parser
on a standard (RFC822 + stdlib) instead of himalaya's rendering quirks, and it
is fully unit-testable with fabricated `.eml` strings.

## Architecture

**Runtime: a container, not a host service.** himalaya needs no host resource
(unlike `ai`'s Metal or `memory`'s git), and it is the one piece that holds
credentials and talks to *external* mail servers — exactly what the agent
plan's "restriction via container" posture is for. So the network-facing
himalaya runs in a small **`mail` container** with egress scoped to the mail
host and creds from the secret store. The classify/mirror/route logic is *not*
re-built: it reuses the existing pipeline in **bot-runner** via a mail bot
beside the archivist, with the Maildir as the handoff (a shared volume) — no
cross-container API, no duplicated pipeline. `stack mail …` wraps `docker exec`
into the container, like other stacklets' CLIs.

```
  IMAP / SMTP mailbox
     │  himalaya  — mail CONTAINER (only network-facing piece;
     │             egress scoped to the mail host, creds from secrets)
     ▼
  Maildir (shared volume)
     │  mail bot (in bot-runner, beside the archivist) reads new messages,
     │  maps each → SourceContent(text=body, title_hint=subject, source_uri=message-id)
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

`stack mail sync` (the mail container's himalaya) fetches IMAP → Maildir; the
mail bot files new messages to the vault as above, emitting a
`dev.famstack.event` whose `data` carries `account` + `folder`.
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
- A thin **email→`SourceContent` mapper** + a `capture_email` entry on the
  pipeline. Pure, fully unit-testable now — *slice A1*.
- A small **`mail` container** running himalaya (egress scoped to the mail host,
  creds from secrets, Maildir on a shared volume), plus `stack mail` wrapping
  `docker exec` (sync/list/read/draft/send) and rendering himalaya's TOML config
  from `stack.toml` + secrets — *slice A2, needs the binary + a test account*.
- A **mail bot** in bot-runner (MicroBot, beside the archivist — *not* the agent
  runtime): reads new Maildir messages, runs `capture_email`, binds rooms on
  invite (`kind: mailbox` room state) and routes filings to the bound room.
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
mail unsupervised is a non-starter). SMTP credentials live in the secret store
and are injected only into the `mail` container's env; nothing else can read
them, and the container's egress is scoped to the mail host.

## Sequencing

- **Phase A1 — the pure core (no binary, no mailbox, no rig).** The
  email→`SourceContent` mapper + `capture_email` on the pipeline, fully
  unit-tested with fabricated inputs. Proves the ADR seam (does email slot into
  `SourceContent` cleanly?) and is mergeable on its own.
- **Phase A2 — ingestion + Matrix surfacing.** The `mail` container (himalaya) +
  `stack mail sync/list/read` + the mail bot (reads Maildir → `capture_email`,
  bind-on-invite room state, routes filings to the bound room) + tasks rollup.
  Needs himalaya installed + a test account; pins the `message read --json`
  body schema against the live binary. Uses the *existing* MicroBot/room-state
  machinery, not the agent runtime. Ships standalone value: family email appears
  in the right Matrix room, searchable, with tasks extracted.
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
5. **Stacklet boundary.** RESOLVED. A `mail` container owns the network-facing
   himalaya (creds, scoped egress); the classify/file/route logic reuses the
   docs pipeline via a mail bot in bot-runner (shared Maildir handoff). Not a
   host stacklet (no host-resource need; isolation wanted), not a fork of the
   docs pipeline (reused).
6. **Bidirectional rooms?** A bound room shows incoming mail; should *posting*
   in it (or a reply-chain) be able to *send* mail too (via the human-approved
   `stack mail send`)? Natural, but it couples Phase A's bot to Phase C's
   drafting — keep read-only in Phase A, revisit once drafting exists.
7. **Binding UX.** Free-text question-and-answer on invite, a `bind <account>
   <folder>` command, or a room-name convention like topic rooms' `Thema:`
   prefix (e.g. `Post:`)? Account+folder is hard to encode in a name, so lean
   on the bot setting state from a short reply.
