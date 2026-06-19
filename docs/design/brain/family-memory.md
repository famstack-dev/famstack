# Family Memory — shipped shape (0.3.0)

> **Status**: descriptive, not aspirational. Documents what's actually
> running on `feat/brain-base` as of 2026-05-21.
>
> **Companion to** (not replacement for):
> - [knowledge-architecture.md](knowledge-architecture.md) — original
>   vision; some parts diverged at implementation time.
> - [knowledge-structure.md](knowledge-structure.md) — concept model;
>   most layers still match.
> - [phase-2-consolidation.md](phase-2-consolidation.md) — Phase 2
>   plan; the actual paths differ in places.
> - [plan.md](plan.md) — Phase 1 retrospective.
>
> **Scope**: the *memory vault* (the Forgejo `family/memory` repo the
> archivist writes to), the *Matrix event ledger* (how filings,
> captures, and corrections ride on the timeline), the *classify
> pipeline* the bot uses to fill that vault, and the *configuration
> knobs* that bind it all to a household's preferences. Anything that
> drifted from the design docs is captured here so a reader can see
> what's actually in the code without spelunking.

## Where things live

```
family/memory.git                       (Forgejo repo, seeded by `memory` stacklet)
├── README.md                           (auto-generated; describes the vault to humans)
├── ontology.toml                       (topic + doctype vocabulary, bilingual de/en)
│
├── <shared_bucket>/                    (institutional artifacts — see [core] shared_bucket)
│   ├── documents/                      (Paperless-mirrored documents)
│   │   ├── YYYY/MM/
│   │   │   └── YYYY-MM-DD-<slug>-p<paperless_id>.md
│   │   └── _unfiled/
│   │       └── p<paperless_id>.md      (when the doc has no usable date)
│   └── correspondents/                 (wiki pages for senders: ADAC, Booking.com, …)
│       └── <slug>.md
│
├── <entity>/                           (one bucket per family member; sender mxid → entity slug)
│   ├── notes/
│   │   ├── YYYY/MM/<slug>-<hash>.md    (text captures: pasted messages, snippets)
│   │   └── _unfiled/<slug>-<hash>.md
│   └── bookmarks/
│       ├── YYYY/MM/<slug>-<hash>.md    (URL captures: trafilatura → LLM digest)
│       └── _unfiled/<slug>-<hash>.md
│
└── wiki/                               (reserved for the Karpathy-style derived wiki, Phase 3+)
```

**The buckets:**

- `<shared_bucket>` — the household's institutional drawer. Defaults to
  `family`; the slug is configurable via `stack.toml [core] shared_bucket`.
  Deskstack uses `office`; surname-based households might use `simpson`.
- `<entity>` — per-family-member personal bucket. The slug is the Matrix
  localpart of the uploader (`@homer:home` → `homer/`). The archivist
  routes captures by the Matrix sender, not by classifier-detected
  persons. Cross-mentions (Homer captures something about Bart) stay
  under Homer; the frontmatter `persons:` field indexes them for Bart's
  wiki compile.

**Identity in the filesystem:**

- Documents carry their Paperless id in the filename suffix (`-p<id>.md`)
  so the bot can find an existing file when reprocessing without scanning
  frontmatter. Stable across title edits.
- Captures use a short hash of the source URL (or the body, for typed
  notes) as the filename suffix. Re-pasting the same URL/text yields
  the same path — idempotent update, not a duplicate.

## File shapes

### Document mirror

```markdown
---
type: document
title: ADAC Kfz-Versicherung 2026
date: 2026-03-15
correspondent: ADAC
document_type: Rechnung
category: Versicherung
persons: [Homer]
tags: [Versicherung, Fahrzeug, "Person: Homer"]
paperless_id: 247
paperless_url: http://docs.local
resource: http://docs.local/documents/247/details
processing: ai_formatted
model: qwen3.5-vl-7b
source: paperless
timestamp: 2026-05-20T14:23:00Z
---

# ADAC Kfz-Versicherung 2026

> **From:** [[ADAC]] · **About:** [[Homer]]

> [!summary]
> Jährliche Erneuerung der Kfz-Vollkasko, Police KFZ-2026-987.
>
> [Show Document](http://docs.local/documents/247/details)
>
> **Facts**
> - Total: EUR 340.00
> - Policy: KFZ-2026-987
>
> **Action items**
> - [ ] SEPA-Lastschrift prüfen — 2026-04-01

[OCR-cleaned or reformatted document body follows…]
```

