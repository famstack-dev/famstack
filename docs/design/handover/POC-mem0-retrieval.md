# POC - mem0 retrieval through the agent

> Status: POC handover, not a production design.
> Branch: `poc-handover-mem0-retrieval`
> Seed context: `feat/family-agent`, `docs/design/agent/pointer-memory.md`,
> `stacklets/agent/workspace/skills/family-memory/SKILL.md`,
> `stacklets/memory/cli/search.py`

## Goal

Prove whether mem0 improves the family agent's ability to retrieve useful
family memory from the Simpsons demo vault when the retrieval path is exercised
through the agent, not as a standalone benchmark.

The POC is successful only if a Matrix/user prompt to the agent causes the
agent to use a mem0-backed search surface, return grounded answers with vault
paths, and still read source pages from the vault before making claims that need
detail.

The priority order is:

1. Correct facts from the source data.
2. Citations and links back to the source material.
3. Better ranking for vague questions and non-exact wording.

mem0 is only useful here if it helps the agent find the right source when the
user's words do not exactly match the vault text. It must not become an
uncited memory blob the agent trusts directly.

## Current conclusion

Do not make mem0 the core family-memory dependency.

The production direction should be a tailored family-memory retrieval layer as
the authority, with mem0 or another embedding backend as an optional semantic
ranking component behind that layer.

Reasoning:

- Correctness, citations, vault paths, and original-source links matter more
  than a generic "memory" abstraction.
- Many important questions are structured and should be deterministic:
  "me", profile, identity, topic overview, topic todos, documents, Paperless
  source, and known vault path.
- Those cases should use exact memory commands first, for example
  `stack memory person homer`, `stack memory topic itchy-scratchy-land`, todo
  reads, and document/source reads.
- mem0 is useful for vague language, paraphrases, and ranking candidate files,
  but a semantic match is not authoritative. It should find candidates, not
  provide final truth.
- The vault already has strong structured metadata: path, title, date, persons,
  tags, frontmatter source links, Paperless ids, and git history. A tailored
  index can preserve and rank that directly.
- Agent behavior is easier to control when tools match intent:
  `memory_person` for profile reads, `memory_topic` or `stack memory topic` for
  topic reads, `memory_search` for broad or vague lookup.

Recommended architecture:

1. Exact tools first for known entities and known surfaces.
2. Hybrid search for broad questions: lexical search plus metadata filters,
   with optional embeddings for ranking.
3. Citations come from vault metadata, not mem0 ids or chunk ids.
4. mem0 stays replaceable behind `stack memory search --backend mem0`.

The live bug that triggered this decision was a self/profile question:

```text
Stacky: what do you know about me?
```

The agent should not start with semantic memory search there. It knows the
sender handle from Matrix, so the correct first read is the deterministic person
surface for that handle. Broader search is only a follow-up if the user asks for
notes, documents, or related history beyond the profile.

## Concrete data source

Use the Simpsons/demo vault at:

```text
~/famstack-data/memory/vault
```

This is fixture/demo data for this POC. It is okay to extend it with additional
Simpsons facts, todos, notes, or topic pages when the retrieval test needs
better coverage.

Do not use a real household vault for this POC. The production rule still holds:
never read or copy a user's production family vault.

## Existing agent contract

The agent currently learns how to search from:

```text
stacklets/agent/workspace/skills/family-memory/SKILL.md
```

The important contract:

```text
stack memory search "<keywords>"
read_file vault/<relative-path>
```

`stack memory search` is implemented by `stacklets/memory/cli/search.py`. It is a
regex/full-text search over the curated vault and prints dated blocks with
relative paths. The agent then reads `vault/<path>` when it needs more detail.

For the POC, preserve this mental model for the agent. Change the retrieval
engine under it, or add an explicit sibling command, but do not make the agent
learn a second unrelated workflow.

## Agent tool behavior finding

The agent strongly prefers the built-in `grep` tool over a new custom
`memory_search` tool, even when the skill says to use semantic memory first.
This is probably because `grep` is a familiar built-in file-search affordance,
the vault is mounted as files, and the model has learned that path from prior
tool-use examples.

For the POC, the best control is not to fight that habit with more prompt text.
Instead, keep `grep` available but route vault-scoped grep calls through the
semantic memory backend:

```text
grep(path="vault", pattern="<natural query>")
```

should behave like:

```text
stack memory search "<natural query>" --backend mem0
```

while non-vault grep remains literal file grep. This lets the model keep using
the tool shape it prefers, while family-memory lookup still goes through mem0.

Important output shape: routed vault grep should put explicit read targets at
the top:

```text
Paths to read:
- vault/family/emails/...
```

