# Cleanup backlog

Items land here with a reason. Surface them when adjacent code is touched
(see `docs/agent/dev.md`, Pre-1.0 conventions).

## Wiki freshness follow-ups (curator shipped 2026-06-11)

The curator sidecar ships the first two freshness tiers: debounced
incremental rebuilds (persons + home) and the nightly full sweep.
Design notes that survive it, for whoever touches this next:

- **Realtime is NOT a requirement.** The mirror is realtime; the wiki
  is a derived view. The nightly sweep makes the incremental person
  mapping merely *helpful*, never load-bearing — worst case for a
  mapping miss is "stale until tonight". Don't grow the incremental
  heuristics; grow the deriver instead.
- **Page update strategy — design when it's time, but the tension is
  known (2026-06-11):** full regeneration resamples page quality (a
  good page can regress on the next sweep); evolving the existing
  page accumulates errors that self-cite (the "Bartley [5]" finding).
  Most promising middle: a fact-checking pass — "page + sources, fix
  what the sources don't support, touch nothing else" — anchored to
  ground truth while preserving good prose. Likely CLI shape then:
  `wiki` = update/check, `wiki rebuild` = fresh full generation.
- **famstacker `wiki` command** (Server Room chat trigger) is the
  missing third tier: "CLI commands are the primitives, the bot runs
  or offers them". Needs the famstack API to allow the command and an
  ack-then-report shape for the multi-minute run.
- `{"cmd": "notify"}` for the famstack API (containers → Server Room
  via `stack messages send`) lives in the git stash
  ("wiki auto-rebuild: curator sidecar + API notify") — platform
  piece, ship it with whichever consumer arrives first; the curator's
  completion notice is a natural one.
- Rejected runtime homes, don't re-litigate: host daemons (no launchd
  surface), quartz container (node image; "the wiki never writes"),
  bot-runner service concept (one consumer), bot-runner image reuse
  (the curator uses 2 of its 10 deps; slim image won).

## Paperless 3.x upgrade (deferred to its own branch, 2026-07-24)

`:latest` + watchtower silently rolled Paperless-ngx from the 2.x line to
3.0.2 and broke document filing across the whole e2e suite. We pinned to
`2.20.15` (`stacklets/docs/docker-compose.yml`) so the current release
ships reproducibly; 3.x is a deliberate, separately-branched migration.
What we learned, for whoever does the 3.x branch:

- **Already made resilient (don't redo):** `PaperlessAPI.wait_task`
  (`stacklets/docs/bot/pipeline.py`) now parses *both* the 2.x and 3.0
  `/api/tasks/` shapes. 3.0 redesigned the task system (upstream #12584)
  and paginated the listing (#12633): the response became a
  `{"count", "results": [...]}` envelope (was a bare list), `status`
  lowercased (`SUCCESS`→`success`), and the filed-doc reference moved
  from scalar `related_document` to `related_document_ids` +
  `result_data.document_id`. `_task_document_id` + the envelope-unwrap
  handle all of it, covered by `TestWaitTask` in `test_pipeline.py`.
- **Still to verify on 3.x (not yet exercised against 3.0):**
  - **Notes endpoint** (`add_note`/get/delete, `pipeline.py`) — 3.0
    "reject invalid requests to API notes endpoint" (#12582). The e2e
    summary-note assertion passed on a transient 3.0.2 rig, so it's
    probably fine, but confirm the POST body is still accepted.
  - **Duplicate detection** — 3.0 moved checksums to SHA256 (#12432).
    `PaperlessDuplicateError` keys off the rejection message; confirm the
    message text/shape still matches `_DUPLICATE_RE`.
  - **Permissions/owner scoping** tightened in 3.0; confirm the bot
    token still reads/writes everything it needs (uploads carry
    `owner_id`, so owner-filtered list endpoints may hide docs).
  - Not affected (checked): we send no API-version header and use no
    `=all` result expansion, both removed in 3.0.
- **One-way door — no downgrade.** Paperless runs DB migrations on
  first 3.0 start and 2.x refuses to boot on a migrated DB. So: the 3.x
  branch can't be tested by flipping the tag on an existing instance —
  it needs a fresh docs volume. And the eventual **upgrade note must warn
  users**: anyone already auto-bumped to 3.0 by watchtower cannot pin
  back to 2.20.15 without restoring a pre-3.0 backup.
- **Adjacent:** `apache/tika:latest` (same compose file, line ~109) is
  also unpinned — same silent-drift risk. Pin it when you touch this.

## CI release gate (post-0.3)

## CI release gate (post-0.3)

The pre-tag gate in `docs/agent/dev.md` is manual; v0.3.0-beta.1 shipped
with a stale `uv.lock` and a wrong `VERSION` string because of it. Move it
to CI in tiers:

1. **Tag-triggered GitHub Action (cheap, do before the next tag):**
   version consistency (`lib/stack/cli.py` VERSION == `pyproject.toml` ==
   tag name), `uv lock --check`, ruff, framework + stacklet unit tests.
   Linux runner, no Docker needed.
2. **Integration suite in CI:** needs Docker + Synapse + Forgejo + the
   OpenAI stub, ~35 min wall clock, assumes repo-root-as-instance. Real
   work, decide after 0.3.0 final.
3. **Fresh-install + ai stacklet:** macOS/Apple Silicon only — needs a
   self-hosted runner on the Mac Studio. Own project, don't start it
   casually.
