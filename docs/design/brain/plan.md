# Family Brain — Implementation Plan

> Target release: 0.3.0
> Branch: `feat/brain-base` (off `main`)
> Status: Phase 0 done → Phase 1 next
> Sibling design docs in this directory:
>   - [knowledge-architecture.md](knowledge-architecture.md) — vision and end-state
>   - [knowledge-implementation.md](knowledge-implementation.md) — layered build order
>   - [knowledge-structure.md](knowledge-structure.md) — concepts, 5 layers, wiki format, backend interface (most current — supersedes earlier docs where they conflict)
>   - [ontology-design.md](ontology-design.md) — early vocabulary model
>   - [ontology-v1.md](ontology-v1.md) — pre-distillation shipping spec
>   - [engram-prototype.md](engram-prototype.md) — engram-as-backend exploration

## Goal

Add a memory layer to famstack that:

1. Defines a shared **ontology** (topics, types, knowledge kinds) reachable from every stacklet.
2. Stores **facts** with provenance — hand-seeded at install via interview, machine-extended over time.
3. Captures content beyond Paperless documents (URLs, voice memos, text notes) with **source-aware classification** (Matrix sender, room, DM).
4. Synthesizes an **Obsidian-compatible wiki** of entity pages (correspondents, persons, topics, stories) grown from the captured stream.
5. Answers questions in Element grounded in facts + documents + wiki, with cited sources.

The "Family Brain" is the system as a whole — LLM + bots + capture + this new memory layer. What we are building in 0.3.0 is the **memory** part. It lives in its own stacklet.

Non-goals for 0.3.0:

- Active decay / supersede / promotion logic (wait for dream cycle in 0.4.0+).
- Matrix conversation extraction beyond what Archivist already emits (Deriver bot — 0.4.0+).
- Vector / semantic retrieval. Keyword + ontology expansion is enough at family scale.
- Cross-product ontology sharing (famstack vs deskstack via published artifact). Same code path, different seeds.

## Invariants

- **`stacklets/docs/taxonomy.toml` stays put** and keeps seeding Paperless tags. Zero regression risk on the working classifier.
- **Memory is instance data**, not product policy. It lives in **Forgejo** as a `memory` repo. Famstack upgrades never overwrite it.
- **Famstack-product ships seeds** under `stacklets/memory/seeds/`. On first install the stacklet creates the Forgejo repo and pushes seeds. From then on the instance owns it. Hand edits via the Forgejo web UI (or a local Obsidian clone) are first-class and appear in the commit log.
- **Framework code (`lib/stack/`) stays generic.** Ontology dataclasses, `FactStore` protocol, decay-window field schema — yes. Family vocabulary, household roles, "Recipe is also Memory" cross-refs — no, those live in famstack-product seeds.
- **Forgejo is the only source of truth.** No working-copy contract under `<data_dir>/memory/`. Every reader (Archivist, Stacker, CLI) goes through `ForgejoClient` (same path Archivist already uses for document mirrors) and caches in-process. A service could be added later — the stacklet declares `type = "host"` for now.
- **Why Forgejo:** the commit log is the learning history. Reverts are free. External editability for free. One client (`lib/stack/forgejo.py`) is already battle-tested by the document mirror.

## Architecture in one diagram

```
  Capture (Matrix, future: web, voice)
     │
     ▼
  Archivist  ── classify ──► memory.get_ontology(lang)
     │                            │
     │                            └─ ForgejoClient.get_file("memory", "ontology.toml")
     │                               cached in-process; refresh on bot restart
     │                                                           │
     │                                                           ▼
     │                                                  Forgejo: <owner>/memory.git
     │                                                  ├── ontology.toml
     │                                                  ├── facts.toml
     │                                                  ├── facts.jsonl
     │                                                  ├── wiki/{family,arthur,...}/*.md
     │                                                  └── meta/index.md     (Phase 5)
     │
     ├── L1 mirror ──► Forgejo: <owner>/documents.git/YYYY/MM/*.md   (exists today)
     │
     └── emit fact ──► memory.FactStore  ──► ForgejoClient.put_file → memory.git


  stacklets/memory/  (new host-type stacklet, no container, no bot in v1)
     ├── seeds/         version-controlled, scenario-specific (family today, office later)
     ├── hooks/         on_install_success → create repo + push seeds (idempotent)
     ├── lib.py         in-process API over ForgejoClient: get_ontology(), FactStore, query_plan() (Phase 4)
     └── cli/           stack facts (Phase 2), stack memory wiki-rebuild (Phase 5)
```

