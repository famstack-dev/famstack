# Retrieval engine PoC: what the numbers say

**Status:** probe complete, nothing wired into the CLI yet.
**Answers:** `docs/design/handover/memory-retrieval-upgrade.md`
**Bench:** `tools/retrieval-lab/` (177-page fabricated vault, 47 questions)
**Date:** 2026-09-16

The handover proposed SQLite FTS5 + BM25 as tier 1 and named a trigram
companion as the fallback if German compounds hurt. This measured both
against the shipping regex engine before writing any of it into
`stack memory search`, on a corpus large enough for ranking to matter.

Every number below comes from `tools/retrieval-lab/evaluate.py`, which
runs all three engines over one question set in about two seconds.

## Query quality

| Engine | recall@1 | recall@5 | MRR | p50 latency |
|---|---|---|---|---|
| regex (today) | 23% | 57% | 0.36 | 9.8 ms |
| fts5 | 57% | 69% | 0.61 | 0.8 ms |
| fts5 + trigram | **60%** | **71%** | **0.64** | 1.3 ms |

35 answerable questions. The right page comes first 2.6x as often, and
a search costs a tenth of what it does today -- on 177 pages. The regex
engine reads every file on every query, so its 9.8 ms grows with the
vault while FTS5's does not.

By question kind, recall@1 / recall@5:

| Kind | n | regex | fts5 | fts5+tri |
|---|---|---|---|---|
| keyword | 6 | 17% / 67% | 83% / 83% | 83% / 83% |
| paraphrase | 7 | 0% / 0% | 29% / 29% | 29% / 29% |
| compound_head | 4 | 75% / 75% | 100% / 100% | 100% / 100% |
| compound_tail | 5 | 60% / 100% | 40% / 80% | 60% / 100% |
| fold (TUV for TÜV) | 3 | 0% / 33% | 100% / 100% | 100% / 100% |
| translit (Ruecken for Rücken) | 3 | 0% / 33% | 33% / 33% | 33% / 33% |
| vague | 4 | 25% / 100% | 50% / 75% | 50% / 75% |
| scope | 3 | 0% / 67% | 33% / 67% | 33% / 67% |

### The trigram index is not optional for a German family

This is the finding that changes the plan. FTS5 matches from the front
of a word, so "Geburtstag" reaches "Geburtstagsfeier" but "Feier" does
not. The regex engine matches *substrings*, so it gets that case right
by accident -- and German puts the noun the family asks with at either
end of a compound.

Shipping FTS5 alone would therefore be a **regression** on compound
tails: 60% to 40% recall@1, and one question ("Wann ist die Feier",
against a page titled *Omas Geburtstagsfeier*) went from one correct hit
to zero hits at all. Adding the trigram index and fusing with RRF
restores it to 60% / 100% while keeping every other gain.

The handover had this as a stretch item, conditional on compounds
disappointing. They did. It belongs in the first slice.

### Paraphrases are where tier 1 runs out

29% recall@1 on questions that share no content word with their page,
up from 0%. Better, still the weakest class, and no amount of lexical
tuning fixes it: "wer hat Probleme mit dem Magen" cannot reach a page
that says "Bauchweh" by matching characters. This is the tier-2 trigger
the handover named, now with a number on it.

### A German gap the handover did not name

`translit`: a query spelling the umlaut out ("Ruecken", "Spuelmaschine")
against a page that uses it ("Rücken", "Spülmaschine"). 33% for every
engine. `remove_diacritics` folds ü to u, not to ue, so the two spellings
stay different words. Cheap to fix in normalisation if it matters --
worth raising with the family before adding machinery for it, since it
depends on how they actually type.

## Confidence correctness

The question behind this half: when the search returns something, can
anything downstream tell whether it is the answer or just the nearest
page? Today nothing can, and the cost is measurable.

**Without any gate, 92% [65-99%] of questions whose answer is not in the
vault still return at least one page** -- a median of 6 of them. That is
the raw material for a confident answer about something nobody wrote
down.

Five candidate signals, scored by how well each separates "the top hit
answers the question" from "it does not" (AUC; 0.5 is a coin flip):

| Signal | regex | fts5 | fts5+tri |
|---|---|---|---|
| **top_coverage** (share of query keywords on the top hit) | 0.73 | **0.85** | 0.80 |
| best_coverage (same, best hit in the set) | 0.54 | 0.83 | 0.79 |
| top_score (the BM25 itself) | 0.50 | 0.75 | 0.68 |
| margin (top score over runner-up) | 0.50 | 0.57 | 0.58 |
| coverage x margin | 0.50 | 0.58 | 0.61 |
| hit_share (how full the result page is) | 0.25 | 0.40 | 0.41 |

