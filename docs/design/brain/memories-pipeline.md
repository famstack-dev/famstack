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
- **Pages are a chronicle/diary hybrid, not transcript dumps.** Detail
  scales with the source: short memos stay verbatim, long recordings
  get a gist, a short narrative, selected word-for-word quotes, and
  the audio link. Quoted words are verified against the transcript;
  narrative renders as narrative. The full transcript stays in the
  transcript store; audio in Matrix is the archival original.
- **Privacy shape:** content flows machine-to-machine (Synapse →
  Whisper → oMLX → vault); only structure and compiled entries surface.

## Open

- Date fix at the source: a small upload path that stamps
  `dev.famstack.recorded_ts` into event content would eliminate the
  uncertain class for future memos. Family habit of speaking the date
  covers the past.
- Burst window (120s): settled differently than expected. Timing alone
  turned out to be the wrong signal -- three memos recorded a minute
  apart at the dinner table are not a sync burst, and calling them one
  filed a normal evening as undateable. A run now has to contradict
  itself (some memo says aloud it was made on a day its own timestamp
  disagrees with) before its timestamps are distrusted, which leaves
  the window doing nothing but grouping what arrived together.
- Join thresholds: gone. The model names which message finishes which,
  having both in front of it; the room checks the link is adjacent,
  same sender, same kind.

- The polish pass could take the household vocabulary too. Whisper now
  decodes against the family's names and topics, which is where a
  misheard name has to be fixed -- polish may not change words and
  should not. But the same vocabulary would help polish decide where
  sentences break around a proper noun it now knows is a name. Its
  contract does not move: clean sentences out of an imperfect
  transcription, same tone, same words.
- Re-transcription when the vocabulary changes. A new family member
  does not improve recordings already decoded, and re-running whisper
  over years of audio to pick up one name is the wrong default. An
  explicit `--retranscribe` would make it a choice.
