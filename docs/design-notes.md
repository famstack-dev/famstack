# Design notes

Decisions and dead ends worth remembering. Surface them when adjacent code
is touched (see `docs/agent/dev.md`, Pre-1.0 conventions).

**This file is not a task list.** Anything actionable lives on the tracker
board, where it carries a verification gate and an owner. What stays here is
the material a card can't hold: why a shape was chosen, what was tried and
rejected, and which tensions are known but not yet resolved. If an entry
below ever grows a "do this next", move it to a card and leave the reasoning.

## Wiki freshness (curator shipped 2026-06-11)

The curator sidecar ships the first two freshness tiers: debounced
incremental rebuilds (persons + home) and the nightly full sweep. The
third tier (chat-triggered rebuild) is a card.

- **Realtime is NOT a requirement.** The mirror is realtime; the wiki
  is a derived view. The nightly sweep makes the incremental person
  mapping merely *helpful*, never load-bearing - worst case for a
  mapping miss is "stale until tonight". Don't grow the incremental
  heuristics; grow the deriver instead.
- **Page update strategy - unresolved, and the tension is known
  (2026-06-11):** full regeneration resamples page quality (a good page
  can regress on the next sweep); evolving the existing page accumulates
  errors that self-cite (the "Bartley [5]" finding). Most promising
  middle: a fact-checking pass - "page + sources, fix what the sources
  don't support, touch nothing else" - anchored to ground truth while
  preserving good prose. Likely CLI shape then: `wiki` = update/check,
  `wiki rebuild` = fresh full generation.
- **Rejected runtime homes, don't re-litigate:** host daemons (no launchd
  surface), quartz container (node image; "the wiki never writes"),
  bot-runner service concept (one consumer), bot-runner image reuse
  (the curator uses 2 of its 10 deps; slim image won).

## Both halves of a Matrix relation (thread corrections, 2026-08-02)

The archivist learned to *answer* in a thread (`_answer`, `REPLY_IN_THREAD`)
but kept *reading* one `m.in_reply_to` hop, so a correction typed in a
filing's thread was routed to search. A client's reply pointer inside a
thread is a falling-back rendering aid at the newest event there, not the
message the human aimed at, so the pointer stopped naming our filing the
moment anything followed it (a todo link, a status line).

The general shape, worth checking whenever a bot gains a new relation
type: **whoever teaches the sender a relation owns teaching the reader
the same one.** Half a relation is worse than none, because the send
side looks right in the client while routing silently degrades.

Also why it survived a release: the intent spec that would have caught it
(`tests/integration/test_room_modes_e2e.py`) is marked
`xfail(strict=False)`, which is green when broken and green when fixed.
A non-strict xfail is not coverage. Making it `strict=True`, skipping it
with a reason, or deleting it are all better; the action is FAM-6.

## Surviving upstream drift: the `wait_task` pattern

When Paperless-ngx 3.0 redesigned its task API, the fix that held up was
absorbing *both* response shapes in a single parser and covering both
offline: `PaperlessAPI.wait_task` (`stacklets/docs/bot/pipeline.py`) plus
`TestWaitTask` in `test_pipeline.py`.

Worth copying whenever an upstream service changes a contract. One parser,
both shapes, proved in the `unit` lane - it turns a version migration into
a contained task instead of a rewrite, and it means the old version keeps
working while the new one is evaluated.

The corollary is the reason it was needed: **an unpinned image is a
scheduled outage.** `:latest` plus watchtower rolled Paperless from 2.x to
3.0.2 unattended and broke filing across the whole e2e suite.
