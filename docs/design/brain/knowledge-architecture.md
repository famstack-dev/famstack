# The Family Brain: Knowledge Architecture

> Status: Research & design document
> Created: 2026-04-14
> Author: Arthur + Claude
> Depends on: [ai-hub.md](ai-hub.md) (Matrix as nerve center)

## Vision

famstack captures everything -- documents in Paperless, photos in Immich, conversations in Matrix, events in the calendar, code in Forgejo. But these are silos. The family server stores but doesn't *understand*.

The Family Brain connects the silos. It extracts knowledge from every service, organizes it into a structured wiki backed by git, and gives Kit Bot (and future agents) access to a growing, queryable understanding of the household. The system gets smarter over time. It becomes a family member.

## Research Sources

This design synthesizes six external sources and our existing infrastructure.

### Karpathy's LLM Wiki Pattern
Source: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

Three-layer architecture: raw sources (immutable documents the LLM reads but never modifies), the wiki (LLM-generated Markdown organized as summaries, entity pages, and interlinked concepts), and the schema (a config document defining structure, conventions, and workflows).

Three operations: **ingest** (read source, update 10-15 wiki pages, update index, append to log), **query** (search relevant pages, synthesize answer, file valuable discoveries back into wiki so explorations compound), **lint** (periodic health check for contradictions, orphans, missing cross-references, stale claims).

Critical files: `index.md` (content catalog, LLM reads this first to locate relevant pages) and `log.md` (append-only chronological record of all operations, parseable with grep/tail).

Key insight: "The tedious part of maintaining a knowledge base is not the reading or thinking -- it's the bookkeeping." LLMs eliminate the maintenance bottleneck that kills human-maintained wikis. References Vannevar Bush's 1945 Memex concept -- personal knowledge with associative trails -- but now the maintenance problem is solved.

Works well at moderate scale (~100 sources, hundreds of pages) without embedding infrastructure. Recommends Obsidian for browsing + graph view, git for version history.

### Total Recall & Ambient Intelligence Engine
Source: https://github.com/gavdalf/total-recall
Source: https://gavlahh.substack.com/p/ambient-intelligence-from-a-gut-feeling

Autonomous memory system for AI agents by Gavin Whittaker. Two major components:

**v1.x Memory Loop (5 layers of redundancy):**
Plain Markdown on disk (`observations.md`). Observer cron every 15 min reads session transcripts and appends compressed observations. Reactive watcher triggers faster on heavy activity. Pre-compaction hook captures before context window compresses. Session startup loads all saved memory. Session recovery catches missed captures. A Reflector consolidates when observations exceed ~8K words (40-60% reduction).

**v2.0 Ambient Intelligence Engine:**
Nine-component pipeline that makes the agent aware of the world *between sessions*:
1. **Sensor Sweep** -- six pluggable connectors (calendar, email, Fitbit, Todoist, file watcher, LinkedIn) emit events to an append-only JSONL event bus
2. **Event Bus** -- separates awareness from reasoning
3. **Rumination Engine** -- four-thread background reasoning (Observation, Reasoning, Memory, Planning) processes events without user prompting
4. **Inner Monologue** -- private reasoning stream, not user-visible
5. **Preconscious Buffer** -- top 5 ranked insights scored using Park et al.'s salience formula (importance weighted at 40%), injected at session start
6. **Associative Priming** -- related memories auto-activate alongside triggered memories
7. **Emergency Surfacing** -- push notifications for high-importance insights expiring within hours (max 2/day, quiet hours respected)
8. **Ambient Actions** -- read-only enrichment (11-tool whitelist, 5-action max, 60-second limit)
9. **Mid-Session Injection** -- buffer updates arrive during active conversations

**Dream Cycle** (nightly):
- Classifies observations by impact and age
- Archives past relevance threshold
- Adds semantic hooks (4-5 alternative search phrasings) so archived items remain findable
- Applies importance decay per memory type
- 7 types with TTLs: event (14d), fact (90d), preference (180d), goal (365d), habit (365d), rule (never), context (30d)
- Detects recurring patterns and proposes "promotions" for human review

**Five action types with guardrails:** ask (max 3/run), learn (importance >= 0.7, max 5), draft (>= 0.75, max 2), notify (>= 0.85, max 2/day), remind (auto-surfaces when due, max 3).

Cost: ~$2-3/month on Flash-tier models. Files on disk, no database.

### Hermes Agent Memory Architecture
Source: https://github.com/nousresearch/hermes-agent (83K stars, MIT)

**Why it exploded:** Launched 2026-02-25. Hit 84K stars by mid-April (~9,500 stars/week). Key catalysts: Anthropic blocked OpenClaw from Claude Code on April 3, Nous shipped `hermes claw migrate` same day (one-command import of settings, memories, skills, API keys). 8 releases in 6 weeks. Paradigm + a16z as backers. Shopify CEO amplified it. But the real hook: users report "it feels different after 2-3 weeks" -- the memory system actually works. People are "spooked by specific details from 3 weeks ago."

**The technical reason it works: bounded, curated memory beats "remember everything."**

Four layers of memory:

