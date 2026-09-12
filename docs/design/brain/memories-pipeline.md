# Memories Processing Pipeline

Status: **draft** — companion to [diary-journal.md](diary-journal.md)
(which covers the timeline/diary/journal split and rendering). This doc
covers the step before: turning a messy, already-populated memories
room into clean timeline entries. Grounded in a structural probe of the
real room (Sept 2026) and scored by the `tools/family-memories` corpus.

## What the room actually contains

| Pattern | Consequence for the pipeline |
|---|---|
| Voice memos with spoken date openings ("Hallo X, heute ist der …") | the date is in the audio, nowhere else |
| Sync bursts: offline recordings uploaded together, out of order | `origin_server_ts` = sync time, days off; ordering within burst is meaningless |
| True fragments: one recording split mid-sentence | must be joined before summarizing |
| Kitchen-table dialogues (2+ speakers) | need dialogue-aware handling, not verbatim monologue treatment |
| Images + late caption via reply relation | caption belongs to the image |
| Texts referencing "the picture above" with no relation | implicit context, adjacency-based |
| Long texts with `m.replace` edits | only the final version counts |

Matrix stores **no compose time** — verified against the spec and the
room's own events. Element strips filenames and metadata. A synced
memo without a spoken date has an unrecoverable date.

## Pipeline

```
room history (paginated, oldest-first)
  → 1 resolve      edits collapsed, replies attached, bursts detected
  → 2 transcribe   Whisper; cached in TRANSCRIPT_DIR (never re-pay GPU)
  → 3 classify     per item, local LLM, structured output:
                   {mode, spoken_date?, fragment_boundary?, addressee?}
  → 4 join         fragment pairs merged (ends-mid-thought ⨯ continues)
  → 5 date         spoken > live timestamp > burst ⇒ UNCERTAIN
  → 6 compile      timeline entries (diary-journal.md takes over)
```

1. **Resolve** — pure Matrix mechanics, no AI: apply `m.replace`, attach
   reply-captions to parents, group events <120s apart from the same
   sender into candidate bursts. Keep event ids for idempotency.
2. **Transcribe** — existing Whisper path + `TRANSCRIPT_DIR` cache
   (core already reserves it for exactly this backfill).
3. **Classify** — one structured-output call per transcript
   (temperature 0): monologue/dialogue, spoken date if present,
   starts/ends mid-thought, addressee. This worked cleanly in the probe.
4. **Join** — a burst is NOT a fragment chain (the probe's key trap:
   three same-second uploads were three independent memos). Join only
   when A ends mid-thought AND B continues it — an LLM judgment on the
   pair, not a timing heuristic.
5. **Date** — the timestamp is usually right: most messages are sent
   live, so `origin_server_ts` is the default. Spoken dates override
   it when present. Only messages inside a detected sync burst get the
   exception treatment — there the timestamp is days off, so without a
   spoken date the entry is dated "week of <sync date>" and marked
   uncertain rather than confidently wrong.
6. **Compile** — entries carry `{date, date_confidence, kind, people,
   transcript/summary, assets, source event ids}` into
   `memory/timeline/`, per diary-journal.md.

## Decisions

- **Backfill-first.** The room is already populated; the compiler is a
  rerunnable batch over full history, incremental later. Idempotency
  via source event ids in each timeline entry.
- **Diarization stays out of v1** (per diary-journal.md), but step 3
  cheaply *labels* dialogues, so the renderer can mark them "captured
  by X, conversation" and a later diarization pass knows exactly which
  few recordings to touch.
- **Goal is topics, not verbatim accuracy.** Whisper large-v3-turbo was
  rated clean on real German memos; good enough. No model change needed.
- **Privacy shape:** content flows machine-to-machine (Synapse →
  Whisper → oMLX → vault); only structure and compiled entries surface.

## Open

- Date fix at the source: a small upload path that stamps
  `dev.famstack.recorded_ts` into event content would eliminate the
  uncertain class for future memos. Family habit of speaking the date
  covers the past.
- Burst window (120s) and join thresholds: tune against the corpus
  (`true_date` / `fragment_of` ground truth in the manifests).
