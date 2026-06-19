# Family Brain Prototype: engram as Knowledge Store

> Status: Design document
> Created: 2026-04-29
> Author: Homer + Claude
> Depends on: [knowledge-architecture.md](knowledge-architecture.md), [knowledge-implementation.md](knowledge-implementation.md)
> External: https://github.com/Gentleman-Programming/engram

## Motivation

The Family Brain architecture (knowledge-architecture.md) designs a complete knowledge system
from scratch: Forgejo git repos, Markdown wiki files, pointer brain indexes, FTS via grep,
dream cycle for consolidation. It's the right end state. But it's 25-36 hours of plumbing
before the system delivers value.

The core problem is universal: ingest, store, remember, recall. Other projects have already
solved the storage + query + memory lifecycle layer. Building it from scratch when we could
be building the famstack-specific extraction and intelligence is a poor use of time.

**engram** (github.com/Gentleman-Programming/engram) is a persistent memory system for AI agents.
Single Go binary, SQLite + FTS5, zero dependencies, 17 MCP tools, HTTP API, project-scoped
storage with deduplication and conflict detection. 2,900+ stars, 64 releases, MIT licensed.

This document designs a prototype that uses engram as the knowledge store and query layer,
so we can focus on what makes famstack different: multi-source extraction from household
services, knowledge typing with decay, and proactive intelligence.


## Architecture

```
  EXTRACTION (famstack-specific, what we build)
  ─────────────────────────────────────────────
  Archivist ──→ classify docs ──→ extract facts ─┐
  Scribe ─────→ transcribe voice ──→ extract ────┤  HTTP POST
  Calendar ───→ event sync ──→ extract ──────────┤  localhost:7437
  Immich ─────→ faces + location ──→ extract ────┤  /observations
  Kit Bot ────→ "remember this" ─────────────────┘
                                                  │
  STORAGE + QUERY (engram, what we get for free)  │
  ─────────────────────────────────────────────   │
  ┌───────────────────────────────────────────────┘
  │
  v
  engram (Go binary, MCP + HTTP)
  ├── SQLite + FTS5 full-text search
  ├── Project-scoped storage (shared, homer, marge, calendar)
  ├── Topic-key upserts (evolving knowledge updates in place)
  ├── Deduplication via normalized hash
  ├── Conflict detection + judgment flow
  ├── Session lifecycle (start/end/summary)
  └── Git sync (compressed chunks for backup)
        │
        ├──→ Kit Bot (MCP tools: mem_search, mem_save, mem_context)
        ├──→ fk knowledge CLI (HTTP API wrapper)
        ├──→ Dream cycle cron (HTTP API: query, decay, promote)
        └──→ Engram TUI / obsidian-export (built-in)
```

The MCP boundary is the safety net. If engram doesn't scale to our needs, we swap
the backend. Kit Bot's tool interface doesn't change. The extraction pipeline
doesn't change. Only the storage layer moves.


## Concept Mapping

How Family Brain concepts land on engram's data model.

| Family Brain | engram | How it works |
|---|---|---|
| Knowledge domain (`shared`, `homer`, `calendar`) | **Project** | Each domain is a project. `mem_search` filters by project. `mem_context` returns per-project. |
| Knowledge type (`rule`, `fact`, `habit`, `goal`, `preference`, `context`, `event`) | **Type field** | The `type` field is a string, not an enforced enum. We use our own values. |
| Wiki entry that evolves over time | **Observation + topic_key** | `topic_key="fact/insurance-duff-insurance"` -- saving with the same key upserts the existing observation, increments `revision_count`. |
| Pointer brain / compact index | **`mem_context` per project** | Returns recent sessions + observations. The agent's map of what's known. |
| Cross-domain search | **`mem_search`** | FTS5 across all projects or filtered to one. Supports type/scope filters. |
| Short-term memory | **Session-scoped observations** | Tied to a Kit Bot conversation session. Temporal context via `mem_timeline`. |
| Long-term memory | **Topic-keyed observations** | Survive sessions. Deduplicated. Upserted on new information. |
| Conversational writes ("remember this") | **Kit Bot calls `mem_save`** | MCP tool, no custom code needed. |
| Deduplication | **`normalized_hash`** | Same fact extracted from two documents creates one entry, not two. |
| Contradiction detection | **Judge flow** | `mem_save` returns `judgment_required: true` with candidates. Agent resolves via `mem_judge`. |
| Scope (household vs personal) | **`scope: project \| personal`** | Built-in filtering on every query. |
| Decay / TTL | **Dream cycle cron (custom)** | Engram doesn't expire memories. We query by type + age, soft-delete expired. |
| Pattern promotion | **Dream cycle cron (custom)** | Query recurring events, create habit observation, supersede originals via `mem_judge`. |


