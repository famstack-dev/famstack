# Vault format specification (v1)

The on-disk contract for a famstack memory vault. This is the vault's
real API: every reader (host CLI, bots, agent, wiki, search, mem0
priming) and every writer (archivist, curator, CLI) must agree on it.
Grounded in the current builders (`stacklets/docs/bot/vault_entry.py`,
`stacklets/memory/bot/cli/wiki.py`) and aligned with the Open Knowledge
Format (see `open-knowledge-format.md`). Companion to ADR-011
(vault-as-database) and the domain model.

`format: 1`.

## 1. File shape

A vault entry is a UTF-8 markdown file:

```
---
<frontmatter>
---

<markdown body>
```

- Identity is the file path (OKF rule). No UUIDs in frontmatter.
- The body is CommonMark. Links between entries are **relative markdown
  links** (`[Homer](../homer/about.md)`), never Obsidian `[[wikilinks]]`
  — relative links are the one syntax Obsidian, Forgejo, and OKF parsers
  all understand.

## 2. Frontmatter grammar (the strict subset — this IS the schema)

Frontmatter is NOT arbitrary YAML. It is a deliberately restricted
subset so a single stdlib-only parser can read it identically on the
host CLI and in containers. A writer MUST emit only this subset; a
reader MAY reject anything outside it.

Allowed:
- Delimited by a line `---` , content, a line `---`, then a blank line.
- **Top-level keys only:** `key: value`, one per line, no indentation on
  the key.
- **Scalar values:** string, integer, boolean (`true`/`false`), or a
  date-as-string (`2026-07-07`). Strings containing `:`, `#`, or leading
  special characters MUST be quoted.
- **One-deep string lists:**
  ```
  persons:
    - homer
    - marge
  ```

Forbidden (a writer MUST NOT emit; a reader MUST fail loudly, not
silently drop):
- Nested maps, lists of maps, flow syntax (`{}`, `[]`), anchors/aliases,
  multiline block scalars (`|`, `>`), multiple documents.

Rationale: full YAML's ambiguity buys nothing a family vault needs and
costs a C-extension dependency the host CLI cannot load. We own the
format; the subset is the spec.

## 3. Required fields (every entry)

| Field | Rule |
|---|---|
| `type` | REQUIRED. The OKF concept kind. Value MUST be from the closed vocabulary in §4. |

Everything else is per-type. OKF's standard optional fields (`title`,
`resource`, `tags`, `timestamp`) are used with their OKF meanings where
present.

## 4. The `type` vocabulary (closed)

Producers and the conformance validator MUST agree on this set. Adding a
type is a spec change (bump nothing below v2 until 1.0; pre-1.0 edits are
cheap).

**Records** (source; things that happened; never carry `generated`):
- `document` — a Paperless-mirrored document.
- `note` — a captured note (verbatim paste).
- `bookmark` — a captured URL.
- `email` — a folded email thread.

**Entities & structure** (generated projections; MUST carry
`generated: true`):
- `person` — a household member or known person page.
- `correspondent` — an external party page.
- `topic` — a topic overview page.
- `index` — a folder navigation page.

Reserved for future (declare before use): `pet`, `vehicle`, `place`.

## 5. Per-type fields

Legend: **R** required, **O** optional (present-when-nonempty — absence
is a signal, so never emit an empty list/string). List fields are
one-deep string lists per §2.

### `document`
| Field | | Notes |
|---|---|---|
| `type` | R | `document` |
| `title` | R | |
| `timestamp` | R | ISO datetime the entry was written (OKF `timestamp`). |
| `source` | R | `paperless` |
| `paperless_id` | R | integer; the join key back to Paperless. |
| `resource` | O | per-document Paperless page URL (OKF `resource`). |
| `date` | O | document date (distinct from `timestamp`). |
| `correspondent` | O | external party name. |
| `document_type` | O | Paperless subtype (invoice, contract) — a different axis from `type`. |
| `category` | O | |
| `persons` | O | list. |
| `tags` | O | list. |
| `paperless_url` | O | base URL. |
| `processing` | O | `ai_formatted` \| `ocr` \| `original`. |
| `model` | O | model that classified it. |
| `paperless_version` | O | |

