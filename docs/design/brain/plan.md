# Family Brain — Implementation Plan

> Target release: 0.3.0
> Branch: `feat/brain-base` (off `main`)
> Status: planning → ready to start
> Sibling design docs in this directory:
>   - [knowledge-architecture.md](knowledge-architecture.md) — vision and end-state
>   - [knowledge-implementation.md](knowledge-implementation.md) — layered build order
>   - [knowledge-structure.md](knowledge-structure.md) — concepts, 5 layers, wiki format, backend interface (most current — supersedes earlier docs where they conflict)
>   - [ontology-design.md](ontology-design.md) — early vocabulary model
>   - [ontology-v1.md](ontology-v1.md) — pre-distillation shipping spec
>   - [engram-prototype.md](engram-prototype.md) — engram-as-backend exploration

## Goal

Add a knowledge layer to famstack that:

1. Defines a shared **ontology** (topics, types, knowledge kinds) usable by every stacklet.
2. Stores **facts** with provenance, hand-seeded at install and machine-extended over time.
3. Captures content beyond Paperless documents (URLs, voice memos, text notes) with **source-aware classification** (Matrix sender, room, DM).
4. Synthesizes an **Obsidian-compatible wiki** of entity pages (correspondents, persons, topics, stories) grown from the captured stream.
5. Answers questions in Element grounded in facts + documents + wiki, with cited sources.

Non-goals for 0.3.0:
- Active decay/supersede/promotion logic (those wait for the dream cycle in 0.4.0+).
- Matrix conversation extraction (the Deriver bot — 0.4.0+).
- Vector / semantic retrieval. Keyword + ontology expansion is enough at family scale.
- Cross-stack ontology sharing with future products. Famstack-only for now.

## Invariants we preserve

- `stacklets/docs/taxonomy.toml` stays put and continues seeding Paperless. Zero risk of regression on the working classifier and seed flow.
- `<stack_root>/ontology.toml` is the new file. Hand-authored, mirrors taxonomy.toml entries, adds ids + synonyms + keywords + cross-refs. Sync enforced by test.
- Framework code (`lib/stack/`) is product-agnostic. Ontology vocabulary, knowledge kinds with decay windows, family-specific dataclasses live at the stack-instance level, not in the framework.

## Architecture in one diagram

```
  Capture (Matrix)
     │
     ▼
  Archivist  ──── classify ──── Ontology ────  classifier prompt
     │             │              │
     │             │              ├──── ontology.toml (vocabulary)
     │             │              └──── users.toml (people + aliases)
     │             │
     │             └──── extract facts ──── FactStore
     │                                         │
     ├──── L1 mirror ────────────────────► <knowledge>/documents/
     │
     └──── eager stub + Timeline update ─► <knowledge>/{family,arthur,...}/<entity>.md
                                                  │
                                                  ▼
                                       wiki-rebuild CLI re-synthesizes
                                                  │
                                                  ▼
                                       <knowledge>/meta/index.md  (L4)
```

## Phases

Each phase is its own PR (or two small commits inside one).

### Phase 0 — Branch + scaffold

```
git checkout main
git pull
git checkout -b feat/brain-base
```

Add this plan to `docs/design/brain/plan.md` (this file) and the sibling design docs. No other code yet — Phase 0 lands the foundation, Phase 1 starts the build.

### Phase 1 — Ontology foundation

**Goal:** the `Ontology` class loads, the new `ontology.toml` mirrors taxonomy.toml with enrichment, the archivist classifier prompt uses it.

Files:
- `lib/stack/ontology.py` (new): `Ontology` class, dataclasses (`Topic`, `DocType`, `Person`, `KnowledgeKind`, `QueryPlan`), TOML loader, resolvers (`resolve_topics`, `resolve_person`, `expand_query`), `classifier_prompt_section(lang)`.
- `lib/stack/paths.py` (new or extend existing): `stack_config_dir()` helper. Verify whether the framework already exposes the stack root before adding.
- `<stack_root>/ontology.toml` (new): top-level `[types.<id>]` lookup with localized names, `[[topic]]` array with id + localized names + synonyms + keywords + types cross-refs. One entry per taxonomy.toml name. Preserves all current entries including `Architektur`/`Architecture`, `Bildung`/`Education`, `Application`, `Payment Reminder`, etc.
- `stacklets/docs/bot/archivist.py`: classify prompt switches from `json.dumps(category_tags)` to `ontology.classifier_prompt_section(self.language)`.

