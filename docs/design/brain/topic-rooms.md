# Topic Rooms

> Status: Design extension to [knowledge-architecture.md](knowledge-architecture.md) and [family-memory.md](family-memory.md)
> Created: 2026-06-09
> Author: Arthur + Claude
> Depends on: memory vault structure ([family-memory.md](family-memory.md)), the `dev.famstack.capture` room state contract ([knowledge-structure.md](knowledge-structure.md) §Room configuration), the archivist's capture routing pipeline (`stacklets/docs/bot/capture_pipeline.py`).

## Why this exists

Tag-based topic membership is statistical. The classifier might emit `[camping, gear]` for one capture and `[travel, packing]` for the next, and finding "everything we noted about camping" becomes a fuzzy join across synonyms and language variants. The single-generic-tag failure mode that motivated the `feat/transcribe-capability` prompt bias (one note tagged only `Travel`) was the symptom. Tightening the prompt helps; it does not make retrieval deterministic.

Room-based topic membership *is* deterministic. A message posted in `#Thema: Camping` belongs to camping by virtue of having been posted there. No LLM call, no synonym sweep, no retrieval miss. The room is the source of truth.

This document extends the memory vault to support **topic folders** at the top level alongside the existing privacy buckets, and binds Matrix rooms to those folders via a naming convention. The room IS the topic's identity, its routing source, and its discovery surface.

## The contract

A Matrix room whose name starts with `Thema:` or `Topic:` is a topic room. The archivist binds it to a folder in the memory vault, routes every capture there, and seeds every capture with the topic's tag.

| Property | Guarantee |
|---|---|
| **Routing** | Captures in `#Thema: Camping` always file under `family/camping/` (shared) or `arthur/camping/` (personal), regardless of the classifier's opinion. |
| **Tag invariant** | Every file in the topic folder has `camping` in its frontmatter `topics:`. The classifier may add tags; it can never remove this one. |
| **Discoverability** | The room list is the topic list. Family members find topics by browsing joined rooms — no separate index UI. |

Topic rooms compose with the existing capture pipeline. The classifier, mirror, search, deriver, ontology-canonicalizer — none of them special-case topic folders. They see a bucket like any other.

**Topics always nest inside the bucket that owns them.** Shared topics live under the household's configured shared bucket (`family/<slug>/`, or `office/<slug>/` for deskstack); personal topics live under the originating person's bucket (`arthur/<slug>/`). The top level of the vault stays pure access-scope: one folder per privacy boundary, never a topic folder. This makes a default sender-scoped search (`["family/", "<localpart>/"]`) automatically include shared-topic content — a family member asking "what did we note about camping?" in #documents finds it without knowing the topic room exists.

## Naming convention

| Room name | Slug | Bucket path (shared) | Bucket path (personal) | Guaranteed tag |
|---|---|---|---|---|
| `Thema: Camping` | `camping` | `family/camping/` | `arthur/camping/` | `camping` |
| `Topic: Photography` | `photography` | `family/photography/` | `arthur/photography/` | `photography` |
| `Thema: Van Life` | `van-life` | `family/van-life/` | `arthur/van-life/` | `van-life` |
| `Topic: 3D printing` | `3d-printing` | `family/3d-printing/` | `arthur/3d-printing/` | `3d-printing` |
| `Thema: Café Hopping` | `cafe-hopping` | `family/cafe-hopping/` | `arthur/cafe-hopping/` | `cafe-hopping` |

(Shared-bucket paths show `family/` because that is the default `[core] shared_bucket`. A deskstack household with `shared_bucket = "office"` gets `office/<slug>/`.)

### Parser rules

- **Prefix recognition.** Match `^\s*(thema|topic)\s*:\s*` case-insensitively. Both languages always accepted, regardless of `[core] language`.
- **Slug derivation.** NFD-normalize, strip combining marks (`Vélo` → `velo`, `Café` → `cafe`), lowercase, replace runs of non-alphanumeric with `-`, strip leading and trailing `-`. Maximum 40 characters; collisions at the tail receive a numeric suffix during bootstrap.
- **Empty after prefix.** `Thema:` with no body fails the parser — not a topic room. The archivist treats it as a regular room.
- **Reserved slugs.** Refused at bootstrap: within-bucket reserved directory names. The mirror writes `notes/`, `bookmarks/`, `documents/` per bucket and the shared bucket also carries `correspondents/` and `_unfiled/`; the derived per-bucket landing page is `about.md`. The reserved set is therefore `{notes, bookmarks, documents, correspondents, _unfiled, about}`. Top-level vault names (`family`, `arthur`, `marge`, `meta`, `wiki`, `archive`) no longer need to be reserved because topics never live at the top level.

### Slug stability