### `note` / `bookmark`
| Field | | Notes |
|---|---|---|
| `type` | R | `note` or `bookmark` |
| `title` | R | |
| `timestamp` | R | |
| `persons` | O | list. |
| `filed_by` | O | Matrix localpart of the filer (mirrors the git author). |
| `tags` | O | list. |
| `resource` | O | source URL (OKF `resource`; bookmarks always have one). |
| `capture_id` | O | the `dev.famstack.event` envelope id. |
| `date` | O | capture date. |
| `model` | O | |

### `email`
As `note`, plus the thread is a fold of per-message sections; each
message is preceded by an idempotency marker comment
`<!-- mid:<Message-ID> -->` in the body (not frontmatter). `resource`
may carry the mailbox/thread reference.

### `person` (generated)
| Field | | Notes |
|---|---|---|
| `type` | R | `person` |
| `generated` | R | `true` |
| `title` | R | canonical display name (OKF `title`). |
| `slug` | R | bucket slug (== Matrix localpart for members). |
| `canonical` | R | canonical name (our H1/canonicalization logic). |
| `aliases` | O | list of known surface forms (the EntityRegistry coupling field). |
| `role`, `member`, `birthday`, `employer`, `owners`, `relation` | O | family-semantic custom fields. |

### `correspondent` (generated)
| Field | | Notes |
|---|---|---|
| `type` | R | `correspondent` |
| `generated` | R | `true` |
| `title` / `canonical` | R | canonical name. |
| `aliases` | O | list; the classifier reads these to reconcile mentions. |

### `topic` (generated)
| Field | | Notes |
|---|---|---|
| `type` | R | `topic` |
| `generated` | R | `true` |
| `slug` | R | |
| `scope` | R | `shared` \| `personal`. |
| `title` | O | |

### `index` (generated)
`type: index`, `generated: true`, `title` optional. Body is navigation.

## 6. The `generated` invariant (from B3 / ADR-011)

- A generated projection page MUST carry `generated: true`.
- A source record MUST NOT carry `generated`.
- This marker — not the filename — is the authoritative source-vs-
  projection signal for the mirror and for generation's skip-over-hand-
  written-pages rule. (Filename remains a backstop only for delete/rename
  diffs where content is unavailable.)

## 7. State documents

`todos.md` is a state document, not a generated page: it is mutable
current truth (a tick is information), lives in memory source, and never
carries `generated`. It is a markdown checklist; frontmatter is optional
and, if present, uses `type: todos`. State documents get read-your-writes
through the CLI (ADR-011); they are not regenerated.

`ontology.toml` / `facts.toml` are TOML config at the vault root, outside
this markdown-frontmatter spec.

## 8. Parser / writer / validator contract

There MUST be exactly one implementation of each, shared by host and
containers (in `lib/stack/`, stdlib-only so the host CLI can import it):

- **Parser**: reads the §2 subset into a dict. On out-of-subset input it
  raises (or returns a typed error) — never silently drops fields.
- **Writer**: serializes a dict to the §2 subset, quoting strings that
  need it, omitting absent-optional fields (no empty lists/strings).
- **Validator**: given a parsed dict, checks §3–§5 — `type` present and
  in vocabulary, required fields per type present, list fields are lists,
  `generated` presence matches the record/projection class. Runs on
  WRITE (fail fast) and is available to a conformance test.

One malformed page currently takes down the whole Quartz build; validate
on write so a bad page never reaches disk, rather than catching it three
hops downstream at publish.

## 9. OKF conformance

This spec is OKF-conformant at rest: required `type`, standard
`title`/`resource`/`timestamp`, path-as-identity, relative-link graph,
`index.md` navigation. Custom fields (`persons`, `paperless_id`,
`aliases`, …) are OKF-legal — consumers use what they understand and
ignore the rest. See `open-knowledge-format.md` for the exporter and
conformance-test plan (Phase 2).
