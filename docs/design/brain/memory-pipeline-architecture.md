# Memory Pipeline Architecture

Status: reference, September 2026. Describes the voice path from the
Matrix memories room to the published diary, the ontology layer, and
the mitigations this system needs because it runs on local models.

## Context and constraints

The pipeline runs on one Apple Silicon host (M1 Max, 64 GB). Models:
Qwen3.6-35B-A3B 4-bit on oMLX for text, whisper.cpp large-v3-turbo
for speech. Both are small compared to hosted models and show error
classes that hosted APIs rarely surface. The output is a family
archive: pages are read years later, by people who were in the
recordings. An error in a published page misstates someone's life.

Consequences for the design:

- Generated text is verified mechanically or constrained to closed
  answer sets. Prompt instructions alone did not prevent any of the
  failures listed under Pitfalls.
- Every model call has an output token cap derived from a measured
  ratio. Uncapped local calls monopolize the single GPU.
- All expensive results are cached, keyed by content, so a full
  recompile is affordable and deterministic.

## Component overview

```
Matrix memories room (source of truth, audio = archival original)
        │
        ▼
  transcription (whisper.cpp, configured flags, quality capture)
        │
        ▼
  transcript store   ~/famstack-data/core/transcripts/, 1 JSON/event
        │              {raw, text, quality, passes[]}
        ▼
  pass chain         gate → polish → correct → structure
        │              (lib/stack/ai/transcripts.py, versioned)
        ▼
  reading            facts per message: date, mode, addressee,
        │            fragment links, gist, candidate quotes
        ▼
  compile            date precedence, fragment joins, reply/caption
        │            attachment, entry assembly
        ▼
  render             language table, tiered entry formats
        │
        ▼
  wiki (Quartz)      pages in memory/brain, committed by curator
```

The room is append-only and the compiler is a fold over the full
history. There is no watermark: a reply or an edit can land on a
year-old entry, and only a full pass attaches it. Caches make the
fold cheap; they store cost, not position.

## Transcription

whisper.cpp flags, set in the LaunchAgent and reconciled on every
`stack up ai`:

| Flag | Value | Reason |
|---|---|---|
| `--language` | from `[core].language` | auto-detection fails on silence and noise; one incident recording of infant sounds was transcribed as CJK text |
| `--max-context` | 0 | a hallucinated segment otherwise seeds the next segment |
| `--suppress-nst` | on | drops non-speech tokens |

Each flag was tested against the three recordings that produced the
2026-09-14 incident. Each flag fixed loops the other two did not;
only the combination produced zero repetition on all three.

Transcription requests use `verbose_json` with word timestamps. The
stored quality record keeps per-segment `avg_logprob`,
`no_speech_prob`, `temperature`, a word count, and every word below
0.5 confidence. whisper computes these values on every call; the
plain `json` format discards them. Voice commands and chat notes use
the plain format and skip all quality machinery — the cost is only
paid where an archive is built.

## Transcript store and pass framework

One record per Matrix event, atomic writes, single-flight production.
Record fields:

- `raw` — whisper output, never modified by any pass
- `text` — current output of the pass chain
- `quality` — segment metrics and low-confidence words
- `passes[]` — `{name, version, outcome, model?, replacements?}`

The pass list makes model upgrades incremental: increasing a pass
version marks affected records stale (`stale_passes`), and a sweep
re-runs that single pass on stored raw text without re-running
whisper. `--retranscribe` exists for the case where whisper itself
changed.

| Pass | Function | Failure class covered |
|---|---|---|
| gate | empty the text when the transcript is hallucinated | repetition loops (measured 5–147 repeats of one 6-gram vs 1–2 in real speech); majority of segments failing whisper's own thresholds (avg_logprob < −1.0, no_speech_prob > 0.6) |
| polish | restore punctuation; word sequence verified unchanged | unreadable single-block output |
| correct | map low-confidence words to household names, closed set | misheard names (measured: p=0.19 on the one confirmed case) |
| structure | paragraph breaks at ≥1.5 s segment pauses, from word counts | wall-of-text rendering; no model call |

