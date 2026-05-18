# Family Brain. Phase 2: consolidation and LLM Wiki adoption

> Target release: 0.3.0 (continuation, same branch)
> Branch: `feat/brain-base`
> Status: Phase 1 shipped, this doc plans Phase 2 in place
> Sibling docs:
>   - [plan.md](plan.md): Phase 1 (what shipped)
>   - [knowledge-architecture.md](knowledge-architecture.md): original vision
>   - [knowledge-structure.md](knowledge-structure.md): five-layer model

## Why this phase exists

Phase 1 shipped the foundations: a `memory` stacklet backed by Forgejo, an
ontology, a correspondents wiki layer, a capture pipeline for URL and
text pastes, and a per-document briefing block. It works. It also
left us with **three coordinating git repos** (`memory.git`,
`documents.git`, plus the original Paperless data store), several
custom subsystems we'd planned to extend (wiki rebuild, alias merging,
person rollup), and an HTTP-API write path for git content that's
slower and stranger than the obvious alternative.

While Phase 1 was being built, Andrej Karpathy published the **LLM Wiki**
pattern (April 2026). It crystallized the exact approach we'd been
groping toward: treat your notes as raw source material, let an LLM
compile them into a structured, interlinked Markdown wiki, keep
hand-edits sacred. The pattern is now well-implemented in
[`obsidian-llm-wiki-local`](https://github.com/kytmanov/obsidian-llm-wiki-local)
(MIT, Python, 620 stars, 9k+ downloads) and its successor
[`synto`](https://github.com/kytmanov/synto).

Phase 2 takes the pragmatic move: **adopt the existing implementation,
consolidate to one repo, drop the custom rebuild plumbing we were
about to write**. The famstack-specific work then sits where it
actually adds value (the capture pipeline, the ontology, the
correspondents seed) and the wiki maintenance becomes someone else's
mature problem.

## Principles for this phase

1. **One knowledge repo.** `family/memory.git` holds everything. No
   `documents.git`. No `captures.git`. One source of truth, one clone,
   one push target.
2. **Plain git, not HTTP.** Writes go through `git` subprocess on a
   local working copy. ForgejoClient stays for what only it can do
   (create repo, manage users/teams, issue tokens).
3. **Adopt, don't reinvent.** `obsidian-llm-wiki-local` (hereafter
   `olw`) is the wiki engine. Accept its conventions for the wiki
   layer. Our value sits in the raw-content pipeline (archivist,
   classifier, briefing block, ontology) and the family-specific
   integration.
4. **Defer chat-based editing.** Users edit in Obsidian or Forgejo web
   UI. Matrix-driven wiki edits can come later if there's demand.
5. **No review queues.** Auto-publish, auto-merge, no admin work. The
   git log is the audit trail; the user fixes wrong stuff with an
   edit, not an approval.

## End-state architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        family/memory.git                             │
│                    (the one knowledge repo)                          │
│                                                                      │
│   ontology.toml                   ours (classifier seed)             │
│   wiki.toml                       olw config                         │
│                                                                      │
│   correspondents/                 ours, hand-curated                 │
│     adac.md                       canonical + aliases + facts        │
│     aok.md                                                           │
│                                                                      │
│   raw/                            ← archivist writes here            │
│     2026/05/                                                         │
│       2026-05-15-adac-rechnung-p247.md   classified document         │
│       2026-05-17-reddit-llms-a7b3c2.md   capture (bookmark/note)     │
│       ...                                                            │
│                                                                      │
│   wiki/                           ← olw writes here                  │
│     ADAC.md                       olw-generated concept page         │
│     LLMs.md                                                          │
│     ...                                                              │
│                                                                      │
│   .olw/                           olw state                          │
└──────────────────────────────────────────────────────────────────────┘
       ▲                                       ▲
       │ git push                              │ git push
       │                                       │
  ┌────┴─────────────┐               ┌─────────┴──────────┐
  │ archivist bot    │               │ memory stacklet    │
  │  writes raw/     │               │  runs olw          │
  └──────────────────┘               └────────────────────┘
       ▲                                       ▲
       │ Matrix events                         │ stack memory rebuild
       │ (PDFs, URLs, pastes)                  │ (cron + on-demand)
       │                                       │
  ┌────┴───────────────────────────────────────┴─────────┐
  │                    the family                         │
  └───────────────────────────────────────────────────────┘
```

**Three things to notice:**

1. Paperless still exists outside this diagram. It remains the
   document database (OCR, full-text search, the web UI for browsing
   PDFs). The archivist files PDFs in Paperless first, then mirrors
   the classified Markdown into `memory.git/raw/`.
2. The archivist and the memory stacklet both write to the same repo
   on the same machine. Coordination is plain git: `pull` before
   write, push after. Race conditions are theoretically possible but
   practically rare (different paths, different commit cadences).
3. Olw watches `raw/` and writes `wiki/`. It treats every Markdown
   file in `raw/` as a note to ingest. Documents and captures look
   identical from olw's perspective: both are dated, classified,
   frontmatter-rich Markdown.

## What changes from Phase 1

### A. Drop `family/documents.git`

The repo was a beta-testing artifact. Phase 2 consolidates its content
into `memory.git/raw/`. No migration story needed (no production
data), no separate README, no second clone path.

**Code changes:**

- `stacklets/docs/bot/git_mirror.py`:
  - `REPO_NAME = "documents"` → `REPO_NAME = "memory"` (or read from
    config so we can flip)
  - `_filepath(...)` returns `raw/YYYY/MM/<slug>-p<id>.md` instead of
    `YYYY/MM/<slug>-p<id>.md`
  - `_capture_filepath(...)` returns `raw/YYYY/MM/<slug>-<hash>.md`
    instead of `captures/YYYY/MM/<slug>-<hash>.md`
  - README in the repo is no longer set up by `_ensure_readme`; the
    memory stacklet owns that.
- `stacklets/memory/`:
  - Repo setup teaches the memory stacklet to expect `raw/`, `wiki/`,
    `.olw/` subtrees in `memory.git`.
  - The README written into `memory.git` describes the unified vault.

### B. Local git for writes

GitMirror currently calls `ForgejoClient.put_file(...)` for every
mirror file write. One HTTP round-trip per file, sequential.

Replace with:

```
git -C <vault> pull --rebase  # before writes
# write file directly into the working copy
git -C <vault> add raw/2026/05/foo.md
git -C <vault> -c user.name=... -c user.email=... commit -m "..."
git -C <vault> push origin main  # batch-pushable
```

ForgejoClient stays for:
- `ensure_setup`: creating the repo, org, team membership, bot user,
  token
- Resolving the push URL with the bot token baked in (already used in
  `memory.lib.authenticated_remote()`)

**Code changes:**

- `stacklets/docs/bot/git_mirror.py`:
  - `publish` and `publish_capture` switch to subprocess-git writes
    on a local working clone instead of `ForgejoClient.put_file`
  - The bot mounts/clones `memory.git` into the archivist's data
    directory at first run; subsequent runs `git pull` to refresh
  - The cache (`paperless_id → path`) becomes a local filesystem walk
    against the clone, not a tree-listing API call
- `stacklets/memory/lib.py`:
  - `ensure_vault_cloned()` already exists; archivist reuses it
  - Add `commit_and_push(files, message)` helper that wraps the
    subprocess git calls with sane env (gitconfig user, no-edit, etc.)

### C. Adopt `obsidian-llm-wiki-local` as the wiki engine, run in Docker

`obsidian-llm-wiki` (hereafter `olw`) requires Python 3.11+. famstack
targets 3.9. Rather than bump our Python floor for the whole stack,
we run olw in its own container, which also matches the pattern other
service stacklets already use.

The memory stacklet flips from `type = "host"` to a service-type
stacklet with one container.

**Container:**

- Custom image built from the `stacklets/memory/Dockerfile` (same
  pattern as `stacklets/core/bot-runner/Dockerfile`).
- Base: `python:3.11-slim`. Single `pip install obsidian-llm-wiki==<pinned>`.
- ~10-line Dockerfile, pinned version, our build pipeline.
- Volume mount: `${DATA_DIR}/memory/vault → /vault` (the shared
  knowledge repo's working copy).
- Default mode: `olw watch /vault`. Watcher fires on `.md` writes
  under `/vault/raw/`, debounces, runs ingest + compile + auto-commit.
- Alternative mode (`mode = "on_demand"` in stacklet settings): the
  container runs `sleep infinity`, real work happens via
  `docker exec memory olw compile` invoked by the CLI.

**Stacklet wiring:**

- `stacklet.toml`:
  - `type = "service"` (was "host")
  - `requires = ["code", "ai"]` so olw can reach oMLX and Forgejo
  - `[env.defaults]` templates `OLW_PROVIDER_URL` from the ai
    stacklet's service hostname (`http://omlx:<port>/v1`)
- `wiki.toml` seeded into the vault on first install, with the
  `[provider]` block pointing at oMLX. Uses the OpenAI-compatible
  endpoint (verified path: olw's `config.py` emits `[provider]`
  blocks for non-Ollama backends).

**Path layout in the vault (with olw's hard-coded conventions):**

```
<vault>/
  wiki.toml                  ← olw config (provider, models, pipeline)
  ontology.toml              ← ours (classifier seed)
  correspondents/            ← ours, OUTSIDE wiki/ (see below)
    README.md
    adac.md
    aok.md
    ...
  raw/                       ← olw reads (recursive .md glob)
    2026/05/...              ← archivist writes here from bot-runner
  wiki/                      ← olw writes (concept articles)
    ADAC.md                  ← olw-generated concept page
    LLMs.md
    ...
  .olw/                      ← olw state (sqlite, content hashes)
```

**Why `correspondents/` lives outside `wiki/`:**

Olw has no `exclude`/`ignore` config option. Verified by reading the
upstream source:

- Watcher: `observer.schedule(handler, str(config.raw_dir),
  recursive=True)`, scoped to `raw/` only.
- Ingest: `config.raw_dir.rglob("*.md")`, scans only `raw/`.
- Auto-commit: hard-coded `["wiki/", "raw/", "vault-schema.md",
  ".olw/"]`.

So olw's reach is strictly `raw/` → `wiki/` + `.olw/`. Anything at
the vault root or in other subtrees is invisible to it.

Moving correspondents from `wiki/correspondents/` to `correspondents/`
keeps it out of olw's reach without needing an exclude feature.
Obsidian's wikilink resolution is name-based, not path-based, so
`[[ADAC]]` still works as a cross-reference between `correspondents/adac.md`
and the olw-generated `wiki/ADAC.md`.

Trade-off: two ADAC pages exist (`correspondents/adac.md` machine-
readable + `wiki/ADAC.md` LLM-summarized). Different purposes, both
discoverable in Obsidian's quick switcher. Optionally link them
explicitly: `> See also: [[ADAC]]` at the top of `correspondents/adac.md`.

**Two writers, one repo, coordinated via git:**

- Archivist (bot-runner container) writes `raw/YYYY/MM/*.md` and
  commits with no `[olw]` prefix.
- Olw (memory container) writes `wiki/*.md` + `.olw/*` and commits
  with `[olw]` prefix.
- Both `git pull --rebase` before pushing to `origin/main`, retry
  once on non-fast-forward.
- Conflicts are practically impossible: disjoint path sets, different
  commit cadences (archivist per-capture, olw per-debounce-cycle).
  The retry covers the theoretical case.

**CLI dispatch (host versus container):**

| Command | Where it runs |
|---|---|
| `stack memory pull` | host (git pull on the vault) |
| `stack memory lookup`, `correspondents`, `prompt` | host (reads TOML/MD directly) |
| `stack memory rebuild` | `docker exec memory olw compile` |
| `stack memory rebuild --watch` | `docker restart memory` into watch mode |
| `stack memory query "..."` | `docker exec memory olw query` |
| `stack memory lint` | `docker exec memory olw lint` |
| `stack memory maintain --fix` | `docker exec memory olw maintain --fix` |

Existing CLI (`stack memory pull`, `lookup`, `prompt`, `correspondents`)
keeps working on the host side, unchanged.

**Implementation:**

- `stacklets/memory/Dockerfile` (new): `FROM python:3.11-slim` + pip install
- `stacklets/memory/entrypoint.sh` (new): handles watch vs. on-demand
- `stacklets/memory/docker-compose.yml.j2` (new): one service, vault volume
- `stacklets/memory/stacklet.toml`: switch to `type = "service"`, add env
- `stacklets/memory/seeds/wiki.toml` (new): olw config template, oMLX endpoint
- `stacklets/memory/seeds/correspondents/README.md` (moved from
  `seeds/wiki/correspondents/`)
- `stacklets/memory/lib.py`: `load_correspondents_from_vault` reads
  from `vault/correspondents/` (was `vault/wiki/correspondents/`)
- `stacklets/memory/cli/rebuild.py` (new): wraps `docker exec`
- `stacklets/memory/cli/query.py` (new): wraps `docker exec`
- `stacklets/memory/cli/lint.py` (new): wraps `docker exec`

### D. Path layout in `raw/`

Flat by date:

```
raw/2026/05/2026-05-15-adac-rechnung-p247.md   document
raw/2026/05/2026-05-17-reddit-llms-a7b3c2.md   capture
raw/2026/05/2026-05-17-pasted-note-d8e9f.md    capture (note kind)
```

Frontmatter discriminates document vs capture (presence of
`paperless_id`, value of `kind`, etc.). Olw doesn't care.

Rejected alternative: `raw/documents/` + `raw/captures/` subtrees.
Adds path-level structure for marginal benefit; Obsidian's
filename-search and Dataview frontmatter-search both ignore the
distinction anyway.

### E. What the bot writes per source kind

| Source | Frontmatter `kind` | Path | Body |
|---|---|---|---|
| Photo → Paperless → OCR + classify | (no `kind` field, has `paperless_id`) | `raw/YYYY/MM/YYYY-MM-DD-<slug>-p<id>.md` | OCR text or LLM-reformatted markdown |
| URL paste (capture mode) | `bookmark` | `raw/YYYY/MM/<slug>-<hash>.md` | empty by default, full body if `capture_keep_body = true` |
| Text paste (capture mode) | `note` | `raw/YYYY/MM/<slug>-<hash>.md` | the pasted body verbatim |

All three carry a briefing block (`## Summary` / `## Facts` /
`## Action items`) between the H1 and any body content. Olw ingests
the whole file and extracts concepts.

## Out of scope for Phase 2

These were on the Phase 1 → Phase 2 wishlist; deferring them
explicitly:

- **Chat-based wiki edits.** `stack memory edit <page>` over Matrix
  is interesting but not a v0.3.0 requirement.
- **Custom structured rollup pages.** Person pages (`wiki/persons/<name>.md`)
  and correspondent rollup pages with hand-curated layouts. Olw
  generates flat concept pages; Obsidian's graph view and Dataview
  serve the cross-referencing need.
- **Tag canonicalization for captures.** Olw's compile does alias
  merging across concept extractions. If capture-specific tag
  canonicalization is still needed after a month of usage, revisit.
- **Per-person interest derivation.** Dataview queries against
  `raw/` + `wiki/` answer "what is Arthur reading lately?" without
  new infrastructure.
- **A second writer for the brain wiki** beyond olw (e.g., an
  archivist-driven wiki-update on every document). Single-writer keeps
  conflicts simple; coordinating two writers is its own design.

## Build order

Each step is shippable in isolation and leaves the system in a working
state.

> **Step B (local git writes) dropped.** It was a refactor disguised
> as a step. Forgejo's HTTP contents API already serialises commits
> server-side; archivist HTTP PUTs and olw's `git push` from its
> container will coordinate fine without making the bot-runner carry
> a working copy. Revisit only if HTTP writes prove painful in
> practice.

1. **A. Consolidate into `family/memory.git`** (half day)
   - `REPO_NAME = "memory"` in the archivist's git mirror
   - Paths prefixed with `raw/` (`raw/YYYY/MM/...` for both documents
     and captures, `raw/_unfiled/...` when undated)
   - README written by memory stacklet (seeds) describes unified vault
   - `correspondents/` moves out of `wiki/` to vault root
   - Tests adjusted for new paths
   - Smoke: archivist writes to `memory.git/raw/`, no documents.git in use

2. **C. Wire olw container** (2-3 days)
   - `stacklets/memory/Dockerfile` building `python:3.11-slim` + pinned olw
   - `docker-compose.yml.j2` mounts the vault, default mode `olw watch`
   - `stacklet.toml` flips to `type = "service"`, declares
     `requires = ["code", "ai"]`
   - `wiki.toml` template seeded into the vault, `[provider]` points
     at the ai stacklet's oMLX endpoint via stack networking
   - CLI commands `rebuild`, `query`, `lint`, `maintain` wrap
     `docker exec memory olw …`
   - Tests:
     - host-side CLI tests assert correct `docker exec` invocation
     - container smoke test (integration tier): bring memory up,
       drop a `.md` into `raw/`, watch a `wiki/*.md` appear, assert
       `[olw]` commit in `git log`
   - Smoke: paste an article in a capture room → mirror in `raw/` →
     olw watcher fires → `wiki/<Concept>.md` lands within debounce
     window (default 3 seconds) and gets committed

4. **D. (Optional for 0.3.0) On-demand mode + rebuild on schedule**
   - Setting `mode = "on_demand"` makes the container sleep and
     waits for explicit `stack memory rebuild` invocations
   - Host-type cron hook to schedule periodic rebuilds in on-demand
     mode (daily at 03:00 by default, configurable)
   - `--dry-run` flag on `rebuild` for inspection
   - Lower priority: watch mode covers the default user; on-demand
     is for constrained deploys

## Resolved decisions

1. **Python version.** Olw requires Python 3.11. We don't bump
   famstack's floor; we run olw in a Python 3.11 container.
2. **oMLX integration.** Automatic. Memory stacklet `requires =
   ["ai"]`, container env templates `OLW_PROVIDER_URL` from the ai
   stacklet's service hostname.
3. **Watch mode is default.** Container runs `olw watch /vault`
   continuously. Idle until files change. On-demand is opt-in for
   resource-constrained deploys.
4. **Image strategy.** We build our own. `stacklets/memory/Dockerfile`,
   pinned olw version, our build pipeline. Same pattern as bot-runner.
5. **Path exclusion.** Olw has no exclude config (verified by reading
   `watcher.py` and `pipeline/ingest.py`). Resolved by layout:
   `correspondents/` lives at the vault root, outside `wiki/`. Olw
   only reads `raw/`, only writes `wiki/`+`.olw/`, only commits
   `["wiki/", "raw/", "vault-schema.md", ".olw/"]`. Everything else
   is invisible.
6. **Bot user, two writers.** Reuse `memory-bot` for both archivist
   and olw container commits. Archivist commits have no prefix, olw
   commits carry `[olw]`; easy to filter in `git log --grep`.
7. **Synto versus olw.** Use olw for 0.3.0. Stable, MIT, 620 stars,
   v0.8.5, dependencies are light (click, rich, pydantic, pyyaml,
   httpx, watchdog, python-frontmatter). Pin a version, upgrade
   deliberately. Synto stays on the watchlist for when its
   "distributable knowledge packs" feature becomes useful (shipping
   seed wikis to new families).

## Open questions

1. **Pin which olw version.** Latest at time of writing is `0.8.5`.
   Pin in the Dockerfile; bump deliberately when we want a feature
   or a fix. Pre-flight check: run our smoke against the pinned
   version before shipping.
2. **Container update strategy.** Watchtower (already in famstack)
   handles container image updates. Since we build our own image
   with pinned olw, Watchtower won't auto-bump olw, which is good:
   stays under our control. Our `[upstream]` block in
   `stacklet.toml` declares the base image (`python:3.11-slim`),
   not olw itself.
3. **Hand-edit commits for files outside olw's reach.** When a user
   edits `correspondents/adac.md` or `ontology.toml` in Obsidian,
   no automatic commit happens (archivist doesn't touch those paths,
   olw doesn't see them). For 0.3.0: users commit + push manually
   (Obsidian Git plugin or terminal). Follow-up: a host-side cron
   that commits dirty changes to `correspondents/` + `ontology.toml`
   on a schedule.

## Success criteria

Phase 2 is done when:

- `family/documents.git` has been removed; only `family/memory.git`
  exists
- The archivist files documents and captures into `memory.git/raw/`
  via local git
- `stack memory rebuild` populates `memory.git/wiki/` from `raw/`
- A pasted URL in a capture room results in a `raw/` mirror plus
  one or more `wiki/<Concept>.md` pages on the next rebuild
- `stack memory query "what is X?"` answers from the wiki
- Existing tests pass; new path/git tests cover the refactor
- The 0.3.0 preview recap doc is updated to reflect the consolidated
  shape

## Notes for the blog post (later)

The consolidation story has a strong narrative arc:

1. We built a custom wiki rebuild plan
2. Discovered Karpathy had crystallized the pattern publicly two
   months earlier
3. Found a mature Python implementation that does the heavy lifting
4. Threw away our own rebuild code and adopted theirs
5. The result is smaller, more battle-tested, and the famstack
   value sits where it always belonged: in the capture pipeline,
   the classifier, and the family-specific integration

"We built less and got more" is the headline.
