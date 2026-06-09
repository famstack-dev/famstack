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
| **Routing** | Captures in `#Thema: Camping` always file under `camping/`, regardless of the classifier's opinion. |
| **Tag invariant** | Every file in `camping/` has `camping` in its frontmatter `topics:`. The classifier may add tags; it can never remove this one. |
| **Discoverability** | The room list is the topic list. Family members find topics by browsing joined rooms — no separate index UI. |

Topic rooms compose with the existing capture pipeline. The classifier, mirror, search, deriver, ontology-canonicalizer — none of them special-case topic folders. They see a bucket like any other.

## Naming convention

| Room name | Slug | Bucket path (shared) | Bucket path (personal) | Guaranteed tag |
|---|---|---|---|---|
| `Thema: Camping` | `camping` | `camping/` | `arthur/camping/` | `camping` |
| `Topic: Photography` | `photography` | `photography/` | `arthur/photography/` | `photography` |
| `Thema: Van Life` | `van-life` | `van-life/` | `arthur/van-life/` | `van-life` |
| `Topic: 3D printing` | `3d-printing` | `3d-printing/` | `arthur/3d-printing/` | `3d-printing` |
| `Thema: Café Hopping` | `cafe-hopping` | `cafe-hopping/` | `arthur/cafe-hopping/` | `cafe-hopping` |

### Parser rules

- **Prefix recognition.** Match `^\s*(thema|topic)\s*:\s*` case-insensitively. Both languages always accepted, regardless of `[core] language`.
- **Slug derivation.** NFD-normalize, strip combining marks (`Vélo` → `velo`, `Café` → `cafe`), lowercase, replace runs of non-alphanumeric with `-`, strip leading and trailing `-`. Maximum 40 characters; collisions at the tail receive a numeric suffix during bootstrap.
- **Empty after prefix.** `Thema:` with no body fails the parser — not a topic room. The archivist treats it as a regular room.
- **Reserved slugs.** Refused at bootstrap: any existing top-level bucket name, the configured `[core] shared_bucket`, any Matrix-localpart that resolves to a known family member, plus the literal strings `meta`, `wiki`, `archive`, `_unfiled`.

### Slug stability

Once bootstrapped, the slug is the bucket path on disk and the value of `topics:` in every file beneath it. Renaming the Matrix room changes the display label (recorded in `about.md` frontmatter), never the slug. Re-slugging requires explicit operator action (`stack memory topic rename`), which does a `git mv` and rewrites `topics:` in every affected file.

## Room state schema

The archivist writes `dev.famstack.capture` room state on first detection and reads it on every event. The schema extends [knowledge-structure.md §Room configuration]:

```json
{
  "type": "dev.famstack.capture",
  "content": {
    "kind": "topic",
    "bucket": "camping",
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
| `bucket` | Top-level path in the vault. Shared topic: the slug. Personal topic: `<sender_localpart>/<slug>`. |
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
4. Detect scope by member count at the moment of bootstrap. One human (the sender, plus the archivist) → `personal`, bucket = `<sender_localpart>/<slug>/`. Two or more humans → `shared`, bucket = `<slug>/`.
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
4. `git mv arthur/camping/ camping/` (full history preserved through the rename).
5. Rewrites `bucket: camping` and `scope: shared` in the room state.
6. Appends to the auto-region of `about.md`: `promoted from arthur/camping/ on 2026-06-09 (sabrina:home joined)`.
7. Posts: `📁 Topic promoted to shared. Files now live under camping/.`
8. On the next deriver pass, cross-references in other buckets' indexes are rewritten to point at the new path.

### Personal → shared promotion: edge cases

| Case | Behavior |
|---|---|
| Promotion would collide with existing shared topic (`camping/` already exists) | Refuse promotion. Post a correction prompt. Leave the personal folder where it is. The room remains personal-scoped even though it has multiple humans (a known degraded state, visible in `stack memory topic list`). |
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

A topic folder follows the same convention as personal buckets, with `about.md` added at the root:

```
memory.git
├── family/                   (existing shared bucket)
├── arthur/, marge/           (existing personal buckets)
├── camping/                  (shared topic, bootstrapped from #Thema: Camping)
│   ├── about.md              (hand-edited + bracketed auto-region)
│   ├── notes/
│   │   ├── YYYY/MM/<slug>-<hash>.md
│   │   └── _unfiled/...
│   ├── bookmarks/
│   │   ├── YYYY/MM/<slug>-<hash>.md
│   │   └── _unfiled/...
│   └── documents/            (when documents are captured in the topic room)
│       └── YYYY/MM/...
└── arthur/
    └── camping/              (personal topic variant, nested under personal bucket)
        ├── about.md
        ├── notes/, bookmarks/, documents/
```

Personal topics nest *under* the existing personal bucket, not at the top level. This keeps the top level a clean privacy gate: top-level reads = explicit access scope. The promotion handler is the only path that creates a top-level topic folder.

### about.md scaffold

```markdown
---
type: topic
slug: camping
display_name: Camping
scope: shared
status: active
participants: [arthur, sabrina]
created: 2026-06-09
created_by: arthur:home
language: de
---

# Camping

<!-- begin: user-edited -->

(Write what this topic is about, the packing list, lessons learned, links
to gear reviews, anything you want to find again. This region survives
every regeneration.)

<!-- end: user-edited -->

## Activity

<!-- begin: generated -->

(Timeline of captures filed under this topic, regenerated by the wiki
engine on each deriver pass. Most recent first.)

<!-- end: generated -->

## Cross-references

<!-- begin: generated -->

(Pointers to captures in other buckets that the deriver identified as
relevant to this topic. Source files stay in their original buckets;
this section curates them.)

<!-- end: generated -->
```

The bracketed-region pattern is the one already proven in correspondent pages (see [family-memory.md](family-memory.md) §Mirror). User edits inside `<!-- begin: user-edited -->` survive every deriver pass. Auto-regions get fully rewritten on each pass.

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
grep -l 'topics:.*camping' camping/**/*.md
```

Plus the deriver's cross-reference index. Guaranteed coverage.

## Query UX

| Surface | Scope behavior |
|---|---|
| `?` in `#Thema: Camping` | Search auto-scopes to `camping/` plus the topic's cross-references in `about.md`. |
| `?` in `#documents` or `#assistant` | Global search, same as today. |
| `stack memory ask "..." --topic camping` | Explicit topic-scoped CLI search. |
| `stack memory search "..." --topic camping` | Topic-scoped grep. |
| `stack memory ask "..." --global` (from a topic room) | Override the auto-scope; search the whole vault. |
| Forgejo / Obsidian | Open `camping/about.md` directly to browse. |

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

The cross-reference pass is deterministic — a grep for `topics:.*<slug>` across non-topic buckets. No LLM call required for the citation pass. (An LLM pass may augment the cross-reference entry with a one-line summary of *why* the source is relevant; deferred to the deriver work.)

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
| `stacklets/docs/bot/topic_rooms.py` | new | Pure parser: `parse_topic_name()`, `derive_slug()`, `is_reserved()`, `room_state_from_name()`. All unit-testable; no I/O. |
| `stacklets/docs/bot/archivist.py` | changed | On-join handler: detect topic room, bootstrap if needed, write room state. Read room state on every capture and pass routing + seed into the pipeline. |
| `stacklets/docs/bot/capture_pipeline.py` | changed | Add `seed_topics` parameter to `capture_text`, `capture_url`, `capture_voice`, `capture_voice_batch`, `capture_binary`. Implement `_merge_classifier_topics`. |
| `stacklets/docs/bot/pipeline.py` | changed | Extend `_build_capture_prompt` to accept `seeded_topic`; emit the guidance line. Adjust the minimum-3 rule to count the seed. |
| `stacklets/memory/lib.py` | changed | Extend `resolve_search_query` to accept `scope_bucket`; topic rooms pass theirs. |
| `stacklets/memory/cli/topic.py` | new | `stack memory topic` subcommands. |
| `stacklets/memory/seeds/_topic_scaffold/about.md` | new | Scaffold template, copied into new topic folders by the archivist. |
| `tests/stacklets/test_topic_rooms.py` | new | Parser, slug derivation, reserved-name check, scope detection from member count. Pure unit tests. |
| `tests/stacklets/test_capture_pipeline.py` | changed | Add `TestTopicSeedInvariant` class with the three pins above. |
| `tests/stacklets/test_archivist_bootstrap.py` | new | Bootstrap flow, idempotency, collision handling. Uses the existing fake-Matrix rig. |
| `tests/stacklets/test_topic_promotion.py` | new | Personal → shared promotion, debounce, collision, cancellation. Filesystem + state machine. |

### Sequencing

Six implementation steps, each independently shippable:

1. **Parser + slug derivation.** Pure function module plus unit tests. No Matrix, no I/O. Lands as its own commit.
2. **Tag invariant in capture pipeline.** Add `seed_topics` parameter, additive merge, pin with the three invariant tests. No archivist change yet; the parameter is unused in production at this point.
3. **Archivist bootstrap.** On-join detection, room state writing, scaffold creation. Tag seed wires up to the parameter from step 2. First end-to-end working topic room.
4. **In-room query scoping.** `resolve_search_query` reads room state; `?` in a topic room becomes topic-scoped automatically. Adds `--topic` and `--global` flags to the search and ask CLIs.
5. **Promotion handler.** Member-count watcher, debounce, `git mv`, room-state rewrite, message. Most complex piece; lands on top of a working baseline.
6. **Deriver cross-reference pass.** Grep-based scan, write to `about.md` auto-region. Deferred until the deriver bot exists (post-v0.3).

Steps 1 through 5 are in scope for the initial branch. Step 6 lands with the deriver work.

## Open questions

1. **Topic-aware ontology.** Should the topic slug auto-register as a topic in `stacklets/memory/seeds/ontology.toml`, or stay as a free-form tag? Registering gets language-aware synonyms (`camping` aligned with `Camping` for queries); not registering keeps the seed flat and avoids ontology churn. Default for v1: stay free-form, leave promotion to ontology as an explicit operator step.
2. **Document-room overlap.** What if a user creates `Thema: Versicherungen` and drops actual insurance documents there? They still go to Paperless (the docs pipeline), but the mirror lands in the topic folder, not in `family/`. The existing capture pipeline already supports this for entity buckets; topic buckets reuse the same path. Worth a test pin.
3. **Stories inside topic rooms.** A camping trip is a story per [knowledge-structure.md](knowledge-structure.md) §Story-specific mechanics. When does a story page get created — automatic on first capture of a trip-shaped event, or hand-created via CLI? Deferred to the story work; topic rooms do not decide it.
4. **Bot accounts as humans for scope detection.** A topic room with `scribe-bot` plus `archivist-bot` plus Arthur has one human. The scope-detector must filter known bot users. Implementation reads from `users.toml` or the equivalent registry.

## Status of this document

This is the design Arthur and Claude agreed to in the 2026-06-09 session. Code follows it. If implementation drifts from the design in non-trivial ways, update this document before shipping the drift — [family-memory.md](family-memory.md) is the descriptive doc for what is running; this is the prescriptive one for topic rooms.

## Related

- [[knowledge-architecture]] — the broader event bus and storage layout this extends
- [[family-memory]] — the vault structure topic folders sit alongside
- [[knowledge-structure]] — the concept layer, including the `dev.famstack.capture` room state contract
- [[wiki-engine]] — the deriver work that will populate the cross-references region
- [[ontology-v1]] — the taxonomy topic slugs may eventually register against