## Phases

Each phase is its own commit set inside the `feat/brain-base` branch. Each ships value independently — cut at any boundary.

### Phase 0 — Branch + foundation docs (DONE)

- Branch `feat/brain-base` created off `main`.
- Design docs committed in `docs/design/brain/`.
- Commit: `be8369c docs(brain): seed Family Brain design docs and implementation plan`.

### Phase 1 — Memory stacklet skeleton + Ontology

**Goal:** the `memory` stacklet exists. On install it creates a Forgejo `memory` repo and pushes seeds. Working copy clones into the stacklet's data dir on start. The Archivist classifier reads the ontology via the memory stacklet's in-process API.

Files:

- `lib/stack/ontology.py` (NEW) — generic: `Ontology` class, `Topic`, `DocType`, `Person`, `KnowledgeKind`, TOML loader, resolvers (`resolve_topics`, `resolve_person`, `expand_query`), `classifier_prompt_section(lang)`. No vocabulary inside.
- `lib/stack/facts.py` (NEW, protocol-only in this phase) — `Fact` dataclass, `FactStore` `Protocol`. Concrete impl lands in Phase 2.
- `lib/stack/paths.py` (NEW or extended) — `stack_config_dir()`, `stack_data_dir(stacklet)`. Verify existing helpers first; add only what's missing.
- `stacklets/memory/stacklet.toml` (NEW) — declares the stacklet: `type = "host"`, `requires = ["code"]` (Forgejo lives in the code stacklet), no container, no port. Health check optional in v1.
- `stacklets/memory/seeds/ontology.toml` (NEW) — hand-authored. One entry per current taxonomy.toml name, enriched with id, synonyms, keywords, type cross-refs.
- `stacklets/memory/seeds/facts.toml` (NEW) — near-empty template, commented examples.
- `stacklets/memory/seeds/wiki/README.md` (NEW) — minimal scaffolding for the wiki tree.
- `stacklets/memory/hooks/on_install_success.py` (NEW) — `ForgejoClient.create_repo("memory")` then `put_file` for each seed under `seeds/`. Idempotent: skips files that already exist (sha check).
- `stacklets/memory/lib.py` (NEW) — in-process API over `ForgejoClient`: `get_ontology(stack, lang)` fetches `ontology.toml` from the memory repo and parses it into an `Ontology`. Caches the parsed object in-process; refresh on process restart. Imported by Archivist via `from stacklets.memory.lib import get_ontology`.
- `stacklets/docs/bot/archivist.py` (MODIFIED) — classify prompt switches from inline `json.dumps(category_tags)` to `memory_lib.get_ontology(self.language).classifier_prompt_section()`.

Tests:

- `tests/stacklets/test_ontology_taxonomy_sync.py` — every name in `stacklets/docs/taxonomy.toml` has a matching id in `stacklets/memory/seeds/ontology.toml` (and vice versa). Fails loudly on drift.
- `tests/stacklets/test_ontology_loader.py` — load, resolve_topics, resolve_person, expand_query happy paths.
- `tests/stacklets/test_memory_install.py` — `on_install_success` creates the repo and pushes seeds against a real Forgejo from the test harness (per project rules: no library mocks).
- `tests/integration/test_archivist_e2e.py` — already exists; must stay green with the new ontology source.

Out of scope:

- `stacklets/docs/seed.py` keeps reading `taxonomy.toml` (still seeds Paperless). Do **not** switch its source.
- No `stack facts` CLI, no Q&A, no wiki rebuild yet.

Time: ~8–10h.

### Phase 2 — Facts CLI against the memory repo

**Goal:** `stack facts` works end-to-end. Reads `facts.toml` + `facts.jsonl` from the working copy, writes back, commits, pushes.

Files:

- `stacklets/memory/lib.py` (EXTENDED) — concrete `FactStore`: read both files, append to jsonl for machine-origin facts, edit toml for hand-origin, commit + push each change.
- `stacklets/memory/cli/facts.py` (NEW) — `stack facts list | add | edit | remove | show`. Sets Git author from `users.toml`.

Tests:

- `tests/stacklets/test_facts_store.py` — read/write round-trip against a temp memory repo.
- `tests/stacklets/test_facts_cli.py` — `stack facts add` produces the expected commit in the working copy.