Once bootstrapped, the slug is the bucket path on disk and the value of `topics:` in every file beneath it. Renaming the Matrix room changes the display label (recorded in `about.md` frontmatter), never the slug. Re-slugging requires explicit operator action (`stack memory topic rename`), which does a `git mv` and rewrites `topics:` in every affected file.

## Room state schema

The archivist writes `dev.famstack.capture` room state on first detection and reads it on every event. The schema extends [knowledge-structure.md §Room configuration]:

```json
{
  "type": "dev.famstack.capture",
  "content": {
    "kind": "topic",
    "bucket": "family/camping",
    "slug": "camping",
    "display_name": "Camping",
    "default_topics": ["camping"],
    "scope": "shared",
    "extract_knowledge": true,
    "bootstrapped_at": "2026-06-09T12:00:00Z",
    "bootstrapped_by": "@arthur:home"
  }
}
```

| Field | Purpose |
|---|---|
| `kind` | `"topic"` distinguishes from existing room kinds (`"capture"`, `"document_drop"`). Future kinds may follow. |
| `bucket` | Vault path. Shared topic: `<shared_bucket>/<slug>`. Personal topic: `<sender_localpart>/<slug>`. Topics never live at the vault root. |
| `slug` | The tag value applied to every capture from this room. |
| `display_name` | Room name with prefix stripped. Used in `about.md` and CLI listings. |
| `default_topics` | List with one element (the slug). Future-proofed for topics that might seed multiple tags. |
| `scope` | `"shared"` or `"personal"`. Drives the promotion handler. |
| `extract_knowledge` | Inherited from base contract; defaults to `true` for topic rooms. |

Room state is the **source of truth** for routing. The archivist re-parses the room name on rename to update `display_name`, but never re-derives `slug` or `bucket` from the name post-bootstrap.

## Lifecycle

### Bootstrap (room → topic folder)

When the archivist joins (or finds itself already in) a room whose name matches the prefix pattern:

1. Parse the name. Derive the candidate slug.
2. Look up existing `dev.famstack.capture` room state. If present and `kind == "topic"`, the room is already bootstrapped; skip to step 7.
3. Check the candidate slug against reserved names and existing top-level vault folders. On collision: post a one-line correction prompt asking for rename. Do not bootstrap.
4. Detect scope by member count at the moment of bootstrap. One human (the sender, plus the archivist) → `personal`, bucket = `<sender_localpart>/<slug>/`. Two or more humans → `shared`, bucket = `<shared_bucket>/<slug>/`.
5. Write the room state per the schema above.
6. Create the topic folder in the memory repo with the scaffold (see §about.md scaffold).
7. Post a one-line confirmation: `📁 Topic '<display>' is set up. Send notes, voice memos, links, or scans; everything files under <bucket>/.`

Bootstrap is idempotent. Re-joining a room with existing state is a no-op.

### Use

Every capture in the room flows through the standard pipeline with three additions:

1. **Bucket override.** Routing uses `room_state.bucket` instead of the sender-localpart-derived default. The submitter still ends up in `persons:` via the existing fallback path.
2. **Tag seed.** The archivist passes `seed_topics = list(default_topics)` into the capture pipeline. The pipeline applies the seed to the mirrored file's frontmatter before the classifier runs, and merges classifier output additively (seed first, deduplicated).
3. **Classifier guidance.** The capture prompt includes a line: `This capture is filed under the '<display>' topic; the '<slug>' tag is already applied. Add 2-4 content-specific tags that further describe this capture.` The existing minimum-3 rule becomes minimum-3 *including* the seed, so the LLM emits two to four additions on top of the guaranteed seed.

### Rename

Matrix room name change event arrives. The archivist re-parses, updates `display_name` in room state and in `about.md` frontmatter. Slug, bucket, and the tag invariant remain untouched. The archivist posts: `📁 Display renamed to '<new>'. Folder stays <bucket>/.`

### Archive

The archivist is kicked, or all humans leave the room. The archivist:

1. Sets `status: archived` and `archived_at` in `about.md` frontmatter.
2. Removes the topic from the active list in the (eventual) master index.
3. Folder, git history, and cross-references stay forever.

Re-joining the room or re-inviting the bot restores `status: active`. Archive destroys no data.

### Personal → shared promotion

A personal topic (`arthur/camping/`) receives a second human member. The archivist:

1. Debounces ~10 seconds, in case the invite comes in a batch.
2. Verifies the new joiner is a human (a known Matrix user, not another bot).
3. Re-detects scope: `personal` → `shared`.
4. `git mv arthur/camping/ family/camping/` (full history preserved through the rename — bucket-to-bucket move within the vault).
5. Rewrites `bucket: family/camping` and `scope: shared` in the room state.
6. The wiki command regenerates `family/camping/about.md` on its next run with the new scope and participant list.
7. Posts: `📁 Topic promoted to shared. Files now live under family/camping/.`
8. On the next deriver pass, cross-references in other buckets' indexes are rewritten to point at the new path.