The gate keeps two independent signals because the failure classes
are disjoint: a repetition loop is a high-confidence failure the
logprob check does not see, and mumble is a low-confidence failure
the repetition check does not see.

## Ontology layer

Sources: person pages in the wiki (canonical spelling plus synonyms)
and `ontology.toml` topics. Consumers:

| Consumer | Use | Effect |
|---|---|---|
| whisper priming | names and topics as decoder prompt | fewer mishearings at the source |
| correct pass | closed replacement set | a repaired word is always a real name |
| summary prompt | list of family members | a name outside the list is treated as a mishearing and kept off the page |
| archivist (documents domain) | tags and correspondents | same principle, pre-existing |

The pattern in all four: the ontology converts an open generation
problem into selection from a known set. Selection is the reliable
operation at this model size.

## Reading, compilation, attribution

The reading returns facts per message, JSON, temperature 0: spoken
date, mode (monologue/dialogue/note), addressee, fragment links,
gist, candidate quotes. Rules with rationale:

- **Quotes are verified.** A candidate quote renders only if it
  matches one sentence or a consecutive run in the transcript
  (case/punctuation-insensitive). The page shows the transcript's own
  text. Quotes containing any low-confidence word are dropped.
- **Date precedence:** spoken date > live timestamp > unrecoverable.
  Matrix records only server receipt time; a synced message can be
  days off. Sync-burst messages without a spoken date are filed under
  the week they surfaced and labeled.
- **Attribution is limited to structural facts.** Sender and spoken
  addressee are known. Line-level attribution inside a conversation
  is unknown until diarization exists; summaries may name a
  conversation's participants and nothing finer.
- **Fragment joins are content decisions.** Three uploads in one
  second are usually three memos; a join requires one message to end
  mid-sentence and the next to continue it. Timing alone misfiled
  ordinary evenings as sync bursts before this rule.

Summary input is preprocessed: words the pipeline knows are unclear
are replaced with `[unclear]` before the model sees them. This
replaced an instruction ("do not use unreliable words"), which the
model had ignored.

## Rendering

- Entries under 120 words render verbatim.
- Longer recordings render as gist, verified quotes, folded full
  transcript, audio link.
- Messages addressed to one person render whole at any length.
- Recordings whose transcript the gate emptied render with a fixed
  explanatory sentence and the audio link.
- All reader-facing strings, month and weekday names come from a
  language table (`en`/`de`), selected once per run from config.
  Prompts that produce family-facing text name the target language
  explicitly; a model otherwise answers in the prompt's language.

## Pitfalls (incident-derived)

| Incident | Cause | Mitigation |
|---|---|---|
| 100k-token generation, 25 min GPU monopoly (2026-09-14) | polish is an echo task; a looping transcript has no natural end; no output cap; server default max_tokens was 128000 | gate before any echo task; caps at input size + 10% (measured output ratio 0.98–1.01); server default lowered |
| misheard name presented as a family member | whisper flagged the word (p=0.19) but the summary prompt's warning was ignored | correction pass (closed set), evidence redaction, ontology priming, quote filter |
| English output in a German diary | prompts are English; models answer in the prompt language | target language stated in every prompt; rendered strings from the language table |
| whisper dead after every `stack down ai` / `up ai` | stop hook unloads the LaunchAgent; nothing on the up path loaded it; `RunAtLoad` fires only at login | on_start reconciles the agent by content and loads it when absent |
| undatable memories | Matrix has no compose-time field; offline recordings carry sync time | spoken-date extraction; honest "unrecoverable" state; recording habit: say the date aloud |
| stale caches serving old wording | caches were keyed by content only; prompt changes did not change the keys | every cached artifact stores a fingerprint (hash) of the prompt or pass parameters that produced it; a prompt edit invalidates exactly the affected artifacts on the next compile |

## Open items

- Diarization for conversations (would upgrade attribution from
  participant-level to line-level; mode labels already mark the
  affected entries).
- Episode grouping: a vacation spanning many entries currently
  renders as independent entries plus one month summary.
- Retranscription (`--retranscribe`) is manual; it is needed only
  when whisper's configuration or vocabulary changes. Pass and prompt
  changes regenerate automatically via fingerprints during any
  compile, including the nightly one.
