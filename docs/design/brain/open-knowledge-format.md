# Open Knowledge Format (OKF) support

> Status: design draft
> Created: 2026-06-16
> Author: Arthur + Claude
> Depends on: [knowledge-structure.md](knowledge-structure.md) (vault shape),
> [family-ontology.md](family-ontology.md) (entity pages),
> [wiki-engine.md](wiki-engine.md) (derived wiki)
> Source: https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing
> Spec: https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf

## TL;DR

OKF is an open spec (Google Cloud Data Cloud team, published 2026-06-12)
that formalizes the LLM-wiki pattern -- markdown files + YAML frontmatter,
file path as identity, markdown links as the relationship graph,
`index.md` for navigation, `log.md` for chronology. That is, almost
exactly, the famstack memory vault we already ship.

We are *structurally* about 90% conformant by accident, because both we
and OKF descend from the same Karpathy pattern. The gap is small and
mostly naming. Since we are pre-1.0 on a beta branch, this is the cheap
window to close it: add the one mandatory field, align a couple of field
names, and add an export path. **Recommendation: adopt OKF as a
conformance + export target, not as our internal model.** Keep our richer
vault (facts with TTL, privacy buckets, ontology, contradiction handling);
make it *emit* clean OKF so a family's knowledge is portable to any
OKF-aware tool. Do not bet the architecture on a four-day-old v0.1 spec.

## What OKF actually is

A bundle is a directory of markdown files. Each file is one "concept"
(a table, a metric, a runbook -- for us: a document, a person, a topic).
Rules, in full:

- **Identity is the file path.** `/family/people/homer.md` *is* the
  identifier for Homer. No UUIDs.
- **One mandatory frontmatter field: `type`.** Everything else is
  optional. Standard optional fields: `title`, `description`,
  `resource` (URL to the original), `tags`, `timestamp`.
- **Relationships are plain markdown links** between files:
  `[customers](/tables/customers.md)`. The link graph is the knowledge
  graph. No RDF, no triples, no database.
- **`index.md`** in any folder = progressive-disclosure navigation as an
  agent walks the tree. **`log.md`** = optional append-only change log.
  Both are reserved filenames.
- **Producers may add any custom fields; consumers use what they
  understand and ignore the rest.** The spec is the interoperability
  surface, deliberately not a content model.

It ships two reference implementations worth knowing: an *enrichment
agent* (walks a data source, drafts OKF docs -- our Deriver analog) and a
*single-file static HTML visualizer* (renders a bundle as an interactive
graph, no backend, no data leaves the page).

What OKF is **not**, and therefore what it cannot replace for us: it has
no notion of fact decay/TTL, privacy scoping, contradiction handling, or
entities as first-class. It is a *serialization surface*, not a memory
system. It is also aimed at enterprise data catalogs (BigQuery tables,
metrics), not family memory -- the structural overlap is real, the
semantic overlap is partial. We export to it; we do not model in it.

## Why bother

One reason, and it is a good one: **portability with a name to point at.**
Our pitch has always been "plain markdown on your own disk, no lock-in,
take it anywhere." OKF lets us upgrade that from a vibe to a standard:
*your family's knowledge is stored in an open, vendor-neutral format --
render it on GitHub, open it in any editor, hand it to any OKF-aware
agent, with zero export step.* That is a credibility asset for a
privacy-first, own-your-data product, and it costs us very little because
we are already shaped like OKF.

Secondary: the OKF static visualizer is a free, offline, no-install vault
browser we could adopt or fork for families who do not run Obsidian.

## Gap analysis (grounded in current code)

All field names below are verified against `stacklets/docs/bot/mirror_format.py`
and `docs/design/brain/family-ontology.md` as of this branch.

### Document mirror frontmatter

Current (`document_frontmatter`): `title`, `date?`, `correspondent?`,
`document_type?`, `category?`, `persons?`, `tags?`, `paperless_id`,
`paperless_url?`, `processing`, `model?`, `paperless_version?`,
`source`, `added`.

| OKF field | famstack today | Gap / action |
|---|---|---|
| `type` (required) | absent | **Add** `type: document`. This is the only hard conformance break. |
| `title` | `title` | aligned |
| `description` | none (prose lives in the `> [!summary]` body callout) | optional; could mirror the summary's first line into `description` |
| `resource` | `paperless_url` (base URL, not page-specific) | align: set `resource` to the per-document Paperless page URL |
| `tags` | `tags` | aligned |
| `timestamp` | `added` | **Rename** `added` -> `timestamp` |
| (custom) | `correspondent`, `document_type`, `category`, `persons`, `paperless_id`, `processing`, `model`, `paperless_version`, `source` | keep as custom fields -- OKF ignores what it does not know |