**The score is not the confidence signal.** The obvious move -- trust
BM25, or trust the gap to the runner-up -- is the weak one: margin lands
at 0.57, barely above chance. What tracks correctness is how much of the
question the top page actually contains. That is also the signal the
regex engine could compute today, which makes it a change that does not
depend on the index landing first.

`hit_share` below 0.5 means it runs backwards, and informatively: a
query that fills the result page is usually a query whose words are too
common to mean anything.

At its best cut (0.40 coverage) on the fts5 arm:

| | |
|---|---|
| balanced accuracy | 80% |
| answers a fact that is not in the vault | 17% [5-45%], down from 92% |
| answers with the wrong page | 40% |
| suppresses a right answer | 10% |

So a coverage gate would cut confidently-wrong answers on absent facts
by roughly five to one, and pay 10% of correct answers for it. On 12
absent questions the interval is wide; the direction is clear, the
precise rate is not.

Note the trigram arm costs a little confidence (0.85 to 0.80) for the
recall it buys: matching inside words finds more pages and means less
per match. Worth watching, not worth reversing.

### Validity check

`top_coverage` is a fraction of the query's keywords, so it falls as a
question gets longer. If the absent questions were wordier than the
answerable ones, the AUC would be measuring question length. They are
not: median 3.0 keywords either way, mean 2.94 against 3.08.

## Keeping the index current

An index is a cache, and a cache that quietly falls behind is worse
than no cache: the family gets yesterday's answer with today's
confidence. Two links in the chain, with different owners.

**Forgejo to the clone** is `refresh_vault_if_stale`, which already
exists and does not change. It compares local HEAD to remote HEAD and
pulls only on a difference. When the remote is unreachable it says so
and reads proceed against the stale clone -- the existing contract.

**The clone to the index** is new, and leans on a fact that is already
load-bearing elsewhere in this stacklet: *nothing writes into the vault
working copy outside git*. `update_memory` reads the canonical file
from Forgejo, commits there, and fast-forwards the clone;
`propagate_write` already uses that clone's HEAD as its "has it landed"
token. So a HEAD that has not moved is proof that no page has changed.

The index stores the HEAD it was built from. Each search compares, and
skips the reconcile entirely when they match:

| Vault pages | HEAD gate | Scan every file | Cold rebuild |
|---|---|---|---|
| 177 | 14.4 ms | 8.2 ms | 64 ms |
| 1000 | 15.7 ms | 42.5 ms | 252 ms |
| 3000 | 15.0 ms | 124.7 ms | 651 ms |
| 8000 | 14.9 ms | 337.5 ms | 1810 ms |

Both columns are real `build_index` calls where nothing changed; the
scan column is the same tree with `.git` hidden so the gate cannot
fire. Reproduce with `tools/retrieval-lab/freshness.py`.

Below roughly 400 pages the gate is the *slower* of the two, because a
`git rev-parse` is a subprocess and statting 177 files is not. It is
still the right choice: 15 ms is constant and 337 ms is not, and the
number that matters is the one at the size a vault grows into. The
obvious further saving is to pass HEAD in rather than re-read it --
`refresh_vault_if_stale` already computed it moments earlier -- which
is a one-line change when the CLI is wired up.

Four properties make the staleness safe rather than merely fast:

- **A vault that is not a checkout always scans.** `--vault` overrides,
  fixtures and the lab have no HEAD to trust, so nothing
  short-circuits and an edit is picked up.
- **The HEAD is stored in the same transaction as the rows it
  describes.** A reconcile that dies halfway rolls back both, and the
  next search redoes it. The index is never half updated while
  claiming to be current.
- **An unreadable page is skipped, not fatal.** A sync can delete a
  file between the walk listing it and the reconcile reading it.
- **Concurrent indexers wait instead of failing.** The CLI on the host
  and the archivist in its container both reconcile; SQLite's default
  is to error the instant the file is busy, so the connection sets a
  busy timeout.

What this deliberately does *not* do is make a hand-edited working copy
visible. In production nothing hand-edits it. That is the whole reason
the gate is sound, and the cost of it is written into the test that
pins the behaviour.

## What this changes in the plan

Revised after the repeated agentic run, which is the measurement that
counts.

1. **Do not ship the index.** Three repeats, seven questions, two arms:
   no difference in answers, iterations or wall time that survives the
   run-to-run spread. The engine bench's 23% to 60% recall@1 is real
   and does not reach the family, because the agent closes the gap by
   iterating.
