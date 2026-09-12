# family-memories

A generator for a demo Memories room, set in the Simpsons world — the
companion to `family-docs`. It renders German voice memos (via the
stack's local Piper TTS), a kitchen-table dialogue, images, and diary
texts, then replays them into a **test rig's** Matrix room with the
same chaotic patterns a real Memories room accumulates. Built to
develop and test the diary compiler against known ground truth.

The **spec is the source of truth** (`spec.yaml`, one ordered
timeline). Rendered audio/images in `out/` are disposable artifacts.

## The patterns it reproduces

Observed in a real Memories room (metadata-level analysis, March 2026
onward) and deliberately kept messy:

| Pattern | The trap it sets for the compiler |
|---|---|
| addressed-memo | spoken "Hallo <Name>, heute ist der <Datum>" opening — date lives in the audio |
| batch-upload | 3 separate memos synced in one burst, out of order — upload timestamps are lies |
| fragment-pair | ONE recording split mid-sentence — looks identical to a batch upload |
| kitchen-table-dialogue | two speakers, needs diarization or dialogue-aware summarization |
| memo-no-date | live-sent, so the server timestamp happens to be right |
| memo-no-date-in-sync-burst | no spoken date AND a synced timestamp — date is unrecoverable; the compiler must say so |
| image-with-caption-reply | caption arrives ~90s later as a reply relation |
| image-no-context | nothing to anchor it but the timestamp |
| text-with-edit | `m.replace` — only the final version counts |
| text-reply-to-audio | commentary attached to a memo |
| implicit-context | text referencing "the picture above" with NO relation |

Every item carries `true_date` + `date_source` ground truth so
pipeline tests can score date recovery, fragment joining, and context
attachment against `out/manifest.json`.

## Usage

```sh
# render everything into out/ (needs the ai stacklet's speech service)
python tools/family-memories/generate.py            # both locales; --locale de/en

# replay into a TEST RIG (never production — the script refuses merles.eu)
python tools/family-memories/ingest.py \
    --homeserver http://<testrig>:42031 \
    --room '#memories:<testrig>' \
    --login marge:PW --login homer:PW --locale de
```

`ingest.py` writes `out/ingest-log.json` (item id → event id) for
assertions. Bursts land back-to-back; replies, edits, and the MSC3245
voice flag are sent exactly as real clients send them.

## What a replay cannot reproduce

Matrix stamps an event when the server receives it, and only an
application service may backdate one. So a replay lands the whole
corpus on the day it runs, and two things follow:

- **Live-timestamp dating is not scored.** Items with
  `date_source: live-timestamp` carry a `true_date` months before the
  replay date, and no compiler could recover it. They verify that a
  message *without* a spoken date falls back to its timestamp, not that
  the timestamp is right.
- **The burst window has to shrink.** In a real room, live messages sit
  hours or days apart and a sync burst lands within seconds, so 120s
  separates them. The replay compresses the live gaps to `--delay`
  (2s by default) while burst members still land ~0.1s apart. The ratio
  survives; the absolute threshold does not. Compile this corpus with
  `stack memory diary --burst-window 1`.

Everything else scores against ground truth as authored: spoken-date
recovery, fragment joining, burst grouping, the unrecoverable case,
edits, and caption attachment.

## What the corpus encodes about dates

Matrix stamps events with **server receipt time only** — there is no
compose-time field, and Element's offline queue discards it. Live-sent
messages have trustworthy timestamps; synced batches do not. The only
in-band recording date is the spoken opening. The compiler's date
logic (spoken date > live timestamp > burst-aware uncertainty) is
exactly what this corpus exercises.
