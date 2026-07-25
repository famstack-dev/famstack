# Family Diary & Family Journal

Two user-facing read surfaces over the Family Brain's timeline, split by
emotional weight and source. Same vault underneath; completely different
reading experiences.

## The split

| | **Family Diary** | **Family Journal** |
|---|---|---|
| **Tone** | Personal, emotional | Operational, factual |
| **Voice** | Verbatim — never paraphrased | Synthesized — dry, scannable |
| **Content** | Letters, voice notes, photos, dinner-talk transcripts, kid recordings | Appointments, deadlines, decisions, deliveries, payments, vehicle/home/insurance events |
| **Source** | Matrix `memories` room | Archivist-filed documents + calendar events |
| **Reader** | Kids in 2040 | You, last month |
| **Cadence** | Continuous (timeline view) + weekly/monthly highlights | Weekly + monthly rollups |
| **Published as** | `family/diary/...` on Forgejo | `family/journal/...` on Forgejo |

The diary is the artifact the family inherits. The journal is the thing
you check to remember whether the inspection was last Tuesday or the one
before.

## Why source-based classification

Each timeline entry's origin determines its lens — no LLM tagging step.

- Memories room → diary. The room itself is the filter; everything
  posted there is high-signal and personal by definition.
- Documents (Paperless) + calendar → journal. These are factual by
  nature: a dentist letter, a renewal notice, a calendar invite.

A letter never accidentally lands in the journal because letters don't
come from documents. A vehicle inspection notice never lands in the
diary because it doesn't come from the memories room. The boundary is
structural, not inferred.

## Architecture

```
┌──────────────────┐         ┌──────────────────┐
│  memories room   │         │  Paperless docs  │
│  (Matrix)        │         │  + calendar      │
└────────┬─────────┘         └────────┬─────────┘
         │                            │
         ▼                            ▼
   ┌──────────┐                 ┌──────────┐
   │  scribe  │                 │ archivist│
   │   bot    │                 │   bot    │
   └────┬─────┘                 └────┬─────┘
        │                            │
        ▼                            ▼
   ┌──────────────────────────────────────────┐
   │           memory/timeline/               │
   │   (append-only, verbatim, source-tagged) │
   └────────┬────────────────────┬────────────┘
            │                    │
            ▼                    ▼
      ┌──────────┐         ┌──────────┐
      │  Diary   │         │ Journal  │
      │ (verbatim│         │(synth'd  │
      │  view)   │         │ rollups) │
      └──────────┘         └──────────┘
```

### Timeline layout

```
memory/timeline/YYYY/MM/YYYY-MM-DD.md
```

One file per day, append events as they arrive. Each event:

```markdown
## 19:42 — Marge (memories room)
> [Voice note, 4m12s]

[Transcript verbatim. Kids talking about the school trip...]

📎 audio: assets/2026-05-23/19-42-marge.m4a
```

Frontmatter on each daily file carries source counts so the rollups
don't have to re-scan:

```yaml
sources:
  memories: 3   # → diary
  documents: 1  # → journal
```

### Diary rendering

The diary is essentially a chronological view of timeline entries
filtered to `memories` source. Two CLI variants:

- `stack docs diary --week 2026-W21` — quote-and-link view: each entry
  rendered verbatim with date, sender, attachments. No paraphrase.
- `stack docs diary --highlight --month 2026-05` — *optional* LLM pass
  that picks 3-5 standout moments to feature at the top, but full
  verbatim list still follows below. The LLM cannot replace, only
  surface.

### Journal rendering

The journal is the synthesized rollup, mirroring the existing
`stack docs overview` pattern:

- `stack docs journal --week 2026-W21` — LLM reads the week's
  documents/events and writes a factual summary: appointments,
  decisions, things that happened. Cites `[N]` back to source
  documents.
- `stack docs journal --month 2026-05` — same, monthly grain.

Output lands at `family/journal/2026-W21.md`, published to Forgejo via
the same path as `overview`.

## Time capsules (future, not v1)

Some diary entries are explicitly *for the future* — a letter to a son
to be read on his 18th birthday. The timeline schema should leave room
for this without building the surface yet:

```yaml
deliver_on: 2040-04-15      # date to surface
deliver_to: bart            # household member identifier
```

A future cron can scan for `deliver_on` dates that fall in the current
week and surface matching entries in that week's diary or as a separate
notification. Don't build this surface yet; just don't paint into a
corner.

## Diarization gap

Existing voice transcription works but cannot attribute speakers within
a single recording. The workaround is **Matrix sender attribution**:
the timeline entry credits *who recorded* it, not *who spoke in it*.

```
## 19:42 — Captured by Marge
> [Voice note, dinner conversation]

[Verbatim transcript follows...]
```

For a dinner-talk recording, "Captured by Marge" + transcript is good
enough. The reader (or the future deriver) can usually infer speakers
from context. Pyannote-style diarization is heavy and unnecessary for
v1.

## v1 scope

Smallest first piece, in order:

1. **Scribe bot** — listens to the memories room, files text-only
   messages into `memory/timeline/YYYY/MM/YYYY-MM-DD.md`. Sender,
   timestamp, message body. No voice, no images yet.
2. **Voice attachments** — route audio uploads through existing
   transcription, file transcript inline + audio asset linked.
3. **Image attachments** — file the image, link to Immich asset by
   hash if present.
4. **Diary CLI** — `stack docs diary --week` renders the verbatim
   weekly view.
5. **Journal CLI** — `stack docs journal --week` synthesizes the
   factual weekly view.
6. **Forgejo publish** — both surfaces publish via the same path the
   `overview` command uses.

Each step ships independently. The scribe bot is the only new
component; everything else reuses archivist patterns.

## Privacy posture

The memories room contains the most sensitive data in the entire
stack. Voice recordings of children, letters to family, dinner
conversations. This must never leave the box.

- Transcription stays on the local oMLX/Whisper stacklet
- LLM synthesis for the journal can use the local model only
- No "share to cloud" surface, even as an opt-in
- The diary's verbatim-preservation rule is also a privacy rule:
  paraphrasing a kid's voice note through a cloud LLM, even briefly,
  is the kind of thing this product exists to prevent

This is also the marketing story. *"Your family's dinner conversations,
transcribed and indexed, on hardware you own."* The privacy posture is
the differentiator from every cloud alternative.

## Related

- [[family-memory]] — the underlying vault structure
- [[knowledge-architecture]] — the Family Brain event bus pattern this
  extends
- [[ontology-v1]] — diary/journal rollups eventually feed back into the
  ontology (people, places, events recognized)