Time: ~5–7h.

### Phase 3 — Install interview

**Goal:** `stack init` interviews the user and seeds `facts.toml` + initial entity stubs into the memory repo.

Files:

- `stacklets/core/cli/interview.py` (NEW) — 6–8 question script:
  1. Household members + ages
  2. Nicknames / aliases per person
  3. Allergies, medical rules
  4. Shared interests (cooking, hobbies, …)
  5. Open stories (renovation, planned trip, big event)
  6. Primary contacts (doctor, school, main insurance)
  7. Anything else worth remembering?
- Answers flow through `FactStore` + write stub `wiki/family/<entity>.md` pages. All committed.
- Hookup: `stack init` calls the interview after users.toml is finalized. Gated by `--no-interview` for CI / scripted installs.

Tests:

- `tests/stacklets/test_interview_seed.py` — mocked answers produce the expected facts and stubs in the memory repo.

Time: ~4–6h.

### Phase 4 — Q&A in Element

**Goal:** ask the Archivist a question in `#documents`, get a grounded answer with citations.

Files:

- `stacklets/memory/lib.py` (EXTENDED) — `query_plan(text)` returns structured context: expanded topic ids, candidate persons, relevant facts.
- `stacklets/docs/bot/archivist.py` (MODIFIED) — new `_handle_question` branch in `_on_text`. Question-shape heuristic (ends with `?`, wh-word prefix, >5 words). Pipeline: `memory.query_plan(text)` → Paperless filtered search → LLM reader → cited reply.
- Reader prompt: "Answer using only the facts and documents below. Cite by fact id and doc id. Say 'I don't know' rather than guess."
- Fallback: short/keyword queries keep the existing `_paperless_search` path.

Tests:

- `tests/stacklets/test_qa_handler.py` — routing heuristic + prompt assembly.
- `tests/integration/test_archivist_qa_e2e.py` — file a doc, ask a question, assert the citation appears in the reply.

Time: ~5–7h.

### Phase 5 — Wiki rebuild + eager stubs

**Goal:** synthesize entity wiki pages from L1 document mirrors. `stack memory wiki-rebuild` is idempotent.

Files:

- `stacklets/memory/cli/wiki_rebuild.py` (NEW) — reads doc mirrors from the documents repo, groups by correspondent / person / topic / story (frontmatter), LLM-synthesizes each group, writes one entity page per group to `wiki/`, commits to memory repo.
- `stacklets/docs/bot/archivist.py` (MODIFIED) — eager stub creation in classification: when a new correspondent/topic/story first appears, write a stub entity page; subsequent docs append to its Timeline.
- The Q&A retriever (Phase 4) starts reading L3 wiki pages first (small + dense), falling back to Paperless for doc details.

Tests:

- `tests/stacklets/test_wiki_rebuild.py` — given a corpus of mirrors, rebuild produces the expected pages.
- Idempotency: re-running on unchanged input produces byte-identical output (after timestamp normalization).

Time: ~6–10h.

## Total estimate

~28–40h across five phases. Cut at any phase boundary.

## Open decisions before code

1. **Hand-authored vs auto-derived seed `ontology.toml`.** Plan assumes hand-authored, guarded by a sync test against `taxonomy.toml`. If you prefer auto-derivation (taxonomy is canonical, ontology is generated), the test becomes the generator.
2. **Cross-stacklet read pattern.** Archivist reads memory's ontology via in-process library import (`from stacklets.memory.lib import get_ontology`) reading the shared working-copy path. Both stacklets run on the same machine so this is fine in v1. HTTP boundary deferred. Confirm or override.
3. **Interview entry point.** Proposal: `stack init` runs the interview immediately after users.toml is finalized; `--no-interview` for CI / scripted installs.
4. **`stack_config_dir()` / `stack_data_dir()` helpers.** Verify whether the framework already exposes these before adding. If yes, reuse. If no, add in Phase 1.
5. **Cross-scenario seeds.** Famstack ships family seeds in `stacklets/memory/seeds/`. Deskstack later overrides via its own stacklet (replacing the `memory/` directory or pointing at different seed paths). Mechanism is TBD for 0.4.0+; for 0.3.0 we ship family seeds only.
6. **Forgejo repo name.** Proposal: `memory` (simple, matches the stacklet name). Alternative: `family-memory` or `brain`.

## File layout summary