**a) MEMORY.md / USER.md** -- file-backed stores with entry delimiters.
- `MEMORY.md`: 2,200 character limit. Agent's notes about environment, conventions, lessons learned.
- `USER.md`: 1,375 character limit. User profile -- name, role, preferences, communication style.
- Entry delimiter: `\xa7` (section sign character). Entries can be multiline.
- Character limits, not token limits (model-independent).
- Single `memory` tool with actions: add, replace, remove. Replace/remove use short unique substring matching.
- **Frozen snapshot pattern**: system prompt gets a snapshot at session start via `load_from_disk()`. `format_for_system_prompt()` returns the frozen snapshot, not live state. Mid-session writes update files on disk immediately (atomic via `tempfile.mkstemp()` + `os.replace()` with `fcntl.flock()`) but do NOT change the system prompt. Tool responses show the live state so the agent sees its writes. Snapshot refreshes on next session start. This preserves LLM prefix cache for the entire session.
- Content security: all memory writes scanned for prompt injection, invisible unicode, exfiltration patterns.

**b) Session Search** -- SQLite FTS5 full-text search across all past transcripts. Schema: sessions table (metadata, token counts) + messages table (role, content, tool_calls, reasoning) + messages_fts virtual table with auto-sync triggers. WAL mode for concurrent reads. Search flow: FTS5 query (up to 50 results) > group by session (top 3-5) > truncate around matches (25% before, 75% after) > send to auxiliary LLM for focused summarization. Two modes: keyword search (with LLM cost) and recent sessions (metadata only, free).

**c) Pluggable Memory Providers** -- abstract `MemoryProvider` base class with lifecycle hooks (initialize, prefetch, sync_turn, on_session_end, on_pre_compress). `MemoryManager` orchestrates one built-in + at most one external provider. 8 plugins: Honcho, Mem0, Hindsight, Supermemory, RetainDB, ByteRover, OpenViking, Holographic. Prefetched memory wrapped in `<memory-context>` fences with system note, injected into user message at API-call time only (never persisted), preserving system prompt prefix cache.

**d) Skills as Procedural Memory** -- `~/.hermes/skills/<name>/SKILL.md` plus references, templates, scripts, assets. Agent creates/edits skills autonomously from experience. Compatible with agentskills.io open standard. Two-layer cache: in-memory LRU + disk snapshot validated by mtime/size manifest.

**System prompt assembly** (from `_build_system_prompt()`, line 3147):
1. Identity (`SOUL.md`, customizable personality)
2. Tool-aware behavioral guidance (memory guidance, session search guidance, skills guidance -- only injected when relevant tools are loaded)
3. Model-specific enforcement (GPT, Gemini need extra discipline prompts)
4. User/gateway system message
5. Frozen memory snapshot (MEMORY.md block + USER.md block with fill percentage)
6. External memory provider block
7. Skills index
8. Context files (`.hermes.md` > `AGENTS.md` > `CLAUDE.md`, each capped at 20K chars with 70% head + 20% tail truncation)
9. Timestamp + session metadata
10. Environment + platform hints

Prompt is built **once per session** and cached on `self._cached_system_prompt`. Only rebuilt after context compression events.

**Prompt caching** (`agent/prompt_caching.py`): Uses Anthropic's "system_and_3" pattern with 4 cache_control breakpoints. System prompt (stable) + last 3 messages (rolling window). 5-minute TTL.