## engram Data Model (relevant subset)

### Observation (the knowledge unit)

```
id              INTEGER   auto-increment
session_id      TEXT      which session created this
type            TEXT      "rule" | "fact" | "habit" | "goal" | "preference" | "context" | "event"
title           TEXT      short, searchable -- "Car insurance Duff Insurance"
content         TEXT      freeform -- structured however we want
project         TEXT      "shared" | "homer" | "marge" | "calendar"
scope           TEXT      "project" (shared within domain) | "personal"
topic_key       TEXT      canonical ID for upserts -- "fact/duff-insurance/car-insurance"
normalized_hash TEXT      deduplication fingerprint
revision_count  INTEGER   incremented on topic-key upsert
duplicate_count INTEGER   rolled-up exact duplicates
last_seen_at    TIMESTAMP updated on duplicate match
created_at      TIMESTAMP
updated_at      TIMESTAMP
deleted_at      TIMESTAMP soft-delete (used by decay)
```

### Key behaviors

**Topic-key upsert.** When `mem_save` receives a `topic_key` that already exists
within the same project + scope, it updates the existing observation instead of
creating a new one. `revision_count` increments. This is how "Duff Insurance sent a new invoice"
updates the existing insurance knowledge rather than creating a duplicate.

**Conflict detection.** When `mem_save` finds observations with similar content,
it returns `judgment_required: true` with candidates. The agent (or cron) calls
`mem_judge` with a relation: `supersedes`, `conflicts_with`, `compatible`, `related`,
`scoped`, or `not_conflict`. Judgments persist. Search results expose
`supersedes[]`, `superseded_by[]`, `conflicts[]` annotations.

**Deduplication.** Exact duplicate saves (same content + project + scope + type + title)
are rolled up: `duplicate_count` increments, `last_seen_at` updates, no new row.

**Session lifecycle.** `mem_session_start` → observations during session →
`mem_session_summary` (mandatory) → `mem_session_end`. Maps to Kit Bot conversations.


## MCP Tools (what Kit Bot gets)

Core tools available to Kit Bot via MCP:

| Tool | What Kit does with it |
|---|---|
| `mem_search` | "What do we know about insurance?" -- FTS5 query, filter by project/type/scope |
| `mem_save` | "Remember: Marge is allergic to peanuts" -- save with type=rule, topic_key |
| `mem_context` | Session start -- load recent knowledge for the current domain |
| `mem_get_observation` | Drill into a search result for full untruncated content |
| `mem_timeline` | Chronological context around an observation |
| `mem_judge` | Resolve conflicts when new knowledge contradicts existing |
| `mem_current_project` | Detect which domain/project is relevant |

Non-interactive tools used by the extraction pipeline and cron via HTTP API:

| Endpoint | Used by |
|---|---|
| `POST /observations` | Archivist, Scribe, Calendar, Immich extraction |
| `GET /search` | Dream cycle (find expired, find patterns) |
| `DELETE /observations/{id}` | Dream cycle (soft-delete decayed knowledge) |
| `PATCH /observations/{id}` | Dream cycle (promote event to habit) |
| `GET /context` | `fk knowledge` CLI |
| `GET /stats` | `fk knowledge` CLI |
| `POST /sessions` | Kit Bot session start |
| `GET /export` | Backup to Forgejo |


## Content Structure

Engram's default content structure is `What/Why/Where/Learned` (coding-oriented).
We use our own structure. The `content` field is freeform text.

### Facts (from documents)

```
Premium: EUR 340/year
Policy: KFZ-2024-XXXXX
Coverage: Vollkasko + Haftpflicht
Expires: 2026-06-30
Source: Paperless #247, Duff Insurance Rechnung 2026-03
```

### Rules (from conversations or manual)

```
Marge is allergic to peanuts.
Source: Homer via Kit Bot, 2026-04-29
```

### Events (from calendar, transient)

```
Dentist appointment for Marge
When: 2026-04-17 10:00
Where: Dr. Hibbert, Springfield
Source: Calendar sync
```

### Habits (promoted from recurring events)

```
Family pizza night every Friday
Confidence: seen 8 times in 10 weeks
Promoted from: event observations #34, #41, #48, #55, #62, #69, #76, #83
Source: Dream cycle promotion, 2026-04-29
```