Notes:
- The briefing rides as a `> [!summary]` Obsidian callout (tinted box
  in Obsidian; labeled blockquote in Forgejo). Visually distinct from
  the OCR body below.
- `Show Document` is a per-document link composed from `paperless_url`
  + `/documents/<id>/details`. The same URL is stamped into the OKF
  `resource` field; frontmatter `paperless_url` keeps the base URL so
  scripts can compose other deep links.
- Sections inside the callout use `**Bold labels**` instead of `##`
  headings — Obsidian renders them as section starts within the callout,
  and callouts don't nest H2s cleanly.

### Capture (note or bookmark)

```markdown
---
type: bookmark
title: Local-LLM benchmarks roundup
date: 2026-05-17
persons: [Arthur]
tags: [local-llms, benchmarks, "Person: Arthur"]
resource: https://example.com/llms
model: qwen3.5-vl-7b
timestamp: 2026-05-17T09:00:00Z
---

# Local-LLM benchmarks roundup

> **About** [[Arthur]]
> **Captured** 2026-05-17 · **Kind** bookmark
> **Source** <https://example.com/llms>

> [!summary]
> 200-word digest of the article, written in the document's language.
>
> **Facts**
> - Mac Mini idles under 10 W
> - M2 Pro hits 60 tok/s on Qwen3.5-9B
```

Notes:
- `type: note` keeps the user's pasted body in a collapsed
  `> [!quote]- Original paste` callout below the summary. `type: bookmark`
  has no body — the URL plus the digest IS the entry.
- No "Action items" section on captures. A bookmark to a Reddit thread
  is not a todo; pasting links shouldn't flood the system with chores.

### Paperless note

The classifier writes one note per filed document into Paperless's
note slot (FTS-searchable):

```
Jährliche Erneuerung der Kfz-Vollkasko bei ADAC. Police KFZ-2026-987,
Beitrag EUR 340 jährlich, fällig zum 01.04.

- Versicherungsnummer: KFZ-2026-987
- Beitrag: EUR 340,00
- Fälligkeit: 2026-04-01

ADAC → Homer

<!-- archivist-bot -->
```

Notes:
- Untitled — no `## Summary` / `## Facts` / `## Parties` headings.
  English labels forced an English frame onto German (or any
  non-English) content; blank lines do the section-boundary work
  instead.
- Trailing `<!-- archivist-bot -->` HTML marker. Invisible in
  Paperless's rendered Markdown view but unmistakable in raw text.
  Each reprocess sweeps notes containing this marker before posting
  the new one, regardless of how Paperless's `user` field is
  serialized (which has been the source of past sweep bugs).
- Legacy notes (pre-marker) are still recognized by their
  `## Summary` / `## Facts` / `## Parties` opening, so a one-time
  reprocess after upgrading cleans accumulated cruft.

## Matrix as ledger

Every state-changing action lands in Matrix as a single event the
deriver (Phase 3+) can replay. Envelope schema in both delivery
shapes:

```json
{
  "source":  "docs",
  "type":    "document.filed",
  "summary": "ADAC Kfz-Versicherung 2026 filed (#247)",
  "actor":   "@homer:home",
  "ts":      "2026-05-20T14:23:00Z",
  "data":    {"paperless_id": 247, "title": "…", "topics": [...], ...}
}
```

### Two delivery shapes

**Visible** — an `m.room.message` (the human notification) carries the
envelope as a content field:

```json
{
  "type": "m.room.message",
  "content": {
    "msgtype": "m.text",
    "body": "Filed: ADAC Kfz-Versicherung 2026 (#247)…",
    "format": "org.matrix.custom.html",
    "formatted_body": "<p>…</p>",
    "dev.famstack.event": { …envelope… }
  }
}
```

Used when the household sees the event (doc filings, classify
confirmations). One event in the timeline, full payload on one fetch.
Replies to the m.room.message can trace back to `data.paperless_id`
without extra plumbing — see *Reply-to-reprocess* below.

**Silent** — a `dev.famstack.event`-typed message:

```json
{
  "type": "dev.famstack.event",
  "content": { …envelope… }
}
```

Used for ops signals families shouldn't see (`service.started`,
`health.degraded`). Element ignores unknown event types entirely.

### Finding events in a room

Treat any timeline event as a ledger entry when EITHER:
- `event.content["dev.famstack.event"]` is present (visible shape), OR
- `event.type == "dev.famstack.event"` (silent shape).