### Personal → shared promotion: edge cases

| Case | Behavior |
|---|---|
| Promotion would collide with existing shared topic (`family/camping/` already exists) | Refuse promotion. Post a correction prompt. Leave the personal folder where it is. The room remains personal-scoped even though it has multiple humans (a known degraded state, visible in `stack memory topic list`). |
| Second human leaves during the debounce window | Cancel promotion. Topic stays personal. |
| Multiple new humans join in one batch | One promotion, not many. The debounce coalesces. |

### Demotion

Reverse (shared → personal) is **not automatic**. Once shared, the topic stays shared even if all but one human leaves. Demotion requires `stack memory topic demote <slug>`. This avoids ping-pong on transient invite/kick activity and keeps shared-vault content stable.

### Bootstrap edge cases

| Case | Behavior |
|---|---|
| Room created with multiple humans from the start | Bootstrap detects scope as `shared`. No personal phase. |
| Slug collides with reserved name (`family`, `meta`, ...) | Refuse bootstrap. Post correction prompt: `Topic name conflicts with reserved name '<x>'. Rename the room.` |
| Slug collides with existing personal bucket (`arthur` matches a household member) | Refuse bootstrap. |
| Two archivists in the same room (multi-instance dev) | Both treat room state as truth. Whichever wrote it first wins. Re-bootstrap is a no-op. |
| Room renamed to no longer match the prefix | Topic state stays. Folder, captures, and tag invariant continue working. The room becomes invisible as a topic in the room-list discovery surface; future captures still file under the original bucket. (Operator can run `stack memory topic archive` to finalize.) |
| `Thema: Topic: Camping` (both prefixes) | Parser strips the outer prefix once. Display name becomes `Topic: Camping`. Slug derives normally. |

## File layout

Topic folders nest inside the bucket that owns them — shared under `<shared_bucket>/`, personal under `<localpart>/`. The top level of the vault stays purely access-scope (one folder per privacy boundary), with no thematic folders at all.

```
memory.git
├── family/                       (shared bucket — institutional + thematic)
│   ├── documents/                (existing institutional artifacts)
│   ├── correspondents/
│   ├── _unfiled/
│   ├── camping/                  (shared topic, bootstrapped from #Thema: Camping)
│   │   ├── about.md              (derived by the wiki command)
│   │   ├── notes/
│   │   │   ├── YYYY/MM/<slug>-<hash>.md
│   │   │   └── _unfiled/...
│   │   ├── bookmarks/
│   │   │   ├── YYYY/MM/<slug>-<hash>.md
│   │   │   └── _unfiled/...
│   │   └── documents/            (when documents are captured in the topic room)
│   │       └── YYYY/MM/...
│   └── photography/              (another shared topic)
│       └── ...
├── arthur/                       (personal bucket)
│   ├── notes/, bookmarks/, documents/, _unfiled/
│   └── gravel/                   (personal topic, nested under personal bucket)
│       ├── about.md
│       ├── notes/, bookmarks/, documents/
└── marge/
    └── ...
```

Why this shape:

- **One rule for topic placement:** topics live inside the bucket whose access scope they share. Personal topics under `<localpart>/`; shared topics under `<shared_bucket>/`. No special case for the top level.
- **Default searches naturally include shared topics.** A sender-scoped search returns `["family/", "<localpart>/"]`. Files under `family/camping/...` are picked up automatically — a family member asking about camping in #documents finds the topic content without knowing the room exists.
- **Promotion (Step 5) is a bucket-to-bucket move.** `git mv arthur/camping/ family/camping/` — both sides of the move are inside an owning bucket. The top level never changes.

### about.md is a derived view

Each topic folder has an `about.md` at its root, generated by the existing `stack memory wiki` command — the same rebuild path that already produces `family/about.md` and the per-member about pages. The bootstrap writes zero files; the folder appears the first time a capture lands; the about page is filled in on the next wiki rebuild.

```markdown
---
type: topic
slug: camping
display_name: Camping
scope: shared
participants: [arthur, sabrina]
captures: 47
last_capture: 2026-06-08
---

# Camping

A shared topic in the family bucket. 47 captures across notes, bookmarks, and documents.

## Recent activity

- 2026-06-08 — Voice memo: roof box arrangement [[family/camping/notes/2026/06/roof-box-arrangement-a3f1.md]]
- 2026-06-02 — Bookmark: best gravel routes in Tuscany [[family/camping/bookmarks/2026/06/gravel-tuscany-d7b2.md]]
- ...

## Cross-references

(Pointers to captures in other buckets the deriver identified as relevant. Filled in once the deriver lands.)
```