Topic keys follow a `{type}/{entity}/{slug}` convention:
- `fact/duff-insurance/car-insurance`
- `rule/marge/allergy-peanuts`
- `event/calendar/dentist-marge-20260417`
- `habit/family/friday-pizza`


## Dream Cycle (Custom Cron)

Engram handles storage and query. It does not handle knowledge lifecycle.
The dream cycle is a Python cron job that uses engram's HTTP API.

**Runs nightly (e.g. 03:00).**

### 1. Decay

Query observations by type, check age against TTL:

| Type | TTL | Action when expired |
|---|---|---|
| `event` | 14 days | Soft-delete |
| `context` | 30 days | Soft-delete |
| `fact` | 90 days | Soft-delete (unless revision_count > 2 -- actively updated facts survive) |
| `preference` | 180 days | Soft-delete |
| `goal` | 365 days | Soft-delete |
| `habit` | 365 days | Soft-delete |
| `rule` | never | -- |

Soft-deleted observations remain in SQLite (recoverable). Hard-delete after 90 days
of soft-deletion to keep the DB lean.

### 2. Promotion

Query `event` observations, group by similar topic_key patterns:
- 3+ occurrences of similar events within 60 days → propose `habit`
- Create new observation with type=habit, link to source events
- Use `mem_judge` to mark source events as `superseded_by` the new habit

### 3. Conflict sweep

Query observations where `conflicts[]` is non-empty and unresolved.
Surface to Kit Bot or #assistant for human review.

### 4. Stats summary

Post to #assistant: "Knowledge: 47 facts, 12 rules, 8 habits. Decayed: 3 events,
1 context. Promoted: 1 new habit (Friday pizza). Conflicts: 0 unresolved."


## Extraction Pipeline

### Archivist (documents)

Already classifies documents with title, category, person, correspondent, date, summary.
Layer 1 from knowledge-implementation.md adds `facts[]` and `action_items[]` to the
classification response.

After classification, POST each fact to engram:

```python
async def _save_knowledge(self, classification: dict, doc_id: int):
    person = classification.get("person", "").lower()
    project = person if person in ("homer", "marge") else "shared"

    for fact in classification.get("facts", []):
        slug = slugify(fact[:60])
        correspondent = slugify(classification.get("correspondent", "unknown"))
        requests.post(f"{ENGRAM_URL}/observations", json={
            "session_id": f"archivist-{doc_id}",
            "type": "fact",
            "title": fact[:120],
            "content": f"{fact}\nSource: Paperless #{doc_id}, {classification['title']}",
            "project": project,
            "scope": "project",
            "topic_key": f"fact/{correspondent}/{slug}",
        })

    for item in classification.get("action_items", []):
        requests.post(f"{ENGRAM_URL}/observations", json={
            "session_id": f"archivist-{doc_id}",
            "type": "event",
            "title": item["action"][:120],
            "content": f"{item['action']}\nDue: {item.get('due', 'none')}\nSource: Paperless #{doc_id}",
            "project": project,
            "scope": "project",
            "topic_key": f"event/action/{slugify(item['action'][:60])}",
        })
```

### Scribe (voice messages)

After transcription, extract knowledge if the message contains actionable content.
Same pattern: POST to engram with appropriate type and topic_key.

### Calendar

Sync events periodically. Each calendar event becomes a type=event observation.
Recurring events get a stable topic_key so they upsert rather than duplicate.

### Kit Bot (conversational)

Direct MCP integration. Kit Bot calls `mem_save` when the user says "remember this"
and `mem_search` when the user asks about something. No HTTP bridge needed --
engram runs as MCP server in Kit's process.


## fk knowledge CLI

Thin wrapper over engram's HTTP API. Same progressive discovery design
from knowledge-architecture.md.

```
fk knowledge                              list subcommands
fk knowledge search <query>               GET /search?q=<query>
fk knowledge search <query> -d <domain>   GET /search?q=<query>&project=<domain>
fk knowledge search <query> -t <type>     GET /search?q=<query>&type=<type>
fk knowledge context [domain]             GET /context?project=<domain>
fk knowledge show <id>                    GET /observations/<id>
fk knowledge timeline <id>               GET /timeline?observation_id=<id>
fk knowledge stats                        GET /stats
fk knowledge save <title> <content>       POST /observations (interactive)
fk knowledge delete <id>                  DELETE /observations/<id>
```

