# Family Agent — Implementation Plan

> Target release: 0.4.0 (post-brain)
> Branch: TBD (off `main`, after `feat/brain-base` lands)
> Status: Phase 0 (framework spike) — pending
> Sibling design docs:
>   - [../brain/plan.md](../brain/plan.md) — the knowledge layer underneath
>   - [../brain/family-memory.md](../brain/family-memory.md) — the vault shape the agent reads
>   - [../brain/knowledge-architecture.md](../brain/knowledge-architecture.md) — long-term vision (the "Kit Bot" seat the agent occupies)

## Goal

Add an interactive intelligence layer on top of the Family Brain that:

1. Answers questions in Matrix grounded in the vault, with source citations.
2. Curates a shared grocery list from natural-language chat ("we're out of milk").
3. Posts scheduled reminders into family rooms.
4. Grows over time into autonomous-task / proactive / IoT territory (long-term).

The agent is **not** the deriver. The deriver (brain 0.4.0+) is a non-interactive event-batching extractor running on cron. The agent is conversational, RPC-bound to a Matrix room. They share the vault, not the loop.

### Short-term scope (v1, this plan)

- Grounded Q&A in `#family` / `#assistant` with citations to vault paths.
- Grocery list curator, vault-native by default (add / remove / show / categorize via chat).
- Reminders posted to Matrix on a cron.
- Read-only over `family/memory.git`. Read-write over a dedicated `family/agent-notes.git` repo.

### Per-instance extensions (not v1 product, but v1 architecture)

The famstack-product ships with self-contained defaults. Individual instances customize via pluggable backends:

- **Grocery backend.** Default backend stores the list in `family/memory/lists/groceries.md`; the "phone access" path is "ask the bot in Matrix." Optional Trello backend (Homer's personal stack) bridges to an existing Trello board via REST API; family workflow unchanged, agent gains read/write through the same `GroceryStore` interface.
- **Pattern.** Same shape as `taxonomy.toml` (default seed, instance overrides), `ontology.toml` (seed, evolved per instance), and the brain invariant "memory is instance data, not product policy." The agent defines the protocol; instances pick the backend.

The v1 build ships the vault backend. The Trello backend ships in the same repo but disabled by default. Other instances (deskstack, future families) get to pick.

### Long-term (not in v1, captured for direction)

- Autonomous tasks (add to grocery list, turn on internet radio, control lights via Home Assistant MCP).
- Proactive messages (reminders, occasional jokes, "this day last year" surfacing).
- Observation of household happenings (what was filed, what's overdue, who asked what).
- Cross-session personality (`SOUL.md`-style self-curated voice).

These are out of scope for v1 deliberately. We do not know what will feel natural until v1 ships and the family uses it for weeks.

## Invariants

- **The agent never mutates `family/memory`.** Vault is read-only. Auto-extend rule applies: no pending/review queues for the family. Agent observations land in `family/agent-notes.git`, the deriver/dream cycle (later) handles promotion via patterns.
- **Forgejo is the canonical source for both repos.** Vault read surface stays at `<data_dir>/memory/vault/` (existing). Agent notes read-write surface at `<data_dir>/agent/notes/`.
- **Restriction via container, not by trusting the agent.** Docker stacklet, no host FS mount. Network access limited to the stack network plus `host.docker.internal:42001` (famstack-api) and the LLM endpoint. Built-in `bash` / `write` if available are scoped to `/workspace/notes` only via the mount layout, not via prompt instructions.
- **Local LLM by default.** oMLX or LM Studio via the existing `ai` stacklet's OpenAI-compatible endpoint. No outbound cloud calls in the default config. Optional fallback provider configurable per-stacklet, never on by default.
- **Citations are mandatory.** Every grounded answer cites at least one vault path. "I don't know" beats invention.
- **Latency target written down on day one.** Median <8s, p95 <20s on local Qwen for a typical Q&A turn in `#family`. Miss this and the project is dead regardless of feature set.
- **`<data_dir>/agent/notes/` admin-readable only by default in Forgejo.** Agent observations about family members are sensitive. Loosen later when the contents have proven boring.

## Architecture in one diagram

```
  Matrix room (#family or #assistant)
     │  user message
     ▼
  agent-bot (Python MicroBot, runs inside bot-runner)
     │  RPC / SDK call (depends on framework chosen in Phase 0)
     ▼
  Agent runtime (Docker stacklet, restricted)
     ├─ reads:  /workspace/memory      ← <data_dir>/memory/vault/      :ro
     ├─ writes: /workspace/notes       ← <data_dir>/agent/notes/       :rw
     ├─ tools:  vault_search, grocery_*, stack_* (read-only)
     └─ LLM:    http://host.docker.internal:42060  (oMLX, OpenAI-compatible)
     │
     │  reply with citations
     ▼
  agent-bot posts to room  (m.room.message + dev.famstack.event envelope)


  Reminder scheduler (host cron stacklet, separate from agent runtime)
     ├─ reads:  family/memory/reminders.toml   (vault file, hand-edited or agent-proposed)
     └─ posts:  to Matrix rooms on schedule


  Forgejo (truth)
     family/memory.git          ← existing vault (brain 0.3.0)
     family/agent-notes.git     ← NEW, agent's working memory
        ├── observations/YYYY/MM/
        ├── people/<localpart>.md
        ├── sessions/YYYY-MM-DD.md
        ├── AGENT.md             ~2K char rules the agent learned
        └── SOUL.md              ~4K char personality (deferred; v2)
```

## Framework decision

Decided through Phase 0 (spike). Candidates evaluated:

| | Pi (earendil-works) | Nanobot (HKUDS) | Hermes (NousResearch) |
|---|---|---|---|
| Language | Node 18+ | Python 3.11+ | Python 3.11+ |
| Matrix native | Via plugin (pi-messenger-bridge) | Native channel | Unclear from README |
| Multi-user | Neutral | Yes (channel-first) | Single-user by design |
| Plugin/package ecosystem | `pi install npm:X`, composable | Channels + skills + MCP | Monolithic distribution |
| Local LLM (Qwen) | Validated by Homer in coding mode | Multi-provider, includes Ollama/LM Studio/vLLM | Custom client-side parsers for Qwen/Hermes/Llama/Mistral |
| MCP | Yes | Yes | Yes |
| Cron / scheduled tasks | Plugin if it exists | Built-in | Built-in |
| Memory/personality patterns | Build ourselves | Dream memory built-in | SOUL/MEMORY/USER built-in |
| Footprint | Mid (Node container) | Light (intentional small core) | Heavy (224MB repo) |
| Already used by Homer | Yes (coding agent, Qwen 3.6) | Yes (prior work) | No |

**Hermes deprioritized** because (a) single-user shape fights a family-Matrix-room context, (b) heavy install for what we'd actually use, (c) the brain doc already flagged its cloud-tilted patterns as non-portable. Keep it as a pattern reference (SOUL.md, frozen snapshot, FTS5 session search), not a runtime.

**Pi and Nanobot both go to spike.** Homer has used both. The pi plugin ecosystem makes long-term composition attractive; nanobot's native Matrix and Python ergonomics make short-term shipping attractive. Spike data picks.

## Phases

### Phase 0 — Framework spike (2-3 days)

**Goal:** get a feel for pi and nanobot against our actual stack. Decide which framework v1 builds on.

Two time-boxed branches, throwaway code, side-by-side comparison:

- `spike/agent-pi` — pi in Docker, RPC mode, talking to oMLX. Custom tool stubs for `vault_search` (no real implementation, returns canned data). Connect to Matrix via pi-messenger-bridge plugin. Send 20 test prompts, measure latency, qualify reply quality.
- `spike/agent-nanobot` — nanobot in Docker, Matrix channel native, talking to oMLX. Same tool stubs. Same 20 prompts. Same measurements.

Evaluation criteria (write down before spiking, score after):

1. **Latency on local Qwen** — median + p95 for a 200-token Q&A turn.
2. **Tool-call reliability** — fraction of 20 prompts that produce the right tool call without retry.
3. **Matrix integration friction** — how many config steps from zero to "bot responds in test room with E2EE."
4. **Multi-user clarity** — what happens when two family members send messages in parallel.
5. **Restart resilience** — does the bot recover cleanly from container restart, or does it lose state / drop messages.
6. **Extension surface** — how clean does adding a real `vault_search` tool feel.
7. **Plugin/composability** — for pi, browse the actual plugin ecosystem; for nanobot, evaluate channel + skill + MCP composition.

Spike output: a one-page memo per framework. Decision committed at the end of Phase 0.

Time: ~1.5 days per spike, ~0.5 day to write up.

### Phase 1 — Agent stacklet skeleton + Q&A (3-4 days)

**Goal:** the chosen framework runs as `stacklets/agent/`, answers questions grounded in the vault from `#assistant`, cites sources.

Files (framework-agnostic shape, fills in concretely after Phase 0):

- `stacklets/agent/stacklet.toml` — declares the stacklet, mounts, requires `[memory, ai, messages]`.
- `stacklets/agent/docker-compose.yml` — agent runtime container, restricted mounts and network.
- `stacklets/agent/hooks/on_install_success.py` — creates `family/agent-notes.git` in Forgejo, clones to `<data_dir>/agent/notes/`, seeds `AGENT.md`.
- `stacklets/agent/bot/bot.toml` — declares `agent-bot` for the bot-runner.
- `stacklets/agent/bot/agent.py` — MicroBot subclass that listens to `#assistant`, forwards to the agent runtime, posts replies with citations.
- `stacklets/core/tools-server/server.py` (EXTENDED) — adds `/tools/vault/search` endpoint over the existing markdown vault: frontmatter filter + ripgrep over body, returns matched paths + snippets.

Tests:

- `tests/stacklets/test_vault_search.py` — search returns expected paths for known fixtures.
- `tests/integration/test_agent_qa_e2e.py` — file a doc, ask a question, assert citation appears in the reply.

Verification: ask 10 known-answer questions in a test room. ≥8 produce grounded replies with valid citations within latency target.

Time: ~3-4 days.

### Phase 2 — Grocery list curator (1-2 days)

**Goal:** the family can add and remove items via chat. Bot returns the current list when asked, smartly categorized. List is fully self-contained in the vault; no external dependency in the default install.

Shape:

- List lives at `family/memory/lists/groceries.md`.
- Tools: `grocery_add(items)`, `grocery_remove(items)`, `grocery_show()`, `grocery_categorize()`.
- Interaction is pull, not push: someone in chat types "add milk and bread" or "what's on the list?" The agent never silently mutates; every change is in response to a request.
- LLM intent-parsing layer turns "we're out of milk and bread" into structured tool calls.
- "Available when not at home" = open Matrix on phone, ask the bot.

`GroceryStore` protocol defined in the agent runtime so a Trello backend (or any other) can swap in without changing tools or prompts. Vault backend is the default and ships enabled.

Tests:

- `tests/stacklets/test_grocery_parser.py` — natural-language inputs produce expected tool calls.
- `tests/integration/test_grocery_e2e.py` — chat round-trip adds and removes items against the vault backend.
- `tests/stacklets/test_grocery_store_protocol.py` — both backends implement the same protocol contract.

Verification: the family adds items across a week, asks for the list at the store, uses it, finishes the trip without abandoning the bot.

Time: ~1-2 days for the vault backend. Trello backend is additive, lives behind an instance config flag, not on the v1 critical path.

### Phase 3 — Reminders (1 day)

**Goal:** scheduled messages post to Matrix rooms on time.

Independent of the agent runtime. A tiny new mechanism, not bundled with the framework choice:

- `<vault>/reminders.toml` — hand-edited or agent-proposed (later) reminder entries.
- `stacklets/agent/cron/reminders.py` — host-side cron job scans the file, posts due reminders.

Tests:

- `tests/stacklets/test_reminders_due.py` — given a fixture file and a clock, the right entries fire.

Time: ~1 day.

### Phase 4+ — Long-term, deferred

Captured for direction, not committed:

- Cross-session personality (`SOUL.md` self-edit pattern).
- Autonomous tool use (mutate grocery list without explicit ask, propose reminders).
- Home Assistant MCP integration (lights, internet radio).
- Proactive surfacing ("policy expires next week").
- Observation feedback loop (agent reads its own session digests to improve).
- Yearbook generation (paid tier mapping from brain doc).

These get their own design docs once v1 is real and we know what feels natural.

## Open decisions before code

1. **Grocery UX (gates Phase 2).** ANSWERED. Default backend is vault-native (`family/memory/lists/groceries.md`); access path is "ask the bot in Matrix." Homer's family uses Trello today; the Trello backend ships in the same repo behind an instance config flag, not enabled by default. Pluggable `GroceryStore` protocol keeps both honest.
2. **Framework (gates Phase 1).** Pi vs nanobot. Spike outcome decides.
3. **Agent rooms.** Single `#assistant` room or per-family-member DM? My instinct: ship `#assistant` first, add DMs only if a family member explicitly asks. Shared room means shared context which is mostly a feature.
4. **Citation format.** Inline `[doc.md]` link to Forgejo web URL, or human-readable "From 2026-03 Duff Insurance filing"? Decide during Phase 1 spike with real reply samples.
5. **Agent identity.** One Matrix user (`@agent:home`) for everything, or separate identities for `agent-bot`, `grocery-bot`, `reminder-bot`? My instinct: one identity. The agent is a person, not a service catalog.
6. **What goes in seed `AGENT.md`?** One paragraph of behavioral rules: "be useful, be honest, don't invent, cite sources, ask for clarification once before guessing." Refine through use.

## File layout summary

```
stacklets/agent/                 # NEW stacklet
├── stacklet.toml                # mounts, requires [memory, ai, messages]
├── docker-compose.yml           # restricted runtime container
├── hooks/
│   └── on_install_success.py    # create agent-notes repo, clone, seed AGENT.md
├── bot/
│   ├── bot.toml                 # agent-bot declaration
│   └── agent.py                 # MicroBot subclass, Matrix ↔ runtime bridge
├── cron/
│   └── reminders.py             # host-side cron, posts due reminders
└── runtime/                     # whatever the chosen framework needs (pi config / nanobot config)

stacklets/core/tools-server/
└── server.py                    # EXTENDED with /tools/vault/search

# Forgejo (truth)
family/memory.git                # existing vault, read-only for agent
family/agent-notes.git           # NEW
├── AGENT.md                     # ~2K char rules
├── SOUL.md                      # ~4K char personality (Phase 4+, deferred)
├── observations/YYYY/MM/        # free-form notes
├── people/<localpart>.md        # per-family-member observations
└── sessions/YYYY-MM-DD-HHMM.md  # session digests

# Local working copies
<data_dir>/memory/vault/         # existing
<data_dir>/agent/notes/          # NEW, agent's read-write area
```

## Verification at each phase

| Phase | Pass criteria |
|---|---|
| 0 | Two one-page spike memos. Decision committed in writing. |
| 1 | 10 known-answer questions: ≥8 grounded replies with valid citations within latency target. |
| 2 | Wife completes one real shopping trip without abandoning the bot. |
| 3 | A reminder set for "tomorrow 09:00" posts to the right room at the right time, on a fresh container restart. |

## What we're explicitly NOT building in v1

| Capability | When | Why deferred |
|---|---|---|
| Autonomous task initiation | v2+ | Need real usage to know what feels natural |
| SOUL.md self-edited personality | v2+ | Hermes pattern, valuable, but unproven for family multi-user |
| Proactive messages (jokes, surfacing) | v2+ | Same — depends on knowing tone the family wants |
| Home Assistant MCP (lights, radio) | v2+ | Requires Home Assistant adoption + MCP server work |
| Cross-session learning loop | v2+ | Wait for deriver / dream cycle from brain 0.4.0+ |
| Skills system | v2+ | Premature; tools are enough for v1 |
| Multi-model routing | v2+ | One local model is the v1 default; routing is optimization |
| Voice input/output | v2+ | Already covered by Scribe (transcribe) and the `ai` speech stack |

## Where to look in sibling docs

- For the **vault shape** the agent reads: [../brain/family-memory.md](../brain/family-memory.md).
- For the **long-term Kit-Bot seat** the agent occupies: [../brain/knowledge-architecture.md](../brain/knowledge-architecture.md) §System Overview.
- For the **knowledge-types and decay model** (the deriver's job, not the agent's): [../brain/knowledge-architecture.md](../brain/knowledge-architecture.md) §Knowledge Ontology.
- For the **event bus** the agent will eventually emit into: [../brain/family-memory.md](../brain/family-memory.md) §Matrix as ledger.

## Precedent: archivist's question-mode loop (in production)

The archivist bot already runs a tightly-scoped agent loop for
question-mode chat searches. It is **not** the Family Agent and is
not meant to be the seed of one, but the shape it has converged on
is a useful precedent for the framework decision:

1. Rewrite the question into search keywords (LLM call #1).
2. Search both backends (memory vault + Paperless).
3. Synthesize an answer from the result summaries (LLM call #2).
4. If the answer pattern-matches a deferral ("I'd need to read [N]
   in detail"), expand the cited rows to full document text and
   re-synthesize (LLM call #3).
5. Render the final answer plus only the cited rows, numbered to
   match the brackets.

Wiring lives in `stacklets/docs/bot/archivist.py::_handle_search`
and `stacklets/docs/bot/nl_query.py`. Tool catalog, decision rule,
and turn budget are all hardcoded -- there is no generic tool-call
loop, on purpose. Two reasons to leave it as-is for v0.3:

- It works in production, the family uses it, and pulling it into a
  shared framework before the Family Agent has any code would cost
  more than it saves.
- The decision and tool surfaces are archivist-specific. Generalising
  them across bots without a second bot to inform the abstraction is
  the exact "premature framework" failure mode this plan's v1 scope
  notes call out.

**Signal that it's time to absorb this into the Family Agent**: the
moment we want either (a) a second tool from a different bot domain
(e.g. archivist answer wants to fold in a calendar lookup), or (b) a
second decision branch besides "did I defer?" (e.g. "should I ask
the user a clarifying question?"). Until then, growing
`nl_query.py` is cheaper than refactoring it.