```
# Famstack source (this repo, feat/brain-base branch)
lib/stack/
├── ontology.py            # NEW — generic Ontology, Topic, DocType, KnowledgeKind, QueryPlan
├── facts.py               # NEW — Fact, FactStore Protocol (impl in stacklet)
└── paths.py               # NEW or extended — stack_config_dir(), stack_data_dir()

stacklets/memory/          # NEW stacklet — type="host", no container, no bot (in v1)
├── stacklet.toml          # type="host", requires=["code"]
├── seeds/
│   ├── ontology.toml      # family-scenario seed
│   ├── facts.toml         # empty template
│   └── wiki/README.md     # initial wiki scaffolding
├── hooks/
│   └── on_install_success.py  # create Forgejo memory repo, push seeds (idempotent)
├── lib.py                 # in-process API over ForgejoClient: get_ontology, FactStore, query_plan
└── cli/
    ├── facts.py           # stack facts ... (Phase 2)
    └── wiki_rebuild.py    # stack memory wiki-rebuild (Phase 5)

stacklets/core/cli/
└── interview.py           # NEW — install-time interview (Phase 3)

stacklets/docs/
├── bot/archivist.py       # MODIFIED — classify via memory.get_ontology, Q&A handler (Phase 4), eager stubs (Phase 5)
└── (seed.py, taxonomy.toml unchanged)

# Forgejo (the only source of truth)
<forgejo>/<owner>/memory.git
├── ontology.toml          # seeded; hand- + system-edited (commit log = learning history)
├── facts.toml             # hand-authored; interview seeds it (Phase 3)
├── facts.jsonl            # machine-appended (Phase 2)
├── wiki/family/...        # entity pages (Phase 5)
├── wiki/<person>/...
└── meta/index.md          # master pointer (Phase 5)

<forgejo>/<owner>/documents.git  # existing — Archivist writes L1 mirrors
```

## Verification at each phase

| Phase | Pass criteria |
|---|---|
| 1 | `test_archivist_e2e` stays green. Ontology sync test green. Fresh `stack up memory` creates a Forgejo `memory` repo and pushes the seeds (idempotent on re-up). |
| 2 | `stack facts add/list/edit/remove` round-trips. Each operation produces a commit in the memory repo. |
| 3 | `stack init` on a fresh instance produces a populated `facts.toml` and ≥5 entity stubs, all committed. |
| 4 | In a test Matrix room, asking the Archivist a known-answer question returns the answer with a citation. |
| 5 | After `stack memory wiki-rebuild`, the memory repo contains one page per correspondent with non-empty Timeline. Re-running is byte-identical (after timestamp normalization). |

## What we're explicitly NOT building in 0.3.0

| Capability | When | Why deferred |
|---|---|---|
| Active decay of expired facts | 0.4.0 | Needs dream cycle; staleness is acceptable in v1 |
| Auto-supersede on contradiction | 0.4.0 | Same |
| Promotion (event → habit after N occurrences) | 0.4.0 | Same |
| Matrix conversation extraction (Deriver bot) | 0.4.0 | Bigger lift; needs opt-in room config + compute budget |
| Vector / semantic retrieval | 0.5.0+ | Keyword + ontology expansion sufficient at family scale |
| facts.jsonl rotation / fold-into-wiki | 0.4.0 | Append-only fine for first year |
| Real-time Deriver replacing wiki-rebuild CLI | 0.5.0+ | One-shot regeneration fits ~500 docs/yr volume |
| Proactive morning briefings | 0.5.0+ | Needs calendar integration + notification policy |
| Cross-domain Kit Bot in `#assistant` | 0.5.0+ | After Archivist Q&A proves the pattern |
| Ontology refresh on famstack upgrade | 0.5.0+ | Instance owns it; manual `git pull` from seed branch suffices in v1 |

## Where to look in sibling docs

- For the **why**: [knowledge-architecture.md](knowledge-architecture.md) §Vision and §Architecture.
- For the **vocabulary shape**: [ontology-design.md](ontology-design.md) (older), refined in [ontology-v1.md](ontology-v1.md).
- For the **layered build order**: [knowledge-implementation.md](knowledge-implementation.md).
- For the **concrete shapes and templates**: [knowledge-structure.md](knowledge-structure.md) (most current).
- For the **engram-as-backend exploration**: [engram-prototype.md](engram-prototype.md).

When the docs disagree, `knowledge-structure.md` wins. It's the distillation.
