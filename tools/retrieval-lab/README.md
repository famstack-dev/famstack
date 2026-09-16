# retrieval-lab

An offline A/B bench for `stack memory search`. It runs the engines
against the same questions over the same fabricated vault and prints
what each one found, how fast, and how well its own confidence tracked
whether it was right.

No model, no containers, no network. One run is about two seconds, which
is the point: the agent rig measures how the family experiences an
answer, and this measures the retrieval underneath it. Use this one
first, because it changes an engine decision in seconds rather than in
LLM calls.

## Run it

```bash
python3 tools/retrieval-lab/generate.py                       # render the vault
uv run --extra test python tools/retrieval-lab/evaluate.py    # measure
uv run --extra test python tools/retrieval-lab/explain.py compound_tail
```

`generate.py` needs `pyyaml`, so run it under `uv run --extra test` too
if your system python3 lacks it. Output lands in `out/`, which is
gitignored and disposable.

## What is in here

| File | What it is |
|---|---|
| `corpus.yaml` | 27 answer pages plus the vocabulary for 150 distractors |
| `goldset.yaml` | 47 questions: 35 answerable, 12 with no answer in the vault |
| `generate.py` | renders `corpus.yaml` into `out/vault/` |
| `evaluate.py` | runs every engine over every question, prints the tables |
| `explain.py` | one question kind, side by side, for reading a regression |
| `freshness.py` | what the "is the index current" check costs as a vault grows |

## The engines it compares

| Arm | What it is |
|---|---|
| `regex` | today's `search_memory`: keywords OR'd into a regex, sorted by date |
| `fts5` | SQLite FTS5 over the vault, BM25 ranking, `unicode61 remove_diacritics 2` |
| `fts5+tri` | the above fused by RRF with a trigram (substring) index |

All three live behind `stacklets/memory/fts_index.py` and
`stacklets/memory/lib.py`. Nothing is reimplemented here, so a
measurement is about the shipping code and not about a copy of it.

## How the corpus avoids flattering the engine

A gold set written while looking at the pages it has to find will share
their vocabulary, and then any lexical engine looks better than it is.
Three rules keep that honest:

1. **Every fact page carries a `truth` line** stating what happened, in
   neutral words. The page body says the same thing the way somebody
   would actually have typed it.
2. **The questions in `goldset.yaml` are written from the truth lines
   only.** If you add a question, read the truth and not the body.
3. **Keywords are extracted mechanically** from the question by dropping
   stopwords. Both engines get the identical list. A hand-written
   keyword list is a thumb on the scale even when nobody means it to be.

Questions are tagged by kind (`paraphrase`, `compound_tail`, `fold`,
`absent`, ...) so a result can be traced to a case rather than being one
number that moved. `absent` questions have no answer in the vault at
all; getting those wrong is how a family ends up with a confident answer
about something nobody ever wrote down.

## What it does not measure

- **The model's rewrite.** Production `--nl` asks a model for keywords;
  this uses mechanical extraction so the engine is the only variable.
  Whether a model's keywords change the ranking is an agent-rig
  question.
- **Cross-language retrieval.** A German question reaches German pages.
  No lexical engine translates, and mixing the two in would measure
  translation rather than ranking.
- **What the family finally reads.** Retrieval feeding a good answer is
  a separate step, measured in `tools/agent-lab/rig/`.

Sample sizes are tens of questions, not thousands. `evaluate.py` prints
95% intervals on the confidence rates for that reason; read those before
treating a two-point difference as real.
