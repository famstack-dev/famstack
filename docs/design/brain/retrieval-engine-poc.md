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

1. **Build tier 1 as FTS5 + trigram, fused with RRF**, not FTS5 alone.
   The trigram table moves from stretch goal to first slice, because
   without it the change is a regression for German compounds.
2. **Ship a confidence signal with the results, and make it coverage**,
   not the score. `Hit.matched` already carries it.
3. **Tier 2 (embeddings) stays open**, with paraphrase recall at 29% as
   the number it has to beat.
4. **Decide whether transliterated umlauts matter** before building for
   them.

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