2. **Drop the confidence gate.** Its engine-level case was the best
   number in this document and it bought nothing once a model was
   reading the results. The 92% figure was measuring a risk the
   reasoning layer already absorbs.
3. **The agent searches one word at a time.** Median search: one
   keyword. Any ranking scheme that earns its keep by combining
   evidence across terms has nothing to work with. Changing *how the
   agent queries* is a bigger lever than changing what answers it, and
   it is free.
4. **Raise the prefill ceiling or trim the agent's context.** Five of
   six runs of the multi-search question died on an oMLX memory guard.
   That is a harder limit on complex questions than retrieval quality
   is, and it is unrelated to any of this work.
5. **Tier 2 (embeddings) is the remaining lever on quality.**
   `expiring` failed on every arm and every run, because no lexical
   engine reaches "läuft ab" from a page saying "Kündigung muss drei
   Monate vorher raus". Paraphrase recall of 29% is the number to beat.

6. **The two cheap fixes are in**, and neither needs an index. See
   "The cheap fixes, measured" below: recall@1 21% to 26%, recall@5
   54% to 64%, no class worse, about fifteen lines.

**Coverage as a displayed signal is not done, on purpose.** It was
listed as a cheap fix, but the only thing measured about coverage is
that *gating* on it hurt. Showing it to the model is untested, and it
adds output the agent pays to read on its hot path. It needs a reason
before it needs an implementation.

## The agentic test, and its kill criterion

Everything above measures retrieval in isolation, which is an
intermediate result. An agent that can search twice and read a page
closes part of the gap without any of this. And at a vault of a few
hundred pages we sit in the tier where "BM25 Wins at Scale" puts the
file-system agent *ahead* of BM25; lexical retrieval wins there on cost
(39x fewer query tokens), not on accuracy. On local inference that cost
is the family's waiting time, so it still matters, but it is a
different argument from the one the handover made.

So the deciding test is the agent answering questions that one lookup
cannot: several pages combined, arithmetic across them, or the
discipline to say a thing is not written down. Three arms, same
questions, 420-page vault: `regex`, `fts5`, `fts5+gate`.

**Written before the run, so it cannot be adjusted to fit the result:**

Ship the index if it does at least one of

- answers a complex question correctly that `regex` gets wrong, or
- cuts tool iterations (`llm_calls`) on questions both get right, or
- stops an invented answer on an absent fact that `regex` invents.

Otherwise drop the index, the trigram table and the RRF fusion, and
keep only the two cheap fixes: diacritic folding inside the existing
regex walk, and the coverage gate, which is a pure function over hits
and needs no index at all.

## What the agent actually did

420-page vault, seven questions, three backends, one run each. Model
`Qwen3.6-35B-A3B-UD-MLX-4bit` on the house oMLX. Replies in
`tools/retrieval-lab/out/agent-ab.json`, readable with `replies.py`.

| Question | regex | fts5 | fts5+gate |
|---|---|---|---|
| camping-todo (multi-page) | partial, 3 calls | partial, 4 | **best**, 3 |
| repair-total (arithmetic) | **3 of 4 bills**, 7 | 2 of 4, 8 | 2 of 4, 6 |
| nut-cake (constraint) | **correct, 2** | correct, 3 | correct, 3 |
| expiring (temporal) | missed everything, 5 | noise, 9 | missed, 5 |
| feier (compound) | **wrong**, 4 | **correct**, 3 | **correct**, 3 |
| absent-birthday | declined, 12 | **declined, 8** | infra error, 11 |
| absent-ticket | declined, 5 | **declined better, 4** | declined, 10 |
| **total** | **38 calls, 274 s** | 39 calls, 324 s | 41 calls, 299 s |

That first pass ran one turn per cell, and every conclusion drawn from
it about individual questions was wrong. A second pass repeated each
cell three times, alternated which arm went first so neither always
paid the cold prefix cache, and logged every search both arms received.

| | regex | fts5 |
|---|---|---|
| calls per run | 36 [35-41] | 37 [33-37] |
| wall seconds per run | 244 [238-279] | 261 [238-332] |

Against the criterion written before the run:

1. **Answers a complex question correctly that regex gets wrong: no.**
   `feier` was the claimed win. Over three runs the ranked arm is
   correct 3 of 3 and the regex arm 2 of 3. The single run that
   started all this was regex's one bad draw. At n=3 that is not a
   difference, and the mechanism story built on top of it (date
   sorting buries the page) was explaining noise.