Error messages tell the agent what to do next:

```
$ fk knowledge show
  [error] usage: fk knowledge show <id>
  Hint: run 'fk knowledge search <query>' to find observation IDs

$ fk knowledge search "insurance" -d personal
  [error] unknown domain: personal
  Available domains: shared, homer, marge, calendar
```


## What We Trade

| Original architecture | engram prototype | Assessment |
|---|---|---|
| Markdown files in git repos on Forgejo | SQLite database at ~/.engram/engram.db | Less inspectable. Mitigated by CLI, TUI, obsidian-export. |
| Browsable on Forgejo web UI | Engram TUI + CLI | Different UX, same capability. |
| `git log` / `git blame` for history | `revision_count` + `mem_timeline` | Less granular. Full git history was a nice-to-have, not a requirement. |
| Obsidian-compatible vault with wiki links | Beta obsidian-export | Weaker. If Obsidian browsing matters, revisit. |
| Custom pointer brain (index.md) | `mem_context` per project | Same concept, different format. |
| grep across repos | FTS5 full-text search | Strictly better for query. |
| Build from scratch, 25-36 hours | Use engram + build extraction, ~13-17 hours | ~50% less effort to first working prototype. |

### The honest risk

Engram is built for coding agent memory. We're repurposing it for household knowledge.
The type system, content structure, and lifecycle are flexible enough to accommodate this.
But if we hit a wall (e.g. need hierarchical knowledge, need richer metadata per observation,
need custom ranking), we're constrained by engram's schema.

The mitigation: MCP is a clean interface boundary. Extraction code talks HTTP. Kit Bot
talks MCP tools. Neither is coupled to SQLite internals. Swapping engram for a custom
store later means reimplementing the API contract, not rewriting the whole system.


## Implementation Plan

### Phase A: engram setup + smoke test (1-2h)

1. Install engram via Homebrew
2. Configure as MCP server for Claude Code (to test the tool interface)
3. Manually save a few household facts via CLI and MCP
4. Verify search, topic-key upsert, project scoping
5. Test conflict detection by saving contradictory facts

### Phase B: Archivist extraction bridge (4-6h)

1. Add `facts[]` and `action_items[]` to Archivist classification prompt (Layer 1)
2. After classification, POST extracted facts to engram via HTTP API
3. Use topic_key convention: `{type}/{correspondent}/{slug}`
4. Test: upload a document, verify facts appear in `fk knowledge search`
5. Upload a second document from the same correspondent, verify upsert

### Phase C: Kit Bot MCP wiring (2-3h)

1. Add engram to Kit Bot's MCP server config
2. Kit Bot system prompt: explain available memory tools and when to use them
3. Test: "Kit, remember Marge is allergic to peanuts" → `mem_save`
4. Test: "What do we know about insurance?" → `mem_search`
5. Test: "Kit, our insurance went up to EUR 380" → conflict detection + judgment

### Phase D: fk knowledge CLI (3-4h)

1. Implement as `stack knowledge` subcommand group
2. HTTP client wrapper with progressive discovery
3. Formatted output for terminal (table for search, detail for show)
4. Test: full CLI workflow (search, show, timeline, save, delete)

### Phase E: Dream cycle (3-4h)

1. Python script, runs via cron or `fk knowledge dream`
2. Decay: query by type + created_at, soft-delete expired
3. Promotion: group events by topic_key pattern, propose habits
4. Stats summary: post to #assistant or stdout
5. Test: seed events with old timestamps, run dream, verify decay + promotion

### Total: ~13-17 hours to a working prototype

After Phase B, documents automatically become searchable knowledge.
After Phase C, Kit Bot can remember and recall.
After Phase E, the system maintains itself.


## Relationship to Original Architecture

This prototype does not replace knowledge-architecture.md. It validates the core
concepts (knowledge types, decay, extraction, agent recall) with less plumbing.

If the prototype proves the model works, two paths forward:

1. **Stay on engram.** Extend it -- contribute upstream or fork. Add richer metadata,
   hierarchical projects, custom ranking. The Go codebase is clean and MIT-licensed.

2. **Graduate to custom store.** Take what we learned about knowledge types, topic keys,
   and extraction patterns. Build the Forgejo-backed Markdown system from the original
   architecture. The extraction pipeline and Kit Bot MCP interface carry over unchanged.

Either way, the prototype is not throwaway work. The extraction logic, knowledge typing,
dream cycle, and CLI are reusable regardless of which storage backend wins.