Tests:
- `tests/stacklets/test_ontology_taxonomy_sync.py` — every name in `stacklets/docs/taxonomy.toml` has an entry in `<stack_root>/ontology.toml` (and vice versa). Fails loudly on drift.
- `tests/stacklets/test_ontology_loader.py` — load, resolve_topics, resolve_person, expand_query happy paths.
- `tests/integration/test_archivist_e2e.py` — already exists; assert classification still produces equivalent Paperless tags.

Out of scope here:
- `seed.py` keeps reading `taxonomy.toml`. Do NOT switch its source.
- No facts, no wiki, no Q&A.

Time: ~6–8h.

### Phase 2 — Facts foundation

**Goal:** the `FactStore` class can read/write facts, the `stack facts` CLI lets you manage them by hand.

Files:
- `lib/stack/facts.py` (new): `Fact` dataclass, `FactStore` (read both toml + jsonl, write to jsonl only, query by persons/topics/kinds/story).
- `<stack_root>/facts.toml` (new, can ship empty — seeded by Phase 3).
- `<data_dir>/knowledge/facts.jsonl` — created on first append.
- `stack facts` CLI (new, in core stacklet): `list`, `add`, `edit`, `remove`, `show`. Writes to facts.toml or facts.jsonl depending on hand vs machine origin.

Tests:
- `tests/stacklets/test_facts_store.py` — append, query by various dimensions, persistence across loads.
- Round-trip a Fact through TOML and JSONL.

Out of scope:
- Decay enforcement, supersede automation. Fields exist and persist; they're not yet acted on.

Time: ~4–6h.

### Phase 3 — Stacker install interview

**Goal:** during `stack init` (or first `stack up`), Stacker interviews the user and seeds `facts.toml` + initial entity stubs.

Files:
- `stacklets/core/cli/interview.py` (new) or extend `stacklets/core/bot/stacker.py`. Decision pending: see Open Decisions.
- Question script with 6–8 prompts:
  1. Household members + ages
  2. Nicknames / aliases per person
  3. Allergies and medical rules
  4. Shared interests (cooking, hobbies, hiking, …)
  5. Open household stories (renovation, planned trip, big event)
  6. Primary contacts (doctor, school, main insurance)
  7. Anything else to remember?
- Renders answers to `<stack_root>/facts.toml` (rule facts for allergies, preference facts for interests, etc.).
- Creates entity stubs in `<knowledge>/family/` for each named person, correspondent, and story.

Tests:
- `tests/stacklets/test_interview_seed.py` — given mocked answers, asserts expected facts and entity stubs are produced.

Time: ~4–6h.

### Phase 4 — Q&A in Element

**Goal:** ask the Archivist a real question in `#documents`, get a grounded answer with citations.

Files:
- `stacklets/docs/bot/archivist.py`: new `_handle_question` branch in `_on_text`. Detects question shape (ends with `?`, wh-word prefix, or > 5 words). Pipeline: `Ontology.expand_query` → `FactStore.query` → Paperless filtered search → LLM reader → reply with citations.
- New LLM prompt: "Answer this question using only the facts and documents below. Cite by fact id and doc id. Say 'I don't know' rather than invent."
- Fallback: short/keyword queries route to the existing `_paperless_search` path.

Tests:
- `tests/stacklets/test_qa_handler.py` — unit test the routing heuristic and the prompt assembly.
- `tests/integration/test_archivist_qa_e2e.py` — file a doc, ask a question, assert citation appears in reply.

Time: ~4–6h.

### Phase 5 — Wiki rebuild CLI

**Goal:** synthesize entity wiki pages from L1 document mirrors. `stack docs wiki-rebuild` is idempotent and re-runnable.

Files:
- `stacklets/docs/cli/wiki_rebuild.py` (new): reads `<knowledge>/documents/**/*.md`, groups by correspondent / person / topic / story (via frontmatter), sends each group to LLM with a synthesis prompt, writes one entity page per group.
- `stacklets/docs/bot/archivist.py`: eager-stub creation in classification — when a new correspondent/topic/story is first seen, write a stub entity page; subsequent docs append to its Timeline.
- The Q&A retriever from Phase 4 starts reading L3 wiki pages first (small + dense), falling back to Paperless for doc details.

Tests:
- `tests/stacklets/test_wiki_rebuild.py` — given a corpus of mirror files, rebuild produces the expected entity pages.
- Idempotency test: re-running rebuild on unchanged input produces byte-identical output (after timestamp normalization).

Time: ~6–10h.

## Total estimate

~25–40h across five phases. Each phase ships independently and adds value. Cut at any phase boundary.

## Open decisions before code