Both deliver the same envelope. The deriver, the reply handler, and
any future consumer use this single predicate.

### Event types shipping today

| `source` | `type` | Delivery | `data` keys |
|---|---|---|---|
| docs | `document.filed` | visible | `paperless_id, title, date, topics[], persons[], correspondent, document_type, summary, facts[], action_items[], url` |
| docs | `document.reclassified` | visible | same as `document.filed` + `user_hint` |

More types arrive with the deriver and the other stacklet integrations.

## The classify pipeline

The classify prompt (`pipeline._build_classify_prompt`) is the bot's
contract with the LLM. Five contextual blocks ride in:

1. **`Date filed: YYYY-MM-DD`** — the document's filing date, used as
   the anchor for partial-date resolution. Initial Matrix uploads feed
   the message's server timestamp; the reprocess CLI and reply handler
   feed Paperless's immutable `added` field so a reprocess weeks later
   doesn't shift the anchor.
2. **Family members** — first names from `users.toml`. The LLM picks
   from this closed set.
3. **Ontology vocabulary block** — topic and doctype canonical names
   (and synonyms) in the household language only. See
   [ontology-design.md](ontology-design.md) for the source format.
4. **Correspondents block** — canonical names with their learned
   aliases inline, sourced from the vault's correspondent pages.
5. **User clarification block (optional)** — present only when the
   reply-to-reprocess flow or the CLI `--msg` flag passed a hint.
   Marked as OVERRIDING the model's own reading on conflicts.

### Rules baked into the prompt

- **VISION VS OCR** — when an image is attached AND the OCR text
  conflicts with what's visible, prefer the image. OCRmyPDF-stamped
  text layers (common on scanned uploads) often carry garbled dates
  and proper nouns; the vision pass is the override channel.
- **DATE** — full date → use verbatim; partial date → pick the year
  closest to `Date filed`, past for backward-looking docs (invoices,
  receipts), future for forward-looking ones (bookings, reservations,
  appointments). Never invent a year. Applies to the top-level `date`
  and any date inside `facts` / `action_items`.
- **LANGUAGE** — title, topics, doctype, summary, facts, action items
  must be in the document's language. A German doc gets German
  topics (`Reise`, `Versicherung`); never `Travel` or `Insurance`.
  When the ontology shows an English canonical for what should be a
  German tag, the *matcher* (not the prompt) handles cross-language
  normalization downstream.