Why fully derived: the household does not edit the wiki by hand today — there is no proper editor, and pretending the system supports hand-edits adds complexity for no user value. When an editor surface lands later, hand-editable regions can be reintroduced. For now, every page in the vault that is not a capture is a derived view over the captures plus the room state.

## The tag invariant

The strongest property of topic rooms — and the reason they exist — is that retrieval becomes deterministic. The invariant has three pieces, each independently testable.

### 1. Seeding before classify

The archivist passes `seed_topics` from `room_state.default_topics` into the capture pipeline. The pipeline applies the seed to `SourceContent.topics` (or equivalent) before the classifier sees the source.

### 2. Additive classifier merge

`CapturePipeline._merge_classifier_topics()` unions the classifier's output with the seed, deduplicates by canonical form (via `Ontology.canonicalize_topic` when available, lowercase string match as fallback), and preserves order (seed first, then classifier additions in their original order).

### 3. Mirror preserves frontmatter

The mirror writer treats `topics:` as load-bearing and writes the merged list verbatim. No path in the mirror strips, truncates, or re-orders `topics:`.

### Test pins

A new test class `TestTopicSeedInvariant` in `tests/stacklets/test_capture_pipeline.py` pins all three:

```python
async def test_seed_survives_empty_classifier_output():
    """The room is the source of truth; the classifier is advisory.
    A topic-room capture retains its seed tag even when the LLM returns
    no tags at all."""
    outcome = await pipeline.capture_text(
        source, seed_topics=["camping"], classifier_topics=[],
    )
    assert "camping" in outcome.frontmatter["topics"]


async def test_seed_survives_classifier_contradiction():
    """A topic-room capture's seed survives even when the classifier
    returns an entirely unrelated set. Room beats classifier."""
    outcome = await pipeline.capture_text(
        source, seed_topics=["camping"], classifier_topics=["travel", "gear"],
    )
    assert outcome.frontmatter["topics"] == ["camping", "travel", "gear"]


async def test_seed_deduplicates_when_classifier_repeats_it():
    """The classifier may include the seed tag in its output; the merged
    list must not contain duplicates."""
    outcome = await pipeline.capture_text(
        source, seed_topics=["camping"], classifier_topics=["camping", "gear"],
    )
    assert outcome.frontmatter["topics"] == ["camping", "gear"]
```

The retrieval test ("find anything we noted about camping") becomes a deterministic grep:

```
grep -lr 'topics:.*camping' family/camping/ arthur/camping/ marge/camping/
```

Plus the deriver's cross-reference index. Guaranteed coverage.

## Query UX

| Surface | Scope behavior |
|---|---|
| `?` in `#Thema: Camping` | Search auto-scopes to `family/camping/` (or `arthur/camping/`) plus the topic's cross-references in `about.md`. |
| `?` in `#documents` or `#assistant` | Default sender-scoped search (`family/`, `<localpart>/`) — naturally includes shared topic content. |
| `stack memory ask "..." --topic camping` | Explicit topic-scoped CLI search. |
| `stack memory search "..." --topic camping` | Topic-scoped grep. |
| `stack memory ask "..." --global` (from a topic room) | Override the auto-scope; search the whole vault. |
| Forgejo / Obsidian | Open `family/camping/about.md` directly to browse. |

In-room scoping is the lightest UX: family members do not learn any flags. They ask in the room they are already in; the room is the implicit `--topic`.

The room-context resolver lives in `stacklets/memory/lib.py` (extending `resolve_search_query`) and reads the calling room's `dev.famstack.capture` state to determine scope. When `kind == "topic"`, scope defaults to the topic's bucket; explicit `--global` overrides.

## Cross-room pull (deriver)

The deriver (forward reference: [knowledge-architecture.md](knowledge-architecture.md) §Deriver Bot, [wiki-engine.md](wiki-engine.md) Step 4) runs on every capture and nightly during the dream cycle. For each topic bucket, it scans every other bucket for files whose frontmatter `topics:` includes the topic's slug, and writes pointers into the topic's `about.md` cross-references region:

```markdown
## Cross-references

<!-- begin: generated -->
- [doc] 2026-04-15 ADAC camping-trailer policy → [[family/documents/2026/04/adac-camping-policy-p247]]
- [note] 2026-05-02 Marge's gear list comment → [[marge/notes/2026/05/gear-list-comment]]
<!-- end: generated -->
```

The source file never moves. The ADAC camping-trailer policy belongs in `family/` (it is a household insurance document); camping just receives the citation. Captures live in the bucket they were posted to; topics are views over the vault, not containers that own data.

