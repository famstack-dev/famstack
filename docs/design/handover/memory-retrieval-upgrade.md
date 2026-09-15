# Handover: family-memory retrieval upgrade

**Status:** research done, not built. Ready for a dev-rig implementation session.
**Audience:** an agent session on a development rig, not production.
**Seed context (read first):**
- `docs/design/agent/review-2026-09.md` (the agent review)
- `docs/design/agent/agent-improvement-log.md` (measurements; search sections)
- `docs/design/handover/POC-mem0-retrieval.md` (the earlier mem0 POC; this
  handover supersedes its engine choice, keeps its citation contract)
- `stacklets/memory/cli/search.py` (the current search engine)
- `stacklets/agent/workspace/skills/family-memory/SKILL.md` (agent contract)

## Why

Today `stack memory search` is a pure-Python regex walk over every `*.md`
in the vault (`stacklets/memory/lib.py`, `search_memory`). It works, but:

- No ranking. It matches lines and sorts by frontmatter date. A vague or
  paraphrased question ("who was worried about bugs on the trip") does not
  reach the right page.
- The scope-first guidance in the skill costs the agent extra tool
  iterations when the answer sits in a personal bucket, because a global
  search returns unranked noise. Measured in the rig, 2026-09-15.
- The family is German-speaking. Regex does no diacritic folding and no
  compound handling ("Geburtstagsfeier" does not match "Geburtstag").

The mem0 POC (`POC-mem0-retrieval.md`) already concluded mem0 should not be
the core dependency: correctness, citations, and vault paths matter more
than a generic memory blob. This handover keeps that conclusion and names
the concrete engine to build instead.

## Decision

Two tiers. Build tier 1 first, measure, then decide on tier 2.

**Tier 1 (first slice): SQLite FTS5 + BM25.** Stdlib `sqlite3`, one index
file next to the vault clone, incremental update on git change. Single-digit
millisecond queries. This is the pragmatic 2026 answer for a few thousand
short markdown notes, and the evidence says lexical search with an iterating
agent captures most of the value (see Sources).

**Tier 2 (stretch, only if tier 1 recall on paraphrases disappoints):**
hybrid via `sqlite-vec` + a small local embedder, fused with reciprocal
rank fusion (RRF). The embedder is `LFM2.5-Embedding-350M`, already served
by this machine's oMLX (confirmed in the model list, 2026-09-15). Embeddings
run in-process on oMLX, so only chat and embedding calls leave the CLI.

**Do not** bring in Meilisearch or Typesense (another always-on service),
DuckDB FTS (no incremental update), or tantivy (unneeded below ~100K docs).

### Established OSS alternative to trial: Hindsight

If the goal shifts from "better search" to "a managed memory layer that
also consolidates", the one worth a shadow trial is **Hindsight**
(vectorize-io, MIT, one container + pgvector, in-process local embeddings,
per-bank isolation, markdown projection). It is the only candidate with
published open-model benchmarks. Trial it in shadow mode against the
existing search, never as the source of truth. The vault stays authoritative.
Full comparison (Mem0, Letta, Zep/Graphiti, LangMem, Hindsight) is in the
agent review. This is a fork in the road, not a step after tier 2: pick
FTS5-first for a search upgrade, or Hindsight for a memory-layer trial, not
both at once.

## Tier 1: concrete build

### Index location

One SQLite file next to the vault working copy, gitignored:

```
{data_dir}/memory/vault-index.sqlite3
```

The vault is a git repo and the index is a derived cache. It can be deleted
and rebuilt at any time; do not commit it.

### Schema

```sql
CREATE TABLE docs(
  id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT,
  persons TEXT, tags TEXT, date TEXT, mtime REAL, sha1 TEXT, body TEXT);

CREATE VIRTUAL TABLE fts USING fts5(
  title, persons, tags, body,
  content='docs', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2');   -- folds ä→a, ö→o, ü→u
```