**Nudge system**: Every 10 turns, a background agent fork reviews the conversation for memory-worthy content. Runs AFTER the response is delivered (never competes with user's task). Has max_iterations=8, writes directly to shared memory stores.

**Other relevant features:**
- Client-side tool call parsers for local models (Hermes format, Qwen, Llama, DeepSeek, Mistral, etc.) -- critical for local LLMs with inconsistent function calling
- Smart model routing (cheap model for simple messages, strong model for complex ones)
- Full Matrix integration via mautrix with E2EE support
- Context compression with pluggable engine, protects first N and last N messages
- Prompt injection scanning on all context files and memory entries

### Honcho (Plastic Labs)
Source: https://github.com/plastic-labs/honcho (AGPL-3.0)

Memory and personalization library. FastAPI server backed by PostgreSQL (pgvector) + Redis.

**Peer Paradigm:** Both humans and AI agents are first-class "Peers." Enables bidirectional modeling -- "what does Agent A know about User B" is different from "what User B knows about themselves."

**Three-stage reasoning pipeline:**
1. **Deriver** (real-time) -- extracts explicit atomic facts from conversations as they happen. Stores as vector-embedded documents in observer/observed pair collections.
2. **Dreaming** (background, after 50 docs + 60 min idle) -- deduction specialist (higher-order conclusions from explicit facts) + induction specialist (patterns across observations) + optional surprisal sampling for unusual observations.
3. **Dialectic** (query-time) -- agentic system with multiple reasoning levels (minimal to max). Pre-fetches relevant observations, uses tools to search/grep/traverse reasoning chains, synthesizes grounded answers.

**Data model:** Workspaces > Peers > Sessions > Messages, plus Collections (observer/observed pair) > Documents (with levels: explicit, deductive, inductive, contradiction) > Embeddings.

**Local LLM support:** Any OpenAI-compatible endpoint via `custom` provider. Each component (deriver, summary, dialectic, dream) can use a different model/provider. Models must support tool calling.

**Assessment for famstack:** Conceptually powerful (peer paradigm, dialectic reasoning). Too heavy for now -- AGPL license is problematic for paid features, needs PostgreSQL + pgvector + Redis + background workers, significant compute overhead. Cherry-pick the patterns, don't deploy the software.

### Manus Backend Lead's CLI Agent Insights
Source: ex-Manus backend lead (pre-Meta acquisition), 2 years building AI agents

**Core thesis:** A single `run(command="...")` tool with Unix-style commands outperforms a catalog of typed function calls. Unix's "everything is a text stream" maps perfectly to LLMs' "everything is tokens." CLI is the densest tool-use pattern in LLM training data.

**Three heuristic techniques for making CLI guide the agent:**

1. **Progressive --help discovery** (3 levels):
   - Level 0: tool description lists all commands with one-line summaries (injected at session start)
   - Level 1: calling a command with no args returns its usage
   - Level 2: calling a subcommand with missing args returns specific parameters
   - "Progressive disclosure: overview (injected) > usage (explored) > parameters (drilled down)"

2. **Error messages as navigation:**
   - Every error contains "what went wrong" AND "what to do instead"
   - `[error] cat: binary image file. Use: see photo.png` -- agent corrects in one step
   - `[error] unknown command: foo. Available: cat, ls, see, write, grep, memory, clip, ...`
   - "stderr is the information agents need most, precisely when commands fail. Never drop it."

3. **Consistent output format:**
   - Metadata footer on every result: `[exit:0 | 12ms]`
   - Exit codes (agent learns success/failure patterns)
   - Duration (agent learns cost awareness -- 12ms = cheap, 45s = expensive)

**Two-layer architecture (critical for context efficiency):**

- **Layer 1: Execution layer** -- pure Unix semantics. Pipes, chains, exit codes. Raw, lossless, metadata-free. If you truncate in this layer, `grep` only searches the first N lines.
- **Layer 2: Presentation layer** -- designed for LLM constraints. Four mechanisms:
  - Binary Guard: detect binary content, return guidance (`Use: see photo.png`)
  - Overflow Mode: output > 200 lines? Truncate, save full output to temp file, tell LLM how to explore (`cat /tmp/cmd-output/cmd-3.txt | grep <pattern>`)
  - Metadata Footer: `[exit:0 | 1.2s]` appended after execution layer completes
  - stderr Attachment: always attach stderr on failure

**Key lesson:** "Giving the agent a 'map' is far more effective than giving it the entire territory."

### LocalLLaMA Community Consensus (April 2026)
Source: r/LocalLLaMA (636K+ members), various articles and benchmarks

**Converging patterns:**
- Markdown-first knowledge bases (Karpathy pattern) for personal/small-team use
- Hybrid memory (vector + knowledge graph) for agents needing temporal reasoning
- Tiered memory (Letta/MemGPT pattern): core memory (RAM), recall (history), archival (vector disk)
- Local embedding standard: nomic-embed-text via Ollama + ChromaDB or LanceDB (embedded)
- Obsidian + local LLM is the dominant human interface pattern
- Qwen 3 14B (128K context, hybrid thinking) is the current community favorite for RAG

**Agent memory benchmarks (LoCoMo):**
- SuperLocalMemory V3 Mode C: 87.7% (needs cloud for synthesis)
- SuperLocalMemory V3 Mode A: 74.8% (fully local, zero cloud)
- Letta/MemGPT: ~83.2% (Docker + Ollama)
- Mem0: ~58-66% (primarily cloud)

**Key frameworks:**
- Letta/MemGPT (Apache 2.0): OS-inspired 3-tier memory, Docker + Ollama
- Cognee: fully local, SQLite + LanceDB + Kuzu graph, no cloud
- Hindsight (MIT): single Docker command, embedded PostgreSQL + pgvector
- OpenMemory: temporal knowledge graph with composite scoring (salience, recency, coactivation)

---

## Architecture

### Design Principles

1. **Git is the knowledge store.** Every piece of knowledge lives in a git repo on Forgejo. SHAs are immutable pointers -- a fact extracted on 2026-04-14 can be referenced forever. History, blame, and diff come free.

2. **Repos are context boundaries.** Each knowledge domain is a separate git repo. Kit Bot loads only the repos relevant to the current conversation. This is how you fit household knowledge into a 50K context window.

3. **Matrix is the event stream via custom event types.** Bots emit `dev.famstack.event` typed events in their originating rooms (not a separate #events room). Element silently ignores custom types -- no visual noise. The deriver bot joins all rooms and filters by event type. Events stay in context, near the action that produced them. No dedicated event room, no double-writes. Matrix is the stream, git is the lake.

4. **The pointer brain is the LLM's map.** A compact index (~200-500 tokens) with one-line summaries and SHA pointers. The LLM always has the map; it retrieves the territory on demand. This is the Manus "overflow" pattern applied to knowledge.

5. **Markdown over databases.** Human-readable, LLM-native, git-trackable, grep-searchable. No vector DB until scale demands it (hundreds of sources, thousands of entries).

6. **Capture is free, intelligence is paid.** The event bus and wiki structure are open source. The deriver, dream cycle, proactive surfacing, smart recall, and yearbook generation are the paid tier.

### System Overview

```
  ┌──────────────────────────────────────────────────────────┐
  |  STACKLET EVENT FACTORIES                                 |
  |                                                          |
  |  Archivist ──→ DocsEventFactory ─┐                       |
  |  Scribe ─────→ MessagesEventFactory ─┤                   |
  |  Calendar ───→ CalendarEventFactory ──┤  Sink: Matrix     |
  |  Immich ─────→ PhotosEventFactory ────┤  (dev.famstack.   |
  |  Kit Bot ────→ CoreEventFactory ──────┤   event in        |
  |  Stacker ────→ CoreEventFactory ──────┘   originating     |
  |                                           room)           |
  |  Each factory: typed events, consistent schema,           |
  |  pluggable sinks. Matrix is first sink.                   |
  |  Future sinks: JSONL file, webhook, MQTT, SQLite.         |
  └──────────────────────┬───────────────────────────────────┘
                         |
                         v
              Deriver Bot (MicroBot)
              - Joins all rooms
              - Filters dev.famstack.event
              - Batches by time/volume
              - LLM extracts knowledge
              - Commits to git
              - (Optional: opt-in NLP on
              -  flagged rooms for organic
              -  conversation extraction)
                         |
                         v
          ┌──────────────────────────────────────┐
          |     FORGEJO: knowledge/* repos        |
          |                                      |
          |  meta/     master index, ontology    |
          |  shared/   household, insurance, etc |
          |  calendar/ events, routines, patterns|
          |  arthur/   personal knowledge        |
          |  sabrina/  personal knowledge        |
          |                                      |
          |  Each repo has:                      |
          |    index.md  (pointer brain)          |
          |    *.md      (full knowledge docs)    |
          |    archive/  (decayed, still in git)  |
          └──────────────┬───────────────────────┘
                         |
              ┌──────────┼───────────┐
              |          |           |
              v          v           v
         Kit Bot    Dream Cycle   Morning
         (runtime)  (nightly)     Briefing
         reads      consolidates  posts to
         indexes    promotes      #assistant
         on demand  decays
                    rebuilds
                    indexes
```

### Git Repos as Knowledge Domains

Each domain gets its own repo on Forgejo. Repos are context boundaries -- Kit Bot mounts only what's relevant.

**Start with natural privacy boundaries, split by size later:**

| Repo | Scope | Access | Split when... |
|------|-------|--------|---------------|
| `meta` | master index, ontology schema, deriver config | system | never |
| `shared` | household, insurance, contacts, home, recipes | all family | a subtopic exceeds ~50 entries |
| `calendar` | events, routines, weekly patterns | all family | probably never |
| `arthur` | work, hobbies, preferences, private notes | Arthur only | work dominates (split to `arthur-work`) |
| `sabrina` | her world, preferences, private notes | Sabrina only | by domain as needed |

Git makes splitting cheap. `git filter-repo` extracts a subdirectory into a new repo with full history.

**Access control via Forgejo permissions.** Each family member's personal repo is readable only in their own agent context. When kids arrive, they get a repo. When they're old enough for privacy, parent read access is restricted.

### The Pointer Brain (Two-Level Index)

**Master index** (in `meta/` repo, ~200 tokens, always in Kit's system prompt):

```markdown
# Family Knowledge
# Last dream: 2026-04-14T03:00:00Z | Domains: 4 | Entries: 47

## Shared
household (12 entries): insurance, contacts, home, appliances
  [latest: f4a2b1c] shared/index.md
calendar (8 entries): upcoming events, weekly patterns, routines
  [latest: d3c1a0e] calendar/index.md

## Arthur
personal (15 entries): work/famstack, running, photography, preferences
  [latest: a7b3e2f] arthur/index.md

## Sabrina
personal (12 entries): school, dance class, preferences
  [latest: e1d4c5a] sabrina/index.md
```

**Domain index** (per repo, ~300-500 tokens, loaded on demand):

```markdown
# Shared Knowledge Index
# Updated: 2026-04-14T03:00:00Z | Entries: 12

## Insurance
- [fact] ADAC car: EUR 340/yr, expires 2026-06-30 [c3d4e5f:household/insurance.md]
- [fact] TK health: family plan, EUR 892/mo [c3d4e5f:household/insurance.md#health]

## Contacts
- [rule] Emergency pediatrician: Dr. Weber +49-89-XXX [d4e5f6g:household/contacts.md]
- [rule] Plumber: Firma Huber +49-89-XXX [d4e5f6g:household/contacts.md]

## Home
- [ctx] Bathroom renovation ongoing, Firma Bauer [g7h8i9j:household/home.md]
- [fact] WiFi: network "merles", password in contacts.md [d4e5f6g:household/home.md]
```

**Full documents** (variable size, retrieved only when Kit needs details):

```markdown
# Car Insurance - ADAC

Policy: ADAC Autoversicherung
Number: KFZ-2024-XXXXX
Coverage: Vollkasko + Haftpflicht
Premium: EUR 340/year
Payment: annual, direct debit January
Expires: 2026-06-30
Vehicle: [redacted]

## History
- 2026-03-15: Renewal notice received (Paperless #247)
- 2025-01-10: Policy started
```

**Three levels of resolution, each a choice about token budget:**

| Level | Tokens | When loaded |
|-------|--------|-------------|
| Master index | ~200 | Always in system prompt |
| Domain index | ~300-500 | On demand per conversation topic |
| Full document | variable | Only when explicitly needed |

### Context Budget

With Qwen3.5-35B-A3B at 50K context:

| Component | Tokens | When |
|-----------|--------|------|
| System prompt + personality | ~2K | Always |
| Master index | ~200 | Always |
| Relevant domain index(es) | ~500-1K | Per conversation |
| Conversation history | ~5-15K | Grows |
| Retrieved documents | ~1-4K | On demand |
| **Available for reasoning** | **~28-42K** | |

### Knowledge Ontology

Seven types with decay rates, adapted from Total Recall for household use:

| Type | Tag | Decay | Example |
|------|-----|-------|---------|
| rule | `[rule]` | never | Sabrina is allergic to peanuts |
| habit | `[habit]` | 365d | Family orders pizza on Fridays |
| goal | `[goal]` | 365d | Save for Italy trip summer 2027 |
| preference | `[pref]` | 180d | Arthur prefers dark roast coffee |
| fact | `[fact]` | 90d | Car insurance is EUR 340/year |
| context | `[ctx]` | 30d | Renovating the bathroom |
| event | `[event]` | 14d | Sabrina had dentist Apr 17 |

Decay moves entries from `index.md` to `archive/`. Git history always retains them. The dream cycle also handles promotion: an `[event]` that recurs 3+ times becomes a `[habit]`.

### Event System: Stacklet Event Factories

Each stacklet has its own event factory -- a typed, consistent interface for emitting events. The factory is decoupled from delivery: it produces events, sinks consume them. Matrix is the first sink. Future sinks (JSONL file, webhook, MQTT, SQLite) can be added without changing any stacklet code.

**Why factories, not direct Matrix writes:**
- Stacklets don't need to know about Matrix client setup, room resolution, or authentication
- Events can be routed to multiple sinks simultaneously
- Testing is trivial (assert events emitted, without Matrix)
- Schema is enforced at the factory level, not per-bot
- Future: events can flow to non-Matrix consumers (dashboards, external systems, JSONL archive)

**Architecture:**

```python
# lib/stack/events.py -- the event system

class FamstackEvent:
    """Immutable event with consistent schema."""
    source: str          # stacklet id: "docs", "messages", "photos", "ai", "core"
    type: str            # namespaced: "document.filed", "voice.transcribed", "photo.added"
    summary: str         # one-line human-readable summary
    data: dict           # structured payload (varies by type)
    timestamp: datetime  # UTC
    actor: str | None    # who/what caused it: "@arthur:merles.eu", "archivist-bot"

class EventSink(ABC):
    """Where events go. First implementation: Matrix."""
    async def emit(self, event: FamstackEvent, room_id: str | None = None): ...

class MatrixEventSink(EventSink):
    """Sends dev.famstack.event typed messages to Matrix rooms."""
    async def emit(self, event, room_id=None):
        await self.client.room_send(
            room_id=room_id or self.default_room,
            message_type="dev.famstack.event",
            content={
                "source": event.source,
                "type": event.type,
                "summary": event.summary,
                "data": event.data,
                "actor": event.actor,
                "ts": event.timestamp.isoformat(),
            }
        )

class StackletEventFactory:
    """Per-stacklet factory. Stacklets call this, never touch sinks directly."""
    def __init__(self, stacklet_id: str, sinks: list[EventSink]):
        self.stacklet_id = stacklet_id
        self.sinks = sinks

    async def emit(self, type: str, summary: str, data: dict = {},
                   actor: str = None, room_id: str = None):
        event = FamstackEvent(
            source=self.stacklet_id, type=type,
            summary=summary, data=data,
            actor=actor, timestamp=utcnow(),
        )
        for sink in self.sinks:
            await sink.emit(event, room_id)
```

**Per-stacklet event types:**

| Stacklet | Event Type | Example Summary | Data |
|----------|-----------|-----------------|------|
| docs | `document.filed` | "ADAC Rechnung 2026-03 filed" | `{paperless_id, title, correspondent, tags}` |
| docs | `document.classified` | "Classified as invoice/insurance" | `{paperless_id, category, confidence}` |
| docs | `document.searched` | "Arthur searched for 'insurance'" | `{query, result_count}` |
| messages | `voice.transcribed` | "45s voice message transcribed" | `{room, user, length_s, text_preview}` |
| messages | `conversation.summary` | "Discussion about vacation plans" | `{room, participants, topics}` |
| photos | `photo.added` | "12 photos from Munich uploaded" | `{count, faces, location, date}` |
| photos | `album.created` | "New album: Easter 2026" | `{album_name, photo_count}` |
| calendar | `event.upcoming` | "Dentist - Sabrina, Apr 17 10:00" | `{title, who, when, calendar}` |
| calendar | `event.created` | "New event: Family dinner Friday" | `{title, when, recurring}` |
| core | `service.started` | "Photos stacklet started" | `{stacklet_id, services}` |
| core | `service.error` | "Paperless health check failed" | `{stacklet_id, error, severity}` |
| ai | `model.loaded` | "Qwen3.5-35B loaded on oMLX" | `{model_id, backend, memory_gb}` |

**Matrix delivery:** Events are sent as `dev.famstack.event` typed messages in the originating room. Element and other clients silently ignore custom event types they don't render -- no visual noise for family members. The deriver bot joins all rooms and filters for this event type in its callback.

**Room capacity (researched):** A single Matrix room handles hundreds of events per day without any degradation. 200 events/day = ~73K/year. Storage: ~100-150 MB/year. No sharding needed. Every Synapse performance issue documented online is caused by federation, not message volume. A local-only server sidesteps all of it. `/sync` only sends new events since last token -- room history size is irrelevant for already-synced clients.

### Deriver

A MicroBot that joins all rooms and listens for `dev.famstack.event` typed messages.

**Pipeline:**
1. Receive events via `/sync` callback, buffer by time (15 min) or volume (20 events)
2. Load master index from `meta/`
3. Load relevant domain indexes based on event sources
4. Send event batch + indexes to Ollama with extraction prompt
5. LLM produces: updated wiki Markdown files + updated index entries
6. Commit atomically to the appropriate git repos
7. Structured commit messages: `learn: ADAC renewal notice, expires 2026-06-30`

**Opt-in conversation extraction (Layer 2, future):**
For rooms explicitly flagged via room state (`dev.famstack.deriver.config: {extract_knowledge: true}`), the deriver also listens for `RoomMessageText` and batches natural language messages for LLM extraction. DMs and unflagged rooms are never read for NLP -- only structured events are processed there. This respects privacy: organic conversations are only analyzed in rooms where the family explicitly opted in.

**Compute awareness:** The deriver queues events and processes them when Ollama isn't serving a conversation. It should not compete with Kit Bot for GPU time. On a single Mac Studio, inference scheduling matters -- the deriver yields to interactive requests.

### Dream Cycle

Nightly cron (e.g. 3:00 AM).

**Pipeline:**
1. `git log --since yesterday` across all knowledge repos -- collect today's changes
2. Promote: move confirmed observations from `observations/staging.md` into wiki files
3. Decay: entries past their TTL move to `archive/` via `git mv`, index updated
4. Detect patterns: recurring events become habits, repeated preferences get promoted
5. Lint: check for contradictions, orphan entries, stale facts
6. Squash: fold today's deriver micro-commits into one clean daily commit
7. Rebuild: regenerate all `index.md` files across all repos
8. Tag: `weekly-2026-wNN` on Sundays, `monthly-2026-MM` on 1st
9. Post summary to #assistant: "Learned 3 new facts, promoted 1 pattern, archived 2 expired events"

### Cross-Domain Queries

"Find everything about Munich" spans calendar (trip events), shared (photos), and arthur (work notes).

**Phase 1:** `fk knowledge search` runs grep across all repos the requesting user has access to. Simple, fast, no index needed.

**Phase 2 (when scale demands it):** The `meta/` repo gets a cross-reference index rebuilt by the dream cycle. Maps entities (places, people, topics) to domain + SHA pointers.

**Phase 3 (future, paid tier):** Add nomic-embed-text via Ollama for local embeddings. ChromaDB or LanceDB (embedded, no server) for vector store. Enables semantic search and document Q&A ("does our insurance cover dental?").

### CLI Interface

```
fk knowledge                              list subcommands
fk knowledge index                        print master index
fk knowledge index <domain>               print domain index
fk knowledge show <domain> <path>         cat current version
fk knowledge show <domain> <path> -r <sha>  cat version at SHA
fk knowledge search <query>               grep across accessible repos
fk knowledge search <query> -d <domain>   grep within one domain
fk knowledge log <domain> [path]          git log
fk knowledge log -d <domain> --since 7d   recent changes
fk knowledge diff -d <domain> --since 1d  what changed today
```

Progressive discovery (the Manus pattern):
```
$ fk knowledge
  knowledge index [domain]         Show the knowledge index
  knowledge show <domain> <path>   Read a knowledge document
  knowledge search <query>         Search across knowledge
  knowledge log <domain>           Show knowledge history
  knowledge diff <domain>          Show recent changes

$ fk knowledge show
  [error] usage: fk knowledge show <domain> <path> [-r <sha>]
  Available domains: shared, calendar, arthur, sabrina

$ fk knowledge show shared
  [error] usage: fk knowledge show shared <path>
  Available: household/insurance.md, household/contacts.md, household/home.md
```

Error messages always tell the agent what to do next. Never a dead end.

---

## Non-Agentic Use Cases

Not everything needs an agent loop. Some of the highest value is passive or template-driven:

**Family Search.** `fk knowledge search "dentist"` queries across knowledge repos + Paperless. Returns a unified view. CLI command, no LLM needed for the search.

**Morning Briefing.** Cron at 7:00 AM, posts to #assistant. Today's calendar events, overdue items, recently filed documents, "this day last year" photos from Immich. Template-driven with LLM adding natural language wrapper.

**Document Digest.** Weekly summary of what Archivist filed. "This week: 3 invoices, 1 insurance letter, 2 school documents." Trends over time.

**Memory Lane.** "This day in 2025" using Immich API + calendar + chat history. Display-ready for a living room screen or morning message.

**Household Wiki.** Manually curated pages (emergency contacts, WiFi, appliance warranties, medication lists) are valuable without any AI. Browsable on Forgejo's web UI.

**Smart Tags.** When Archivist files a document, the deriver extracts facts into the wiki. "ADAC sent a renewal. Policy expires 2026-06-30. Premium EUR 340/year." Kit can answer "when does our car insurance expire?" without touching Paperless.

---

## Paid Tier Mapping

| Layer | Free (open source) | Paid |
|-------|-------------------|------|
| Events | All stacklet event factories + Matrix sink | -- |
| Knowledge | Manual wiki, basic `fk knowledge` CLI, Forgejo browsing | Deriver (auto-extraction), dream cycle (consolidation + decay + patterns) |
| Intelligence | Kit Bot reads wiki on demand | Proactive surfacing, morning briefing, emergency alerts, smart recall, document Q&A, yearbook generation, memory lane |

---

## Implementation Path

### Phase 0: Forgejo repos + manual wiki (3-4 hours)
- Create `knowledge/meta`, `knowledge/shared`, `knowledge/arthur` on Forgejo
- Seed `shared/` with household contacts, insurance, key facts
- Write master index and shared domain index by hand
- Test: browse on Forgejo web UI, verify structure

### Phase 1: CLI + Kit Bot integration (4-5 hours)
- Implement `fk knowledge` subcommands (index, show, search, log)
- Wire Kit Bot to load master index into system prompt (frozen snapshot pattern from hermes-agent)
- Kit Bot can `fk knowledge index <domain>` and `fk knowledge show` mid-conversation
- Test: ask Kit about insurance, verify retrieval chain

### Phase 2: Event system (4-6 hours)
- Implement `FamstackEvent`, `EventSink`, `MatrixEventSink`, `StackletEventFactory` in `lib/stack/events.py`
- Add `DocsEventFactory` to Archivist -- emit `document.filed` after successful filing
- Add `CalendarEventFactory` to calendar service -- emit `event.upcoming`, `event.created`
- Add `MessagesEventFactory` to Scribe -- emit `voice.transcribed`
- Test: file a document, verify `dev.famstack.event` appears in #documents room (invisible in Element, visible via Matrix API)

### Phase 3: Deriver bot (6-8 hours)
- New MicroBot subclass that joins all rooms and filters for `dev.famstack.event`
- Buffers events by time (15 min) or volume (20 events)
- Loads current indexes, sends event batch + context to Ollama
- LLM extracts facts, produces updated Markdown
- Commits to appropriate git repos
- Respects Ollama availability (yield to interactive requests)
- Test: file a document, verify knowledge appears in wiki

### Phase 4: Dream cycle (6-8 hours)
- Nightly cron processing daily changes
- Promotion, decay, pattern detection, lint
- Commit squashing, index rebuild, tagging
- Summary posted to #assistant
- Test: seed some events over a few days, verify dream output

### Phase 5: Proactive intelligence (4-6 hours)
- Morning briefing cron posting to #assistant
- Emergency surfacing for time-sensitive items (importance >= 0.85)
- "This day last year" from Immich API
- Quiet hours (22:00-07:00)

### Phase 6: Semantic search (8-10 hours, paid tier)
- nomic-embed-text via Ollama for local embeddings
- LanceDB (embedded, no server) for vector store
- Embed wiki pages + Paperless document summaries
- `fk knowledge search` becomes hybrid: keyword + semantic
- Document Q&A: "does our insurance cover dental?"

### Phase 7: Yearbook (paid tier, future)
- Dream cycle pattern detection + Immich photos + calendar events
- Rendered into annual/monthly summaries
- "2026 in review: 3,400 photos, 127 documents, 14 family patterns detected"

---

## Patterns Adapted from Hermes Agent

Hermes-agent's memory system is the most proven in production (83K stars, users report meaningful behavior change after 2-3 weeks). These patterns are directly transferable to the Family Brain:

| Hermes Pattern | How It Works | Famstack Adaptation |
|---------------|-------------|-------------------|
| Frozen snapshot | MEMORY.md loaded once at session start, never changes mid-session. Preserves prefix cache. | `index.md` loaded into Kit Bot system prompt at session start. Knowledge writes go to git but don't update Kit's prompt until next session. |
| Bounded curated memory | 2,200 + 1,375 chars total (~900 tokens). Constraint forces consolidation. | Master index ~200 tokens + domain index ~500 tokens. Same principle: bounded context forces the system to keep only what matters. |
| Single memory tool | `memory` tool with add/replace/remove actions. Schema description IS the behavioral prompt. | `fk knowledge` with show/search/log. Progressive discovery via --help. The CLI is the tool interface. |
| Entry delimiters + substring matching | `\xa7` section sign separates entries. Replace/remove use unique substring, not IDs. | Markdown headers + ontology tags `[fact]`, `[rule]`, etc. Git SHAs for versioned references. |
| Nudge system | Background agent fork every 10 turns reviews conversation for memory-worthy content. Runs AFTER response delivered. | Deriver bot processes events every 15 min. Same idea (background extraction), different trigger (events vs turn count). |
| FTS5 session search | SQLite full-text search across all past transcripts. Truncate around matches, summarize with cheap model. | `fk knowledge search` across git repos (grep-based). Add SQLite FTS for Matrix history search later. |
| Atomic file writes | `tempfile.mkstemp()` + `os.replace()` prevents corruption | Git commit (atomic by nature). Even better: full history + rollback. |
| Prompt injection scanning | All context files scanned for injection, invisible unicode, exfiltration | Scan knowledge entries before injecting into Kit's prompt. Especially important for entries derived from user conversations. |
| Tool-aware guidance injection | Memory/session/skill guidance only injected when those tools are loaded | Kit Bot guidance varies by available knowledge domains. If no calendar repo exists, skip calendar-related prompting. |
| System prompt caching | Built once per session, cached on instance, only rebuilt after compression | Kit Bot loads index once. `fk knowledge` calls are mid-conversation tool use, not system prompt changes. |

**Patterns NOT transferred (cloud assumptions):**
- Anthropic `cache_control` breakpoints (no equivalent in local Ollama/oMLX API, but MLX has its own prompt caching)
- Auxiliary LLM for session summarization (at 60 tok/s, too expensive -- return raw snippets instead)
- External memory providers (Honcho, Mem0 -- cloud services)
- Multi-platform gateway (famstack is Matrix-only, which is simpler)

## Open Design Questions

**1. Conversational writes.** Should family members be able to update the wiki via Matrix? ("Kit, remember that I'm allergic to peanuts" -> Kit commits `[rule]` to personal repo.) This is the natural UX for non-technical family members. Needs confirmation flow: "I'll remember that Sabrina is allergic to peanuts. Correct?"

**2. Conflict resolution.** What happens when the deriver extracts a fact that contradicts an existing entry? Options: flag for human review (safe but requires attention), newer-wins with changelog (automated but lossy), keep both with `[contradiction]` tag and let the dream cycle resolve.

**3. Immich connector depth.** Immich has face detection, object recognition, location, and EXIF data. How much to emit via `PhotosEventFactory`? Full metadata is noisy. Probably: faces + location + date, skip object detection unless it detects something notable.

**4. Multi-model routing.** The deriver and dream cycle don't need the big model. They could run on a smaller model (llama3.1:8b or Qwen 9B) to avoid competing with Kit Bot for the 35B model's GPU time. The fk CLI could add `--model` flags, or oMLX could queue requests.

**5. Knowledge repo visibility.** The wiki structure and tooling should be open source (part of famstack). The actual family data is private (Forgejo, never pushed upstream). This mirrors how `stack.toml` works: schema is public, values are private.

**6. Event sink strategy.** Matrix is the first sink, but the factory pattern supports multiple sinks. When (if ever) to add: JSONL file sink (for grep-based debugging and offline replay), SQLite sink (for structured queries and FTS), webhook sink (for future integrations). Probably: start Matrix-only, add JSONL when debugging the deriver, add SQLite when we need cross-event queries that Matrix API handles poorly.

**7. Opt-in conversation extraction.** The deriver can optionally listen for natural language in flagged rooms (via `dev.famstack.deriver.config` room state). This captures organic knowledge ("the dentist moved to Thursday") but costs LLM compute. Worth the cost for #assistant (where family talks to Kit)? Probably not for #server (ops noise). Let family members flag rooms via Kit: "Kit, start learning from this room."

**8. Obsidian-compatible format.** Knowledge repos use YAML frontmatter (type, domain, tags, created, source, decay) and `[[wiki links]]` for cross-references. This makes repos openable as Obsidian vaults for free -- graph view, backlinks, Dataview queries, all without building any UI. Obsidian is an optional viewer, not a dependency. Forgejo web UI + Kit Bot in Matrix + fk CLI are the primary interfaces.

**9. Open-source self-hosted Obsidian alternative.** TODO: Evaluate whether a self-hosted open-source knowledge UI could be added as a stacklet. Candidates to check: Silverbullet (Markdown notebook, web UI, Lua plugins), Wiki.js (Markdown wiki with git storage backend, Docker, search, graph view), Trilium (hierarchical notes, API, Docker), Logseq (open source, graph view, Markdown). Key requirement: must support git-backed storage or be able to read/write from a git repo, so bots and humans share the same data. Low priority -- the git repos + Obsidian-as-optional-viewer approach works today.

---

## Core Insight

The entire system boils down to one operation: **ontological tagging for fast queries.**

Repos, Markdown files, git SHAs, wiki links, event factories -- all plumbing. The intelligence is in the classification step where the LLM takes raw input and produces:

```
raw input → [type, domain, tags, summary, relationships]
```

Get the tagging right and grep does the rest. The ontology (7 knowledge types with decay rates, domain routing, entity cross-references) is the product. Everything else is infrastructure to feed input into the tagger and make tagged output searchable.