The cross-reference pass is deterministic — a grep for `topics:.*<slug>` across the vault, excluding the topic's own bucket (no point citing yourself). For shared topic `family/camping/`, that means scanning `family/documents/`, `family/correspondents/`, every personal bucket, and every *other* shared topic under `family/`. The intra-`family/` scan is what surfaces an ADAC camping-trailer addendum filed in #documents. No LLM call required. (An LLM pass may augment the cross-reference entry with a one-line summary of *why* the source is relevant; deferred to the deriver work.)

## CLI surface

```
stack memory topic list                        Show topic folders, their rooms, status, scope
stack memory topic show <slug>                 Print about.md
stack memory topic rename <old> <new>          git mv + rewrite topics: in affected files
stack memory topic demote <slug>               shared → personal (operator action)
stack memory topic archive <slug>              Manual archive (sets status, no Matrix kicking needed)
```

`topic create` is intentionally absent. Topic creation goes through Matrix — create a room with the prefix. This keeps the room as the source of truth and rules out CLI-created topics with no room (and therefore no capture surface).

## Implementation map

| Module | Status | Purpose |
|---|---|---|
| `stacklets/docs/bot/topic_rooms.py` | new | Pure parser: `parse_topic_name`, `derive_slug`, `is_reserved`, `scope_from_members`, `bucket_for_scope`, `make_room_state`, `binding_from_state`. All unit-testable; no I/O. |
| `stacklets/docs/bot/archivist.py` | changed | Lazy bootstrap (`_topic_binding`): read room state on every capture; parse the room name and write state on first encounter. Threads `bucket` + `seed_topics` into the four capture entry points and `topic_bucket` into search. |
| `stacklets/docs/bot/capture_pipeline.py` | changed | `seed_topics` and `bucket` kwargs on `capture_url`, `capture_text`, `capture_voice_batch`, `capture_binary`. `_merge_seed_topics` does the additive dedupe; `_publish` uses `bucket` to override the sender-derived entity. |
| `stacklets/docs/bot/search_service.py` | changed | `scopes_for_sender` and `run` accept `topic_bucket`. When set, scopes become `[<topic_bucket>/]` only; otherwise the existing shared + sender defaults. |
| `stacklets/memory/cli/topic.py` | future | `stack memory topic list / show / rename / demote / archive`. Not in v1. |
| `stacklets/memory/cli/wiki.py` | future change | Extend to discover topic folders under each bucket and generate `about.md` from captures + room state. Replaces the per-bootstrap scaffold idea. |
| `tests/stacklets/test_topic_rooms.py` | new | Parser, slug derivation, reserved-name check, scope detection, bucket composition, room-state shape, binding extraction. Pure unit tests. |
| `tests/stacklets/test_capture_pipeline.py` | changed | `TestTopicSeedMerge`, `TestTopicSeedEndToEnd` — the three-piece invariant plus the bucket-override pins. |
| `tests/stacklets/test_search_service.py` | changed | `TestScopesWithTopicBucket` — topic-bucket override semantics. |
| `tests/stacklets/test_archivist_topic_bootstrap.py` | new | Bootstrap flow, idempotency, reserved-slug refusal, resilience, human counting. Light fakes for the nio state I/O. |
| `tests/stacklets/test_topic_promotion.py` | future | Personal → shared promotion, debounce, collision, cancellation. Lands with Step 5. |

### Sequencing

Six implementation steps, each independently shippable. Steps 1-4 are shipped on this branch; the wiki extension lands next; Step 5 follows.

1. ✅ **Parser + slug derivation.** Pure function module plus unit tests. No Matrix, no I/O.
2. ✅ **Tag invariant + bucket override in capture pipeline.** `seed_topics` and `bucket` kwargs plumbed through the four entry points; additive merge pinned with tests.
3. ✅ **Archivist lazy bootstrap.** `_topic_binding` reads existing room state or parses the room name and writes state inline. Bootstrap is idempotent and best-effort. Wired into all four capture entry points.
4. ✅ **In-room query scoping.** `SearchService` accepts `topic_bucket`; the archivist passes the binding's bucket on `?` queries. Default sender-scoped search still picks up shared topic content naturally because shared topics nest under `<shared_bucket>/`.
5. ⏳ **Wiki-command extension** (replaces the pre-bootstrap scaffold idea). Generate `<bucket>/<topic>/about.md` from captures + room state, the same rebuild path that already builds `family/about.md`.
6. ⏳ **Promotion handler.** Member-count watcher, debounce, `git mv arthur/<slug>/ family/<slug>/`, room-state rewrite, message.
7. ⏳ **Deriver cross-reference pass.** Grep-based scan, append to `about.md`. Deferred until the deriver bot exists (post-v0.3).