Without that path block, the model may receive useful ranked results but still
make malformed follow-up calls such as `read_file({})` before recovering.

### Stack shim quoting bug

The agent container calls the host CLI through `stacklets/agent/client/stack`.
That shim used to join argv with plain spaces before sending the command to the
host API. Multi-word semantic queries then arrived as separate CLI arguments,
for example:

```text
stack memory search school sign handle soon --backend mem0
```

and argparse rejected the extra words. The shim must shell-quote each argv item
before sending the command line. Otherwise natural-language search works in the
host CLI but fails through the agent.

## POC shape

Build the smallest reversible adapter:

1. Index markdown files from `~/famstack-data/memory/vault` into a local mem0
   store.
2. Expose a search command with the same output obligations as
   `stack memory search`: relative path, title, date if known, short excerpt or
   reason, source metadata, ranking, and enough context for the agent to decide
   what to read.
3. Wire the agent skill to call the mem0-backed command for normal family
   lookup.
4. Ask questions through the agent layer and verify that the answer is grounded
   in returned vault paths.

Preferred command surface:

```text
stack memory search "<query>" --backend mem0
```

Acceptable POC-only surface if the smaller implementation wins:

```text
stack memory mem0-search "<query>"
```

If the second form is used, the handover must include the exact temporary skill
edit in `stacklets/agent/workspace/skills/family-memory/SKILL.md` so the agent
actually uses it.

## Indexing model

Start with file-level records before adding chunking. The vault is already
curated and relatively small.

For each markdown file:

- `memory`: concise text assembled from title, frontmatter, headings, bullets,
  and body excerpt.
- `metadata.path`: vault-relative path, for example
  `family/camping-trip/about.md`.
- `metadata.title`: parsed title or first `#` heading.
- `metadata.date`: frontmatter date if present.
- `metadata.persons`: frontmatter persons if present.
- `metadata.tags`: frontmatter tags if present.
- `metadata.paperless_id`: frontmatter Paperless id if present.
- `metadata.resource`: frontmatter source/resource URL if present.
- `metadata.paperless_url`: frontmatter Paperless base URL if present.
- `metadata.source`: `memory-vault`.
- `metadata.commit`: current vault `HEAD`, when available.

Chunk only if file-level retrieval is too blunt. If chunking is needed, keep the
path stable and add `metadata.chunk_id`; do not return chunk ids as the source of
truth to the agent. The source remains the vault file.

## Citation contract

Every mem0 result must preserve a path back to the original source. The agent
must be able to cite what it read without guessing.

Minimum result fields:

- `rank`: result order, starting at 1.
- `score`: backend score if mem0 exposes one.
- `path`: vault-relative markdown path.
- `title`: human-readable source title.
- `why`: short explanation, matched phrase, or semantic reason for the result.
- `source_url`: frontmatter `resource` if present.
- `paperless_id`: frontmatter `paperless_id` if present.
- `paperless_link`: composed Paperless document link if `paperless_id` and
  `paperless_url` are present.

Answer rules:

- For any factual claim from retrieved memory, cite the vault path.
- If the vault entry points to an original Paperless document or external URL,
  include that source link too.
- A semantic match alone is not enough for a detailed answer. The agent should
  read the vault file before stating details as fact.
- If sources conflict, name the conflict and cite both paths.
- Do not cite mem0 ids or chunk ids as user-facing sources. They are retrieval
  internals, not source material.

## Refresh policy

The index can be rebuilt from scratch for the POC.

Minimum acceptable refresh:

```text
stack memory mem0-index --vault ~/famstack-data/memory/vault --rebuild
```

Do not try to solve incremental sync until the retrieval quality is proven.
famstack already has git freshness semantics around the vault; mem0 should be a
derived cache that can be deleted and rebuilt.

## Agent-layer test scenarios

Run these through the agent, not only through the CLI.

### 1. Person recall

Ask a question about a family member that requires looking up their profile or a
note. Expected behavior:

- for profile or self-identity questions, agent calls the exact person read
  surface first, for example `memory_person` or `stack memory person homer`;
- for broader note/document questions about a person, agent may use
  `memory_search` with semantic ranking;
- result or read output includes a person path such as `homer/about.md` or
  `marge/about.md`;
- answer cites or names the relevant vault path in plain text.

### 2. Topic recall

Ask about a topic folder, for example the camping trip or Itchy & Scratchy Land.
Expected behavior:

- agent retrieves `family/<topic>/about.md` or `family/<topic>/todos.md`;
- agent reads the returned page when the answer needs exact todo text;
- stale brief data is not recited from context.

### 3. Fuzzy semantic query