`remove_diacritics 2` is the correct variant (handles combining
codepoints). Frontmatter (persons, tags, date) becomes weighted, filterable
columns instead of stripped noise. That is an upgrade over today, where the
regex engine strips frontmatter entirely.

German compounds: FTS5 has no compound splitter. Two options, cheapest
first:
1. The agent model already generates the keywords, so prompt it to emit
   stems and variants ("Geburtstag" alongside "Geburtstagsfeier"). Free.
2. If recall on compounds is still weak, add a companion trigram table
   (`tokenize='trigram'`) and union its hits. Larger index, fine at this
   scale; query tokens under 3 chars match nothing in that table.

### Incremental update

On each search call (or a `post-merge` git hook in the clone), diff
`git ls-files -z '*.md'` plus mtime/sha1 against `docs`, upsert changed
rows via the external-content protocol (`INSERT INTO fts(fts, rowid, ...)
VALUES('delete', ...)` then insert). A no-change scan over a few thousand
files is roughly 10-30 ms; a full rebuild is seconds. Do not attempt
incremental sync cleverness before recall quality is proven.

### Query

The agent's 2-4 keywords map directly. Quote and prefix each keyword
(quoting neutralizes FTS5 query-syntax injection from model-supplied text):

```sql
SELECT path, title, date,
       snippet(fts, 3, '**', '**', ' … ', 12) AS snip,
       bm25(fts, 8.0, 4.0, 4.0, 1.0) AS score
FROM fts WHERE fts MATCH '"urlaub"* OR "geburtstag"* OR "oma"*'
ORDER BY score LIMIT 20;
```

Weight title and tags above body. Keep date as a secondary sort or a mild
recency boost in Python. Expected latency under 10 ms.

### Wiring into the existing surface

The simplest path swaps the engine under `stack memory search` and leaves
the agent contract in SKILL.md alone. Start there for the A/B, so the only
variable is the engine.

**The agent contract is not fixed. Change it when a change clearly improves
retrieval results or how the agent interacts with search.** The whole point
of this work is better answers, not a preserved workflow. Candidate contract
changes worth testing once the engine ranks well:
- new query fields the model fills directly (persons, tags, date range,
  scope) instead of relying on the model to phrase them into keywords;
- returning ranked candidates with scores so the model can judge ambiguity
  and read the top page, rather than re-searching;
- a single retrieval verb that the model reaches for reliably, if the split
  between `memory_search` / `memory_person` / `grep` still causes wasted
  iterations (measured in the rig; see the review).

Discipline for any contract change:
- Measure it against the current contract in the rig. Keep it only if it
  cuts tool iterations or improves the answer, and log the before/after in
  `agent-improvement-log.md`.
- Keep a `--backend regex` path for the engine A/B regardless of contract.
- Two invariants stay whatever the contract becomes:
  1. Output the agent cites from must carry a resolvable source link, and
     the model must copy it verbatim. If you change the output shape, update
     the SKILL.md Sources rule in the same change and re-test citations. See
     `_format_block` in `stacklets/memory/cli/search.py`.
  2. Citations are vault paths and original source links (Paperless id,
     resource URL), never index rowids or chunk ids. Reuse the rules in
     `POC-mem0-retrieval.md` "Citation contract".

## Tier 2: hybrid (only if needed)

```sql
CREATE VIRTUAL TABLE vec USING vec0(id INTEGER PRIMARY KEY, emb float[768]);
```

Same SQLite file. Embed whole pages (frontmatter stripped, title
prepended) at index time via oMLX `/v1/embeddings` with
`LFM2.5-Embedding-350M`; split only pages over ~512 tokens at heading
boundaries. At query time, embed the joined keyword string once, take FTS
top-50 and vec top-50, fuse with RRF (k=60), return top-20 with FTS
snippets. Expected round trip 50-250 ms, dominated by the one query
embedding. If German recall on paraphrases still disappoints, swap the
embedder to `bge-m3` (also served) before adding any other machinery.

## Dev-rig rules

