# Handover: where retrieval stands, and what round two should do

**Status:** round one measured and stopped. Nothing is blocked.
**Branch:** `feature/improve-brain-retrieval2`, PR #99 (pushed, unmerged)
**Numbers and method:** `docs/design/brain/retrieval-engine-poc.md`
**Supersedes the plan in:** `docs/design/handover/memory-retrieval-upgrade.md`
**Date:** 2026-09-16

## What round one was, in one paragraph

The earlier handover asked for a SQLite FTS5 + BM25 index to replace the
regex walk behind `stack memory search`. It was built, benchmarked
against a fabricated corpus, then run through the real agent on a
420-page vault. The index wins the benchmark and loses the only test
that matters. One small change did earn its place. The rest of this
document is what to carry forward and what to leave alone.

## The one thing that improved

**Search matches title and tag values, not only the body.** Frontmatter
was stripped before matching so a query for "date" would not hit every
page through its `date:` line, and that threw the title out with the
field names. A page titled "Zahnarzttermin Lisa" was invisible unless
its body repeated the word.

Measured by replaying the 104 queries the agent really sent
(`tools/retrieval-lab/replay.py`): queries returning nothing fall from
**31% to 24%**, seven real searches rescued. That is the whole of the
measured gain from this work.

## What did not work, so nobody repeats it

| Change | Result |
|---|---|
| FTS5 + trigram + RRF index | 23% to 60% recall@1 on the bench, **no agent-level difference** |
| Confidence gate on keyword coverage | answers-to-absent-facts 92% to 17% on the bench, **bought nothing**; the model already declines |
| Skill instruction "search in the family's language" | dead-end rate 33% to 31%, **noise** |
| Diacritic folding ("Kase" finds "Käse") | **zero** on real agent queries; the agent types English, not umlaut-less German |

The pattern behind all four: the reasoning layer was already absorbing
the weakness being fixed. An agent that can search twice and read a page
does not need better ranking, and does not invent facts just because
search handed it a near-miss.

## What should land on main

Round one produced 4371 lines. Most of it should not be carried.

**Land (about 700 lines, all measured or explanatory):**

- title and tag matching in `search_memory`, plus its tests
- `docs/design/brain/retrieval-engine-poc.md`, whose job is to stop the
  index being built a second time
- the rig's search log (`lab-api.py --search-log`) and
  `tools/retrieval-lab/replay.py`. Every real insight in round one came
  from the search log, and replay is what finally answered "did anything
  improve" honestly

**Leave on PR #99:**

- `stacklets/memory/fts_index.py` and its tests, 1013 lines with no
  callers. Dead code in main is code somebody eventually wires up
  because it is there. Check the branch out if round two needs it.
- the rest of `tools/retrieval-lab/`, about 2330 lines. Genuinely useful
  if there is another retrieval question, but its gold set needed
  correcting twice in one session, so it is not yet an artefact to
  depend on.
- the `SKILL.md` hunk: a prompt change with no evidence behind it.

**Marginal, decide rather than drift:** diacritic folding is 8 lines and
costs about 15% of search latency (10.5 to 12.0 ms p50) for zero
measured benefit. But it was measured against the *agent*, and
`stack memory search` also serves family members typing German on phone
keyboards through chat. That is the case it fixes and it is unmeasured,
not disproven.

## Open, and worth deciding before round two

- **`--nl` was used 0 times in 161 agent searches.** A documented flag, a
  full LLM round trip, and a chunk of `search.py` that the agent has
  never reached for. It writes its own regex alternation instead. Either
  the agent contract should point at it or it should go.
- **The oMLX prefill ceiling.** Five of six runs of the multi-search
  question died on the prefill memory guard, which refused the prompt at
  a context of 8192 tokens because its predicted peak would exceed the
  endpoint's safety cap. That is a harder limit on complex questions
  than retrieval quality is, and it has nothing to do with search.
- **The unverified half of round one.** `unmatched_terms` makes an empty
  search say which words appear nowhere, so "wrong words" and "not
  written down" stop looking identical. Unit-tested and hand-checked;
  the rig run pairing it with its skill lines was killed partway. About
  20 minutes to finish.

## What round two should actually be

**Embeddings, and nothing else in retrieval.** Paraphrase recall is 29%
and every remaining failure is semantic: no lexical engine reaches "läuft
ab" from a page that says "Kündigung muss drei Monate vorher raus". That
is the only lever left on answer quality, and the bench already has the
number it has to beat.

Before building it, settle whether it clears the bar this round did not:
a measurable improvement **through the agent**, not on a benchmark.

## Method notes, learned the hard way

Round one reported four findings that did not survive being measured
again. Each correction came from running one more arm, never from
thinking harder. If round two runs agent experiments:

- **One run per cell is a dice roll.** A claimed correctness win here was
  the old engine's single unlucky draw; it was right 2 of 3 times on
  repeat. Always repeat, always alternate which arm goes first.
- **Log what each arm was actually asked.** The two arms in one A/B
  shared only 19% of their keyword vocabulary, because the agent writes
  its own query. Without that log, a difference between arms cannot be
  told apart from the agent having asked one of them better questions.
- **Attribute by what moved, not by what a fix was designed for.** Three
  of the four bad claims came from crediting a change with a gain that
  belonged to a different change.
- **Replay real queries, not only a gold set.** The gold set said folding
  was valuable. The agent's own queries said it does nothing. A gold set
  written by the same person who wrote the fix will agree with the fix.