## Open questions

1. **Topic-aware ontology.** Should the topic slug auto-register as a topic in `stacklets/memory/seeds/ontology.toml`, or stay as a free-form tag? Registering gets language-aware synonyms (`camping` aligned with `Camping` for queries); not registering keeps the seed flat and avoids ontology churn. Default for v1: stay free-form, leave promotion to ontology as an explicit operator step.
2. **Document-room overlap.** What if a user creates `Thema: Versicherungen` and drops actual insurance documents there? They still go to Paperless (the docs pipeline), but the mirror lands in the topic folder, not in `family/`. The existing capture pipeline already supports this for entity buckets; topic buckets reuse the same path. Worth a test pin.
3. **Bot accounts as humans for scope detection.** A topic room with `scribe-bot` plus `archivist-bot` plus Arthur has one human. The scope-detector must filter known bot users. Implementation reads from `users.toml` or the equivalent registry.

## Future direction: story rooms

This section captures a planned extension agreed in the 2026-06-09 design session. **Not in scope for this branch.** Recorded here so the topic-room implementation does not paint itself into a corner.

### Why stories are a peer concept, not a child

Topics are open-ended (camping as an ongoing interest; cooking as a shared household habit; gravel cycling as Arthur's hobby). Stories are bounded — they have a beginning and an end. The 2027 Italy trip, the bathroom renovation, Marge's 40th birthday party. Conflating them was the earlier mistake: a hobby grows for decades, a trip wraps up in two weeks. The about.md prompt, the lifecycle, and the "find this again later" intent differ enough that one model serves both badly.

Stories live as **peers of topics**, not children. A trip does not nest inside the camping topic. Instead, the story declares which topics it draws from, and the topic page picks up the story's captures via the existing cross-reference grep. This keeps each layer's purpose clean.

### Routing — same engine

Same parser, same bootstrap, same capture pipeline. The story prefix sits alongside `Thema:` / `Topic:` in the prefix recognizer. The room state's `kind: story` discriminates from `kind: topic` everywhere downstream (wiki render, status auto-flip, future deriver pass). The folder shape is identical: `<bucket>/<slug>/notes/`, `<bucket>/<slug>/bookmarks/`, `<bucket>/<slug>/documents/`, `<bucket>/<slug>/about.md`.

### Prefix (open)

Three candidates. Pick before implementation; the parser branches on whichever lands.

- `Projekt:` / `Project:` — reads concrete, matches household vocabulary
- `Story:` / `Geschichte:` — matches the earlier knowledge-structure design, but "Geschichte" reads as "history" in German first
- `Plan:` / `Plan:` — emphasises the planning phase

### Room state

```json
{
  "type": "dev.famstack.capture",
  "content": {
    "kind": "story",
    "bucket": "family/italien-2027",
    "slug": "italien-2027",
    "display_name": "Italien 2027",
    "default_topics": ["italien-2027"],
    "parent_topics": ["reisen"],
    "scope": "shared",
    "status": "planning",
    "starts": "2027-07-15",
    "ends": "2027-07-29",
    "participants": ["arthur", "marge"],
    "extract_knowledge": true,
    "bootstrapped_at": "2026-06-09T12:00:00Z",
    "bootstrapped_by": "@arthur:home"
  }
}
```

New fields over a topic:

| Field | Purpose |
|---|---|
| `parent_topics` | The ongoing topics this story belongs to. The seed-topic invariant expands so every capture in the story gets `[italien-2027, reisen]` tagged; the parent topic's cross-reference grep picks the captures up automatically. |
| `status` | `planning` (before `starts`), `active` (between `starts` and `ends`), `completed` (after `ends`), `archived` (90 days after `ends`, or manual). |
| `starts`, `ends` | Date range. `ends` may be null for stories with an open horizon (a renovation with no end date pinned yet). |
| `participants` | Canonical localparts of the humans the story belongs to. Defaults to the room's joined humans at bootstrap time. |

### Status auto-flip

The wiki command rewrites each story's `status` on every rebuild based on the current date relative to `starts` / `ends`. No background job needed: the rebuild is the only state-changing pass, and it runs frequently enough that "active" never lags reality by more than a wiki rebuild.

### About.md differs

The wiki command branches on `kind: story`. Story about.md leads with a status block (`status: planning`, countdown to `starts`, days since `ends` for completed) and a prominent action-items section. It does not have the open-ended "what does the family use this for" abstract framing topic pages carry — the story IS the thing it is for.

### Capture flow

A capture in `Projekt: Italien 2027` (or whatever prefix lands):

1. Bucket: `family/italien-2027/`
2. Seed topics: `[italien-2027, reisen]` (story slug + parent topics)
3. Files at `family/italien-2027/notes/2027/...md` with `tags: [italien-2027, reisen, ...]`
4. The `family/reisen/about.md` topic page's cross-reference grep finds the capture via the `reisen` tag, lists it under "Cross-references" with a link back to its actual location
5. The `family/italien-2027/about.md` story page lists the capture under "Recent activity" and surfaces any action items extracted by the classifier

### Lifecycle

| Event | Effect |
|---|---|
| Bootstrap | Same as topic bootstrap. Detect `kind` from prefix; carry the date / participant fields when prompted (CLI or later: invite Kit Bot to ask in-chat). |
| Trip-end date passes | Next wiki rebuild flips `status` to `completed`. About.md drops the countdown, gains a "lessons learned" section. |
| 90 days after `ends` | Next wiki rebuild flips `status` to `archived`. Story drops from the parent topic's active-stories list; folder and captures stay forever. |
| Family kicks the bot or leaves the room | Same archive behaviour as a topic. |

### Why this stays simple

Three properties keep the story extension from sprawling:

- **No nesting.** Topics and stories are flat in their bucket. The bridge is the `parent_topics` field on the story, picked up by the topic's existing cross-reference grep. No special hierarchy code.
- **Status is derived.** Nothing writes `status` at runtime. The wiki command recomputes it from dates on every rebuild. No background job, no state-machine code in the archivist.
- **Same routing engine.** The parser gains a prefix; the bootstrap gains a kind-discriminating branch; the capture pipeline is untouched (the seed-topic invariant already handles `default_topics` of any length).

### Implementation cost estimate

Roughly half the topic-room work:

- Parser: add story prefix + return `kind` on `ParsedRoomName` (renamed from `ParsedTopicName`)
- Bootstrap: branch on parsed kind; story bootstrap collects extra fields (interactive prompt or sensible defaults)
- Wiki: new `_generate_story` + `_build_story_prompt`; status auto-flip pass before the rebuild loop
- Tests: parser variants, story state shape, status-flip table

~3-5h once the topic-room branch is settled.

## Future direction: personal entity graph

This section captures another planned extension agreed in the 2026-06-09 design session. **Not in scope for this branch.** Recorded here so the topic-room implementation does not paint itself into a corner.

### The problem

Today's classifier extracts persons, dates, topics, and free-form tags per capture. It does *not* learn that "our BMW 320d" is central to this household's camping topic, or that "Thule roof box," "der Thule," and "the rooftop carrier" are three names for the same physical object. Asking "how did we pack the trunk for camping?" relies on tag-level keyword matching; it has no notion of which entities are central to *this* household's camping life.

The fix is a per-topic entity graph the system builds from its own captures, not from a generic vocabulary.

### Generic versus personal

The existing ontology (`stacklets/memory/seeds/ontology.toml`) is generic: universal categories like `insurance`, `vehicle`, `medical`. It ships with the install and applies to every household.

A personal entity graph is the inverse: household-specific things, people, places, products, locations that recur in this household's captures. The BMW 320d, the Karwendel campsite, the Vaude tent, Marge's mosquito allergy. None of these belong in a shipped ontology. They emerge from filings.

The two layers sit alongside each other:

| Layer | Lives in | Scope | Authored by |
|---|---|---|---|
| Generic ontology | `stacklets/memory/seeds/ontology.toml` | Universal | famstack maintainers |
| Personal entity graph | `<bucket>/<topic>/entities.toml` | One household, one topic | The deriver, over time |

### What the data looks like

A per-topic registry of entities with aliases and co-occurrence weights:

```toml
# family/camping/entities.toml — derived, regenerated by the deriver

[entity.bmw-320d]
display      = "BMW 320d"
kind         = "vehicle"
aliases      = ["BMW", "der 320er", "das Auto"]
first_seen   = 2024-03-12
last_seen    = 2026-06-08
captures     = 14
co_occurs    = ["thule-roof-box", "karwendel-campsite"]

[entity.thule-roof-box]
display      = "Thule Roof Box"
kind         = "gear"
aliases      = ["Thule", "Dachbox", "rooftop carrier"]
captures     = 9
co_occurs    = ["bmw-320d"]

[entity.karwendel-campsite]
display      = "Karwendel Campsite"
kind         = "location"
aliases      = ["Karwendel", "der Karwendel-Platz"]
captures     = 6
```

Each entity has a stable slug, a display name, a kind (`vehicle`, `gear`, `location`, `person`, `product`, ...), known aliases (future captures using a different spelling resolve to the same entity), capture counts, and a co-occurrence list (which other entities show up in the same captures).

### How it gets built

A deriver pass per topic, run incrementally on every new capture (or batched per nightly dream cycle):

1. **Extract.** LLM pass: "Read this capture. List every concrete entity mentioned — vehicles, gear, locations, people not in the persons list, products, brands. Return `(display, kind, aliases)`."
2. **Resolve.** Match each extracted entity against the existing registry (by display, by alias, by similarity). New entities get a new slug; recognised ones increment `captures` and merge new aliases in.
3. **Co-occur.** Update `co_occurs` for every pair of entities mentioned in the same capture.
4. **Decay.** Entities not seen in N captures or N months fade in salience, but are not deleted. The campsite the family hasn't visited in five years should still be findable when asked.

The pass is per-topic because the cost scales with the topic's capture count, and because the same entity is rarely relevant outside its home topic (a campsite is a camping entity; a tax accountant is not).

### How it's used

Four downstream consumers, each pulling on a different angle of the registry:

| Surface | Use |
|---|---|
| **Topic about.md** | A `## Key entities` section lists the highest-weighted entities with one-line context: "BMW 320d (the family car, 14 camping captures), Thule roof box (9), Karwendel campsite (6 visits)." Grows with the topic. |
| **Topic cross-references** | When an entity registered to a topic appears in a capture in another bucket (the BMW 320d shows up in `family/documents/2026-04-15-adac-policy.md`), the topic's cross-reference grep includes it even when no topic tag was applied. The entity *is* the bridge. |
| **Query expansion** | A `?` query in the topic room rewrites "how did we pack the trunk" to include "BMW 320d AND Thule roof box AND camping" for richer recall. Same shape `Classifier.synthesize_answer` already uses for keyword expansion. |
| **LLM context for synthesis** | The top entities feed the synthesis prompt as ambient context: "When this household says 'the car' under camping, they mean their BMW 320d. When they say 'the box,' they usually mean the Thule." Removes ambiguity in the answer without the user having to specify. |

### Propagation to the parent topic

Entities are local to the topic that learned them, but propagate one level up via `parent_topics` (the same mechanism the story design uses):

- `family/camping/entities.toml` knows the BMW is central to camping
- The parent topic (`family/reisen/` if Reisen is the parent of Camping) inherits the BMW as a candidate, ranked lower until its own captures reinforce it
- A future capture in Reisen that says "the car" can resolve to BMW 320d because the parent topic's candidate registry already carries the alias

Propagation is structural (parent-child by `parent_topics`), not heuristic. No cross-bucket leakage; a personal topic's registry never reaches another personal bucket.

### Relation to capture-tags.json

The existing capture-tag cache (`stacklets/docs/bot/capture-tags.json`) records which free-form tags have been used so the classifier prompt can include them as "existing tags" for consistency. The personal entity graph is the same idea at a higher level: where capture-tags learns the vocabulary, the entity graph learns the *entities* and their *relationships*. One is a flat list with counts; the other is a typed graph with aliases and co-occurrence edges. The capture-tag cache will likely fold into the entity graph once the deriver lands — same purpose, richer shape.

### Why this is deriver work

The capture pipeline today files one capture and returns. The entity registry needs a pass that reads *all* of a topic's captures, looks at relationships across them, and persists structured output back to the vault. That is precisely the deriver bot's job per [knowledge-architecture.md §Deriver Bot](knowledge-architecture.md). The personal entity graph lands as one of the deriver's outputs alongside the cross-reference index in `about.md`.

Until the deriver exists, the topic-room work cannot ship entity learning. Two paths to consider when the time comes:

- **Wait.** Personal entity graph lands as a feature of the deriver branch.
- **Stub.** Add a `stack memory wiki --derive-entities` flag that runs a one-shot LLM pass over a topic's captures and writes `entities.toml`. Useful for proving the model out before the deriver is built, but a temporary scaffold.

Recommended: wait. Build the deriver first; the entity registry is a natural shape inside it.

### Scope estimate

Bigger than the story extension because the LLM pass is per-capture, the persistent store is new, and the merge / decay logic needs care. Roughly the size of the topic-rooms work itself — 8-12h plus the deriver foundation.

## Status of this document

This is the design Arthur and Claude agreed to in the 2026-06-09 session. Code follows it. If implementation drifts from the design in non-trivial ways, update this document before shipping the drift — [family-memory.md](family-memory.md) is the descriptive doc for what is running; this is the prescriptive one for topic rooms.

## Related

- [[knowledge-architecture]] — the broader event bus and storage layout this extends
- [[family-memory]] — the vault structure topic folders sit alongside
- [[knowledge-structure]] — the concept layer, including the `dev.famstack.capture` room state contract
- [[wiki-engine]] — the deriver work that will populate the cross-references region
- [[ontology-v1]] — the taxonomy topic slugs may eventually register against