- This is a development rig. Isolated experiments against the oMLX endpoint
  are allowed. Never touch production services or a real household vault.
- Use synthetic data. The agent-lab rig at `tools/agent-lab/rig/` already
  has a fabricated demo vault and an end-to-end harness; reuse it. Its
  `lab-api.py` implements `memory search`, so point the new engine there
  first, then the real CLI. Read `tools/agent-lab/rig/README.md` for how to
  run a turn, the KPIs (wall time, LLM calls, cached tokens, TTFT), the
  cache-block rule, and the A/B method. That is how you measure every
  change in this handover.
- Keep the search engine stdlib-only in `lib/stack/` if it lands there;
  `sqlite3` is stdlib, `sqlite-vec` is one wheel and belongs in a stacklet
  (memory curator container), not in `lib/stack/`. Mirror the mem0 POC's
  boundary rules.

## Test scenarios (reuse the rig)

Run these through the agent, not just the CLI, with `tools/agent-lab/rig/`.
The demo vault already has multi-person topics, DMs, and a pollution trap.

1. Paraphrase: a fact worded differently from the page ("who was worried
   about bugs" vs a page saying "mosquitoes"). FTS with model-supplied
   stems should still reach it; if not, that is the tier-2 trigger.
2. German compound: query "Geburtstag" against a page that only says
   "Geburtstagsfeier". Tests the tokenizer and stem guidance.
3. Ranked vague query: several candidate pages; the right one should rank
   first and the agent should read it before answering.
4. Scope miss: a question whose answer is in a personal bucket while the
   agent is in a topic session. Ranked global search should find it in one
   search instead of the current multi-probe.
5. Negative control: a fact not in the vault. The agent must say it did not
   find it, never invent from an adjacent page.
6. Latency: a search tool call returns well under 1 s.

Keep one regex-baseline run per scenario (`--backend regex`) so the result
is a comparison, not a vibe check. Log to `agent-improvement-log.md`.

## Exit criteria

- Ranking beats the regex baseline on paraphrase and vague queries.
- German diacritics fold; compounds reach their base word (via stems or
  the trigram table).
- Any contract change is measured to cut iterations or improve answers, and
  the SKILL.md Sources rule matches the output shape; citations still
  resolve.
- Negative control does not hallucinate.
- Index rebuild from the vault is documented and repeatable.
- Query latency under 1 s through the agent.

## Sources (from the 2026-09-15 research)

- SQLite FTS5 reference: https://www.sqlite.org/fts5.html
- Diacritics variant 2: https://www.sqlite.org/forum/forumpost/bffb495577
- "Is Grep All You Need?" (lexical search suffices for iterating agents),
  arXiv 2605.15184
- "BM25 Wins at Scale", arXiv 2607.26497
- LFM2.5-Embedding-350M (multilingual incl. German, short-context):
  https://www.liquid.ai/blog/lfm2-5-retrievers
- Apple Silicon embedding latency (bge-m3 ~159 ms/query on a Mac mini):
  https://nullmirror.com/en/blog/2026-02-28-embedding-models-on-affordable-cloud-vms-and-apple-silicon/
- sqlite-vec: https://alexgarcia.xyz/sqlite-vec/
- Hybrid FTS5 + sqlite-vec + RRF worked example:
  https://dev.to/soytuber/building-a-hybrid-rag-in-200-lines-sqlite-fts5-sqlite-vec-rrf-38h1
- Hindsight (OSS memory layer trial option): https://github.com/vectorize-io/hindsight

## Suggested first slice

1. `lib/stack/` or a memory helper: build the FTS5 index from the demo
   vault, one file-level record per page, frontmatter into columns.
2. Add `--backend fts5|regex` to `stack memory search`, default fts5, same
   output shape.
3. Point the rig's `lab-api.py` search at the new backend.
4. Run the six scenarios above against the demo vault, both backends.
5. Record results in `agent-improvement-log.md`.
6. Decide tier 2 from the paraphrase and compound results.