2. **Cuts tool iterations: no.** The totals overlap.
3. **Stops an invented answer: no.** Nothing invented anything, on
   either arm, in any run.

### The comparison was weaker than it looked

The agent writes its own query for each search, and the search logs
show the two arms were barely asked the same things: **19% keyword
vocabulary overlap**, 18 shared terms out of 96 distinct. A difference
between the arms would have been as easily explained by the agent
happening to ask one of them better questions.

### Why the engine gains do not reach the agent

The search log answers this. The **median search carries one keyword**.
The agent does not hand over the 2-4 term queries the bench fed the
engines; it sends a single word, looks, and sends another. BM25 ranks
by combining evidence across terms, and there is almost nothing to
combine. The agent harness turns search into grep no matter what is
underneath it, which is the same effect "Is Grep All You Need?"
reports.

One mechanical difference did survive: the ranked arm dead-ends less
often, 16 empty results of 79 searches against 27 of 82. It did not
convert into fewer iterations or better answers.

### An infrastructure limit, not a retrieval one

`repair-total` hit an oMLX prefill guard rejection in **5 of 6 runs**,
on both arms: `predicted peak would exceed prefill safety cap 46.8GB
... kv_len=8192`. Questions that need several searches grow the
context past what the endpoint will prefill. The earlier claim that
regex "saw three of four repair bills" was reading whichever arm got
further before erroring. That question measures the endpoint, not the
engine, until the guard is raised or the context trimmed.

### The gate earned nothing here

Its engine-level case was strong: without it, 92% of unanswerable
questions still return pages. At agent level that never became a wrong
answer, because the model reads the pages and declines on its own. The
gate cost three extra calls across the set and produced the run's only
hard failure, an oMLX prefill guard rejection. **Drop it.** The
engine-level number was measuring a risk the reasoning layer was
already absorbing.

### Ranking plus a hard limit loses aggregate questions

`repair-total` is the one to keep. The regex engine returns everything
that matched, so the agent saw three of the four repair bills. The
ranked arms return the best five, and the fourth bill ranked sixth, so
they saw two and confidently answered 230 Euro. None of the three got
the right total.

Ranking helps "which page answers this" and hurts "find every page
like this". That is not an argument against ranking; it is an argument
that a fixed `--limit 5` is the wrong shape for aggregate questions.

### Honest limits on all of the above

One run per cell, seven questions, a non-deterministic model. The call
counts are within noise and should not be read as a result. The one
correctness difference is more trustworthy because the bench predicted
that exact question would separate the engines, but a single run is a
single run. Repeats would be the next thing, not more questions.

## The cheap fixes, measured

Two changes to the existing regex walk, about fifteen lines, no index,
no new dependency: fold combining marks on both sides before matching,
and match the title and tag *values* alongside the body.

| | recall@1 | recall@5 | MRR | p50 |
|---|---|---|---|---|
| regex (before) | 21% | 54% | 0.33 | 10.5 ms |
| regex + both fixes | **26%** | **64%** | **0.41** | 12.0 ms |
| fts5 + trigram | 62% | 72% | 0.65 | 1.3 ms |

By question kind, recall@1 / recall@5:

| kind | regex | regex+cheap |
|---|---|---|
| keyword | 17% / 67% | **50% / 100%** |
| compound_head | 75% / 75% | 75% / **100%** |
| scope | 0% / 67% | 0% / **100%** |
| everything else | unchanged | unchanged |

No class got worse. Most of the gain is the title, not the folding.

### Two corrections to this document's own gold set

**The `fold` class was never testing folding.** Adding a `regex+fold`
arm produced results byte-identical to plain regex, which is not what a
working fix looks like. The reason: all three questions turned on words
("TÜV", "Zählerstand", "Reisepässe") that appear *only in a page
title*, and the body-only engine cannot see titles however they are
spelled. Those are now filed as `frontmatter`, and four real
body-level fold questions replace them. The 0% to 100% jump this
document previously credited to diacritics belongs to indexing the
title.

**Folding works; the bench protocol hides it.** Directly on the corpus,
`Nachprufung`, `Burgerburo` and `Uberweisung` go from **zero hits to
the right page**. The bench misses that because it ORs every content
word of a question into one pattern, so a common word like "stand"
drags in dozens of pages and the date sort scatters the real hit. The
agent does not query that way: the search log says its median query is
**one keyword**, which is exactly the case folding rescues.

### Matching is not ranking