Note the naming subtlety: OKF `type` is the *concept kind* ("document"),
while our existing `document_type` is the Paperless *subtype* ("invoice",
"contract"). They are different axes and can coexist: `type: document` +
`document_type: invoice`. No need to rename `document_type`.

### Capture frontmatter

Current (`capture_frontmatter`): `title`, `kind`, `date?`, `persons?`,
`tags?`, `source_uri?`, `model?`, `added`.

| OKF field | famstack today | Gap / action |
|---|---|---|
| `type` (required) | `kind` (note/bookmark) | **Add** `type: note` / `type: bookmark`, or promote `kind` to `type`. Recommend promoting `kind` -> `type` (one field, OKF-native). |
| `resource` | `source_uri` | **Rename** `source_uri` -> `resource` (exact semantic match: URL to the original) |
| `timestamp` | `added` | **Rename** `added` -> `timestamp` |
| `title`, `tags` | same | aligned |

### Entity pages

Current (`family/people/homer.md`): `canonical`, `aliases`, `role`,
`member`, `birthday?`, `employer?`, `owners?`, `relation?`. Critically,
`kind` is **derived from the folder, never stored** (people -> person).

| OKF field | famstack today | Gap / action |
|---|---|---|
| `type` (required) | absent (folder-derived) | **Add** `type: person` / `pet` / `vehicle` / `place`. We keep deriving from the folder for our own use; we also write it so the file is self-describing and OKF-valid standalone. |
| `title` | `canonical` | either rename `canonical` -> `title`, or write both. Recommend writing `title: <canonical>` and keeping `canonical` for our H1/canonicalization logic. |
| (custom) | `aliases`, `role`, `member`, `birthday`, `owners`, ... | keep as custom |

The doc already promises "unknown frontmatter keys are preserved (forward
compat)", so adding `type`/`title` is consistent with the existing schema
contract, not a break.

### Structural items

| OKF concept | famstack today | Gap / action |
|---|---|---|
| identity = file path | date-bucketed paths + `entity/about.md` hubs | compatible. `about.md` is the entity concept file; folder is the entity. |
| `index.md` (per-folder nav) | one L4 master `index.md`; entity hubs are `about.md` | add lightweight per-folder `index.md` listings (the wiki rebuild can emit these). `about.md` stays the human hub. |
| `log.md` | `stack memory log` over git history; no materialized file | optional. Could materialize a `log.md` on export, or leave it (git IS the log). |
| reserved filenames | `_unfiled`, `about` | check `index.md`/`log.md` do not collide with any generated slug; the archivist's reserved-slug set already guards `about`. |
| relationship links | Obsidian `[[wikilinks]]` (`[[Homer]]`, `[[ADAC]]`) | **The one real divergence.** OKF wants relative markdown links `[Homer](/family/people/homer.md)`. See below. |

## The wikilink decision