- **persons** — names must explicitly appear in the document text.
  When the doc specifies a group only by count or role ("2 Erwachsene,
  2 Kinder", "die Familie") with no actual names, return `[]` — the
  pipeline falls back to the submitter.
- **title** — short identifying string (3–6 words, ~50 chars). No
  amounts, no full dates, no invoice numbers. Year only when it
  disambiguates annually-recurring docs. The same string becomes the
  Paperless title AND the filename slug.

### Vision attach policy

Single decision point in `archivist._should_attach_vision`:

| Input shape | Page count | Vision attached? |
|---|---|---|
| Image upload (PNG/JPG/...) | n/a | ✅ |
| PDF, no text layer (true scan) | any | ✅ |
| PDF, OCR'd text layer (OCRmyPDF / Tesseract stamp) | ≤ 5 | ✅ |
| PDF, OCR'd text layer | > 5 | ❌ (text-only, cost-bounded) |
| PDF, native text layer (Word, LaTeX, CMS-generated) | any | ❌ (trustworthy text) |

The 5-page cap is `_VISION_MAX_PDF_PAGES`. OCRmyPDF detection lives in
`_has_pdf_ocr_text_layer` — checks `/Producer` and `/Creator` metadata
for `ocrmypdf` / `tesseract`.

### Ontology-aware matching

LLM output flows through `Ontology.canonicalize_topic` /
`canonicalize_doctype` before reaching Paperless tags. Each returns a
`Resolution(canonical, cross_field)`:

- **`canonical="<name>", cross_field=False`** — resolved across any
  supported language. The household-language canonical name is used,
  regardless of which language the LLM emitted. (LLM says `Travel` on
  a German household → matcher writes `Reise`.)
- **`canonical=None, cross_field=True`** — text matched as the wrong
  kind (a doctype name landed in topics, or vice versa). The matcher
  drops the value silently. (LLM stuffs `Booking` into topics →
  matcher recognizes it's a doctype → drops; the doctype field can
  still independently resolve to `Buchung`.)
- **`canonical=None, cross_field=False`** — unknown to the ontology;
  falls through to the legacy fuzzy match against existing Paperless
  tags and, failing that, gets created as new vocabulary.

This is what keeps a German household from accumulating English tag
debt over time.

### Submitter fallback for persons

When the classifier returns `persons: []` and the upload's Matrix
sender resolves to a known `Person: <localpart>` tag, the archivist
attributes the document to the submitter. Cases:

- Live archivist (Matrix upload): submitter = uploading sender.
- CLI reprocess: no known submitter → empty persons stays empty (we
  don't pretend to know who uploaded a doc Paperless has had for
  weeks).
- Reply-to-reprocess: no submitter passed for the same reason.

The fallback only fires when the LLM returned empty; an explicit
classifier `persons:` list takes precedence.

## Reply-to-reprocess

User replies to a bot's filing message with a correction:

```
[bot] ✅ Filed: ADAC Kfz-Versicherung 2026 (#247) — Versicherung | Homer | …
[user reply]: das ist eigentlich Marges Versicherung, nicht Homers
```

Flow:
1. The bot's reply is an `m.room.message` carrying a
   `dev.famstack.event` envelope with `data.paperless_id = 247`.
2. The user's reply has `m.relates_to.m.in_reply_to.event_id` →
   bot's message.
3. The bot's text handler fetches the parent event via
   `client.room_get_event`, reads `data.paperless_id`.
4. The reply body is stripped of the Matrix quoted-fallback prefix
   (`_strip_reply_fallback`) — what remains is the human's actual
   correction.
5. The pipeline runs with `is_reprocess=True` (so the filing date is
   preserved) and `user_hint=<reply text>` (so the prompt's User
   clarification block carries the correction).
6. The mirror's idempotent publish overwrites the existing entry
   in place — same paperless_id, possibly new title slug.
7. The bot posts a `document.reclassified` envelope as a follow-up
   m.room.message; replying to *that* chains another correction.

No persistent state. Survives bot restarts because everything rides
on the timeline itself.

## CLI surface

```
stack docs reprocess <id|range>... [--msg "your hint"] [--no-reformat] [--no-mirror] [--dry-run]
```

`--msg "text"` lands in the classify prompt as the same User
clarification block as the Matrix reply path. Examples:

```
stack docs reprocess 7 --msg "Der Urlaub war im Februar 2026"
stack docs reprocess 1-5 --msg "filed for tax year 2025"
stack docs reprocess 42 --dry --msg "this is Marge's, not Homer's"
```

CLI reprocess uses Paperless's `added` field as the `date_filed`
anchor (filing date is immutable; reprocessing later doesn't shift
date resolution). No submitter fallback (no known uploader).

## Configuration knobs

In `stack.toml`:

```toml
[core]
language       = "de"        # household language; drives ontology rendering + i18n
shared_bucket  = "family"    # vault slug for institutional artifacts
host           = "localhost" # optional; pins URLs to localhost for mobile-network testing

[ai]
language       = "en"        # AI subsystem only — TTS voice, Whisper language
default        = "mlx-community/Qwen3.5-VL-7B-MLX-4bit"
```

The household language (`[core] language`) flows to the bot-runner via
the `LANGUAGE` env var (rendered from `{language}` in
`stacklets/core/stacklet.toml`). The archivist reads `os.environ["LANGUAGE"]`
to:
- pick which language the ontology section renders in (German topics
  vs English topics in the classify prompt);
- drive the household-language argument into `Ontology.canonicalize_*`
  so the matcher normalizes to the right canonicals;
- choose which `messages/archivist.yml` locale block to use for chat
  replies.

`[ai] language` is **not** the same lever — it controls AI-subsystem
knobs (TTS voice choice, Whisper transcription language). A German
household with an English-fluent classifier can run `[core] language =
"de"` and `[ai] language = "en"` simultaneously.

## What's not in this doc

- The wiki layer (Karpathy-style derived `wiki/`) — design only,
  implementation pending. See [knowledge-architecture.md](knowledge-architecture.md)
  for the long-term shape.
- The deriver bot — design only. Same source.
- Five-layer concept model (vocabulary / instances / mirror / facts /
  wiki) — see [knowledge-structure.md](knowledge-structure.md). L0–L1
  are live, L2–L4 are still on paper.
- Memory of the bot itself (memory.md, session search) — different
  concept, not part of the family memory vault. See
  [engram-prototype.md](engram-prototype.md) and the Hermes-style
  exploration there.