Add or find a fact where exact keyword grep is weak. Example shape:

```markdown
family/camping-trip/about.md
```

Add a detail such as "Lisa is worried about mosquitoes near the lake" and ask:

```text
Who was concerned about bugs on the camping plan?
```

Expected behavior: mem0 retrieval should find the camping page even if the user
does not say "mosquitoes".

### 4. Ranked vague query

Ask a vague question that could match several memories, for example:

```text
What was the thing Lisa was worried about for the trip?
```

Expected behavior:

- mem0 returns ranked candidates, not an unordered blob;
- the top result is the most relevant source when the vault contains a clear
  answer;
- lower-ranked alternatives remain visible enough for the agent to notice
  ambiguity;
- the agent reads the top source before answering and cites it.

### 5. Source-link citation

Ask about a fact from a vault entry that has `paperless_id`, `resource`, or an
external source URL in frontmatter.

Expected behavior:

- answer cites the vault path;
- answer includes the original source link when present;
- answer does not cite a mem0 id or chunk id as the source.

### 6. Negative control

Ask for a fact that is not in the demo vault.

Expected behavior: the agent should say it did not find that fact after
searching. It must not invent an answer from semantically adjacent memories.

## Comparison baseline

Keep one grep-backed baseline run for each scenario:

```text
stack memory search "<query>" --vault ~/famstack-data/memory/vault --no-refresh
```

Record whether mem0 improves:

- recall for paraphrased questions;
- ranking quality for vague or ambiguous questions;
- precision for ambiguous family terms;
- answer grounding with citations and original source links;
- correctness of extracted facts after reading the cited source;
- latency acceptable for a chat turn;
- failure behavior on missing facts.

The POC does not need a broad benchmark. Ten well-chosen Simpsons questions are
enough to decide whether this is worth turning into production code.

## Implementation boundaries

Keep mem0 out of `lib/stack/`. The framework CLI stays stdlib-only.

Allowed POC locations:

- `stacklets/memory/cli/mem0_index.py`
- `stacklets/memory/cli/mem0_search.py`
- `stacklets/memory/mem0/` for helper code if the CLI files get too large
- `stacklets/agent/workspace/skills/family-memory/SKILL.md` for the temporary
  agent instruction change
- Runtime dependency: the memory curator container, not the host. The host
  `stack memory ...` command should dispatch into `stack-memory-curator` for
  mem0-backed retrieval.

Dependency options:

- Prefer the memory curator container. It already owns the memory working copy
  and derived state under `/data/memory`.
- If the curator image lacks too much for the POC, bot-runner is an acceptable
  temporary fallback. Do not install mem0 on the host and do not add it to
  `lib/stack/`.

## Open decisions

- Does mem0 store one memory per file, one per section, or one per extracted
  fact?
- Can mem0 run fully local with the current oMLX/OpenAI-compatible endpoint and
  a local vector store, or does the POC need an external embedding model?
- Should the long-term command become `stack memory search --semantic` rather
  than naming mem0 in the user-facing surface?
- How does the index notice vault updates: explicit rebuild, git HEAD check, or
  hook from the memory curator?

## Exit criteria

Ship the POC only when these are true:

- The agent, when asked in chat, actually calls the mem0-backed search path.
- Answers are grounded in vault paths and include original source links when the
  vault entry has them.
- Detailed factual claims are made only after reading the cited source file.
- Ranking helps vague/non-exact queries reach the right source better than the
  grep baseline.
- The negative control does not hallucinate.
- Rebuilding the index from `~/famstack-data/memory/vault` is documented and
  repeatable.
- The grep baseline is kept so the result is a comparison, not a vibe check.

## Suggested first slice

1. Add `stack memory mem0-index --vault <path> --rebuild`.
2. Add `stack memory mem0-search "<query>" --vault <path> --limit 10`.
3. Temporarily edit the family-memory skill to prefer `mem0-search`.
4. Rebuild from `~/famstack-data/memory/vault`.
5. Run the agent-layer scenarios above.
6. Record results in this file under a new `POC run log` section.

## POC run log

2026-07-07:

- Implemented the POC as memory-owned, curator-executed retrieval:
  `stack memory search --backend mem0` dispatches into
  `stack-memory-curator`.
- Built the curator image with `mem0ai`.
- Indexed the Simpsons demo vault: 84 files into `/data/memory/mem0`.
- Verified the agent-facing command returns ranked, citable candidates:
  `stack memory search "what was Lisa worried about for the trip" --backend mem0`.
- The current local embedder is intentionally lightweight. It proves the
  wiring, citation metadata, ranking surface, and non-host runtime shape. A real
  embedding model should replace it before judging semantic quality.