This is the only non-trivial fork. OKF's graph is built from standard
relative markdown links. We author Obsidian `[[wikilinks]]` (name-based,
resolved by Obsidian's index), which OKF parsers do not understand as
edges.

Two options:

1. **Translate on export.** Keep `[[wikilinks]]` as the internal authoring
   convention (Obsidian-native, matches `wiki-engine`, matches olw, what
   families browsing the vault actually use). The OKF exporter rewrites
   `[[Name]]` -> `[Name](relative/path.md)` by resolving against the entity
   roster. Internal UX unchanged; OKF surface is clean.
2. **Switch natively to relative markdown links.** Conformant by
   construction, no exporter. But it degrades the Obsidian experience
   (wikilinks are why backlinks and the graph view "just work") and is a
   wider rename touching every renderer in `mirror_format.py`.

**Recommendation: option 1 (translate on export).** The vault's primary
consumer is a family in Obsidian/Forgejo, not an OKF agent. Optimize the
internal surface for them; treat OKF as the portable export. Resolving
`[[Name]]` to a path requires the entity roster, which the wiki engine
already maintains -- the exporter is small.

(If we ever find the export translation is lossy or annoying to maintain,
the beta window is the time to reconsider option 2. Flag it, do not
silently switch.)

## The `tags` convention

OKF lists `tags` as *optional* (a recommended field, not a required one).
We considered making it a famstack-required field across every concept.
**Decision: keep it a convention, present-when-nonempty -- not required.**

Two reasons. First, "always emit `tags`, even empty" breaks the
presence-as-signal idiom the builders already rely on (the capture
renderer omits an absent `source_uri` precisely so a Dataview
`where source_uri` cleanly filters); an always-present `tags: []` is
noise that defeats "has tags vs untagged" filtering. Second, document
and capture tags come from the classifier, but entity and topic pages
have no tag source today -- a required field there would be empty
theater. So: documents and captures emit `tags` when the classifier
produced some (already the case); entity/topic pages gain a `tags` slot
only once something populates it.

## What we change vs what we build

### Changes (the beta-window renames -- cheap now, expensive after 1.0)

1. Add `type` to all three frontmatter builders (`document`, capture
   note/bookmark, entity person/pet/vehicle/place). For captures, promote
   `kind` -> `type`.
2. Rename `added` -> `timestamp` everywhere it is written (and update any
   reader: Dataview queries in docs, `search_service`, CLI).
3. Rename capture `source_uri` -> `resource`; for documents set a
   page-specific `resource` URL.
4. Write `title` on entity pages (mirror of `canonical`).

All four are mechanical and test-covered surfaces. Each is a coherent
standalone commit (per repo working-notes: invariant changes stay
reviewable). Migration of *existing* vault files is a one-shot script
(rename keys in frontmatter, add `type` from folder); pre-1.0 we can also
just let old entries age out, but a script is safer and trivial.

### Build (additive, no rewrite)

5. **OKF exporter / conformance mode.** A `stack memory export --okf
   <dir>` (or a vault flag) that: emits per-folder `index.md` nav files,
   translates `[[wikilinks]]` to relative links, and writes a top-level
   bundle `index.md`. Reuses the wiki engine's roster + the existing
   `mirror_format` renderers.
6. **Conformance test.** A small validator (the OKF spec ships conformance
   criteria) run in `stacktests` against a fabricated Simpsons vault, so we
   do not silently drift out of conformance.
7. **(Optional, later) Visualizer.** Adopt or fork the OKF static HTML
   visualizer as an offline vault browser for non-Obsidian families.

## What we explicitly do NOT do

- **Do not replace the internal model with OKF.** OKF has no TTL/decay, no
  privacy scoping, no contradiction handling, no first-class entities. Our
  L0-L4 model and ontology stay the source of truth; OKF is a projection.
- **Do not contort the family ontology to fit a data-catalog spec.** OKF's
  worked examples are BigQuery tables and metrics. Where family semantics
  (persons, member/relative, owners) have no OKF standard field, they stay
  as custom fields. We bend the export, not the model.
- **Do not deep-couple to v0.1.** The spec is four days old. We track it,
  we conform at the surface, we keep the exporter thin enough to drop or
  rev cheaply if the spec changes or stalls.
- **Do not block any current brain work on this.** This is a portability
  layer on top of the existing vault, sequenced after the core knowledge
  pipeline, not ahead of it.

## Suggested sequencing

- **Phase 1 (now, ~half a day):** the four frontmatter changes (items 1-4)
  + migration script + update readers. Lands us conformant-at-rest except
  for links.
- **Phase 2 (~half a day):** exporter + per-folder `index.md` + wikilink
  translation (items 5) + conformance test (item 6).
- **Phase 3 (optional, later):** visualizer (item 7).

Phases 1-2 are the whole conformance story and total roughly a day of
work, almost all of it mechanical, because we were already this shape.

## Inspiration from the reference repo (beyond conformance)

The reference repo (`github.com/GoogleCloudPlatform/knowledge-catalog`,
checked out locally) is **Apache 2.0**, which matters: unlike Honcho
(AGPL, flagged as unusable for us in `knowledge-architecture.md`), this is
license-compatible with our AGPLv3, so we can lift *code*, not just
patterns. It contains three subsystems worth mining. Ranked by leverage
for famstack, not by what OKF conformance strictly needs.

### Tier 1 -- genuinely new, high leverage

**1. The evaluation framework (the standout borrow).**
`agents/enrichment/eval/` is a ready-made blueprint for measuring
extraction quality, which is exactly the untested claim in
`family-ontology.md` ("priming a weak local model with household context
closes most of the accuracy gap" -- experiment round 6, never run). The
shape:
- A golden JSON per corpus (`goldens/TEMPLATE.json`): `expected_concepts`
  with `canonical` + `flavor_hints` (aliases) + `golden_facts`, plus
  `acceptable_extra_concepts` (extras that don't count as errors) and
  `non_entries` (things that must NOT be merged or invented).
- Deterministic checks first (`metrics.py` `check_structural`): YAML
  parses, required fields present, no stray frontmatter -- no LLM needed.
- LLM-as-judge metrics: `concept_recall` / `concept_precision`,
  `fact_recall`, `hallucination_free`, `business_terms_presence`.
- `MetricResult{score, passed, detail, insights}` -- every score carries
  *why* and *how to improve*.

We have the Simpsons corpus already. A `goldens/simpsons_family.json` plus
the recall/precision/hallucination metrics turns "we believe priming
helps" into a number we can regress against on every classifier change.
This probably deserves to graduate into its own doc or fold into the
family-ontology experiment plan; it is broader than OKF.

**2. Two-pass enrichment: topic-neutral summarize, then topic-shaped reduce.**
`agents/enrichment/src/modes/doc_mode.py` summarizes each source once into
a neutral "doc card" (cached, reused), then applies topic lenses
downstream. Maps directly onto the unbuilt Deriver: classify/summarize a
document once into a stable intermediate, synthesize many wiki/topic pages
from the cards without re-reading sources. This is the structural defense
against derived-note drift (sources read once; cards are the fixed
middle layer), and it reinforces our L1-mirror-as-stable-intermediate.

**3. Augmentation-strict regeneration (a concrete prompt contract).**
`okf/src/enrichment_agent/prompts/web_ingestion_instruction.md` is the
discipline our wiki regeneration needs and `wiki-page-anatomy.md` only
gestures at ("primed notes survive regeneration"). Verbatim-worthy rules:
copy `type`/`title`/`resource` verbatim, refine `description` only if
improved, merge `tags` as a union, keep every existing heading in order,
*extend not rewrite*, never shrink `# Schema` or `# Citations`. And
`bundle_tools.py` enforces the shrink-guard in *code*, not just the prompt
-- a regeneration that drops a section is rejected. We should make
"regeneration never destroys" a verifiable invariant the same way.

### Tier 2 -- useful, accelerates the conformance plan

**4. A clean, tested OKF document parser (liftable code).**
`okf/src/enrichment_agent/bundle/document.py` is a ~small `OKFDocument`
with `parse` / `serialize` / `validate` and roundtrip + error tests
(`okf/tests/test_document.py`). This is the Phase 2 conformance validator
nearly for free; Apache 2.0 means we can port it directly.

**5. `index.md` autogeneration algorithm.**
`okf/src/enrichment_agent/bundle/index.py` `regenerate_indexes()`: walk
depth-first, group by frontmatter `type`, emit `* [title](link) -
description`, bubble child descriptions up to parent indexes;
`synthesizer.py` LLM-writes a one-line description when none exists. This
is the concrete recipe for the per-folder `index.md` I scoped in Phase 2.

**6. Retrieval without embeddings (confirms our direction).**
`samples/enrichment/src/tools/fileskb/main.py` exposes exactly three
operations -- `list`, `read`, `search` (regex grep over `.md`) -- behind a
`SKILL.md` that tells the agent how to traverse. Plus the
`index.md`-first progressive-disclosure walk. This validates our
`stack memory` + CLI-as-agent-interface direction rather than adding new
work; the borrow is the three-tool minimalism and the SKILL.md packaging.

### Tier 3 -- patterns to note, mostly not applicable

**7. Static HTML graph viewer.** `okf/src/enrichment_agent/viewer/`
renders a bundle as a single self-contained cytoscape.js page: node per
concept, color by `type`, size by body length (a cheap "maturity"
signal), edges from parsed links. Concrete recipe for the optional
Phase 3 visualizer.

**8. mdcode's identity model (one nugget, skip the rest).** `mdcode`
(`toolbox/mdcode/`) is a heavyweight bidirectional sync engine
(source-of-truth <-> markdown, with snapshots, manifests, layouts,
push-back). Most of it is **not applicable**: we never write vault edits
back to Paperless, so the whole push/conflict half is dead weight for us.
The one nugget worth keeping: **identity lives in frontmatter, the
filesystem path is derived; reindex by globbing and reading the stored
id, so files can be renamed/moved without breaking identity.** That
directly informs our rename work -- if we stamp a stable concept id, the
"filename is stable after first AI pass" constraint in `mirror_format.py`
relaxes. Their separate `.state` checksum file (detect human edits vs
machine writes, fail-fast on drift) is also a clean pattern for the
Deriver to avoid clobbering hand-edits.

## Open questions

1. `resource` for documents: link to the Paperless page, the OCR PDF, or
   both (one in `resource`, one custom)? Leaning Paperless page.
2. Do we materialize `log.md` per bundle on export, or declare git the
   log and skip it? Leaning skip (git IS the log) unless a consumer needs
   the file.
3. `type` vocabulary: fix a closed set (`document`, `note`, `bookmark`,
   `person`, `pet`, `vehicle`, `place`, `topic`, `correspondent`) in
   `ontology.toml` so producers and the conformance test agree.
4. Per-folder `index.md` vs our `about.md` hubs: keep both (different
   jobs -- `index.md` is machine nav, `about.md` is the human entity page)
   or unify? Leaning keep both.