1. **Branch name.** `feat/brain-base` — confirmed.
2. **Auto-derivation vs hand-authoring of `ontology.toml`.** Plan assumes hand-authored with a sync test against taxonomy.toml. If you prefer auto-derivation (taxonomy.toml is canonical, ontology.toml is generated), the test becomes the generator.
3. **Interview entry point.** `stack init` adds an interview step after users.toml? Or `stack up` first time? Or separate `stack interview` command? Proposal: `stack init`, immediately after the user list is set.
4. **Interview lives in code where.** Extend `stacklets/core/bot/stacker.py` (existing) or new `stacklets/core/cli/interview.py`? Proposal: new CLI file — the interview is install-time, not bot-driven.
5. **`stack_config_dir()` helper.** Verify whether the framework already exposes the stack root path before adding. If yes, reuse. If no, add in Phase 1.
6. **Where the example facts.toml lives.** Famstack defaults dir (so new families have a starting point) vs only in the user's stack instance. Proposal: famstack ships a tiny example as a default; the user's real one lives in their stack instance and is git-private.

## File layout summary

```
# Stack-instance config (hand-edited)
<stack_root>/
├── stack.toml
├── users.toml                  # extended with aliases per user
├── ontology.toml               # NEW — vocabulary
└── facts.toml                  # NEW — hand-authored seed

# Runtime state (machine-written)
<data_dir>/knowledge/
├── facts.jsonl                 # NEW — machine-appended
└── (future) wiki/              # synthesized entity pages

# Forgejo "knowledge" repo (canonical wiki storage; reachable from stack)
<knowledge>/
├── family/                     # shared bucket
├── arthur/, marge/, ...        # personal buckets
├── documents/YYYY/MM/...       # L1 doc mirrors (exists today)
└── meta/index.md               # L4 master index (Phase 5)

# Framework code (generic, product-agnostic)
lib/stack/
├── ontology.py                 # NEW — Ontology, Topic, DocType, etc.
├── facts.py                    # NEW — FactStore, Fact
└── paths.py                    # NEW or extended — stack_config_dir()

# Famstack-specific code
stacklets/docs/bot/archivist.py # updated classify prompt, new Q&A handler, eager stub
stacklets/docs/cli/wiki_rebuild.py  # NEW — Phase 5
stacklets/core/cli/interview.py # NEW — install-time interview (Phase 3)
stacklets/core/cli/facts.py     # NEW — `stack facts` CLI (Phase 2)
```

## Verification at each phase

| Phase | Pass criteria |
|---|---|
| 1 | Existing `test_archivist_e2e` still green. New sync test green. Classifier prompt visibly uses ontology context. |
| 2 | `stack facts add/list/edit/remove` round-trips. FactStore query returns expected results. |
| 3 | Running `stack init` on a fresh stack instance produces a populated `facts.toml` and ≥5 entity stubs. |
| 4 | In a test Matrix room, asking the Archivist a known-answer question returns the answer with a citation. |
| 5 | After running `wiki-rebuild`, `<knowledge>/family/` contains one page per correspondent with non-empty Timeline. Re-running is idempotent. |

## What we're explicitly NOT building in 0.3.0

| Capability | When | Why deferred |
|---|---|---|
| Active decay of expired facts | 0.4.0 | Needs dream cycle infrastructure; staleness is acceptable in v1 |
| Auto-supersede when newer fact contradicts older | 0.4.0 | Same |
| Promotion (event → habit after 3 occurrences) | 0.4.0 | Same |
| Matrix conversation extraction (Deriver bot) | 0.4.0 | Larger lift; needs opt-in room config + LLM compute budget |
| Vector / semantic retrieval | 0.5.0+ | Keyword + ontology expansion sufficient at family scale |
| Real-time Deriver replacing wiki-rebuild CLI | 0.5.0+ | One-shot regeneration is fine for ~500 docs/yr volume |
| Proactive morning briefings | 0.5.0+ | Needs calendar integration and notification policy |
| Cross-domain Kit Bot in `#assistant` | 0.5.0+ | After Archivist Q&A proves the pattern |

## Where to look in sibling docs

- For the **why**: [knowledge-architecture.md](knowledge-architecture.md) §Vision and §Architecture.
- For the **vocabulary shape**: [ontology-design.md](ontology-design.md) (older), refined in [ontology-v1.md](ontology-v1.md).
- For the **layered build order**: [knowledge-implementation.md](knowledge-implementation.md).
- For the **concrete shapes and templates**: [knowledge-structure.md](knowledge-structure.md) (most current).
- For **the engram-as-backend exploration**: [engram-prototype.md](engram-prototype.md).

When the docs disagree, `knowledge-structure.md` wins. It's the distillation.