The pattern across every cheap fix: they improve what is *found*
without improving what is *surfaced*. `frontmatter` questions still
sit at 0% recall@1 even once titles are searchable, because the right
page is now in the results and sorted by date along with everything
else. Recall@5 moves; recall@1 mostly does not.

An early version of the frontmatter change also matched person names,
and that made things worse: one query went from four hits to twenty
and its answer from rank two to rank nine. A name says who a page
concerns, not what it says. `--person` already asks that question
properly. Persons stay out of the haystack.

## The query side

The search log from the repeated run is the most useful artefact this
work produced, because it says what the agent actually does rather than
what the skill asks of it. 161 searches:

- **The median query is 2-3 terms**, written as a regex alternation
  (`subscription|license|domain`). An earlier claim in this document
  that the agent "searches one keyword at a time" was an artefact of
  counting whitespace-separated words in a query that has no spaces.
- **`--nl` was used 0 times in 161 searches.** The keyword-rewrite
  path, a full LLM round trip and a documented feature, is one the
  agent has never reached for. It writes its own regex instead.
- **27% of searches came back empty**, and the empty ones skew English:
  German-ish queries dead-end around 16% of the time, non-German ones
  around 35%. The agent is asked a German question, answers in German,
  and searches a German vault in English.

### Telling the agent to use the right language did not work

Measured, three repeats per arm, dead-end rate straight from the search
log:

| | searches | dead ends | rate | calls |
|---|---|---|---|---|
| before | 83 | 27 | 33% | 115 |
| after | 74 | 23 | 31% | 101 |

German-ish queries moved 54% to 57%, and the same English dead ends
recurred. The instruction was unfollowable rather than ignored: it
asked the agent to search "in the language the family wrote the page
in" at the moment it writes its *first* query, when nothing has yet
told it what that language is.

### Giving it the fact instead

An empty result means one of two things and the caller must act
differently on each: the words were wrong, or the fact is not written
down. `no results` cannot tell them apart, so a bad guess either gives
up on a fact that is there or keeps rephrasing at one that is not.

`unmatched_terms` names the query words that appear nowhere in the
vault, and the CLI prints them on the empty path only:

```
$ memory search "repair|expense"
no results. These words appear nowhere in the vault: expense
```

`repair` is absent from that list because it matches the `repairs`
tag, which is only visible thanks to the frontmatter change above. The
two compound.

**Status: unverified at agent level.** The rig run was killed partway
through. The helper is unit-tested and checked by hand on both cases,
and it costs a second vault walk only on a search that already
returned nothing, so the successful path is unchanged. Whether it
actually cuts the dead-end rate is the run that still has to happen.

## Replaying the agent's own queries

Every number above rests on a gold set somebody wrote. This one does
not: it replays the 104 distinct queries the agent actually sent,
recovered from the lab-api search logs, against each version of the
engine. Reproduce with `tools/retrieval-lab/replay.py`.

Zero results is the right metric for these two fixes. Ranking decides
which page comes first; these decide whether there is a page at all,
and an agent can iterate past bad ordering but not past nothing.

| engine | queries returning nothing |
|---|---|
| old (body only, byte literal) | 32 / 104 (31%) |
| folding only | 32 / 104 (31%) |
| title and tags only | **25 / 104 (24%)** |
| both (shipped) | 25 / 104 (24%) |

**Folding contributes nothing on real agent queries.** All seven
rescues come from matching titles and tags. That is the fourth
attribution in this document that did not survive being measured, and
the pattern is always the same: a fix was credited by the class it was
*designed* for rather than by what moved.

Folding is not wrong, it is unexercised. The agent does not type German
without umlauts; it types English. `Kase` for `Käse` is a phone
keyboard and a human in a hurry, and no evidence here says a family
does that often. It stays because it is correct, cheap and tested, not
because it was shown to help.

The rescues also explain themselves once read: `repairs|expenses`,
`flight|travel|ticket` land on pages whose *tags* are English while
their prose is German. Matching tags accidentally bridges the language
gap that the skill instruction could not.

## What has not been measured

The agent has not run against this. Everything above is the engine in
isolation: whether ranked results and a coverage number actually cut the
agent's tool iterations, or change what it says when it finds nothing,
is a `tools/agent-lab/rig/` question and the next step. The handover's
scenarios 1-6 are written for that rig and still stand.

Also untested: the model's own keyword rewrite (this used mechanical
stopword removal so the engine was the only variable), cross-language
questions, and anything at real vault scale -- 177 pages is enough for
ranking to bite, not enough to say anything about a vault of thousands.
