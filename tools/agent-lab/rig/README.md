# agent-lab rig

An isolated end-to-end rig for the famstack agent. It runs the real
agent image, the real runtime shims, and the real tool loop. It
replaces every production dependency:

| Production | Rig |
|---|---|
| Matrix channel | `nanobot agent -m` (direct turn, no channel) |
| family vault | `demo-vault/` (fabricated data, git repo) |
| famstack-api (port 42001) | `lab-api.py` (port 42011) |
| oMLX direct | `proxy.py` (port 42012, logs every request) |

The proxy records the byte-exact request and the usage object for
every LLM call in `state/proxy-log.jsonl`. This shows the full context
the agent sends, per request.

## Usage

```
python3 tools/agent-lab/rig/rig.py build
python3 tools/agent-lab/rig/rig.py reset
python3 tools/agent-lab/rig/lab-api.py --model <model> &   # port 42011
python3 tools/agent-lab/rig/proxy.py &                     # port 42012
python3 tools/agent-lab/rig/rig.py turn "What is open on the shopping list?" --session rig:lists
```

`turn` prints the agent reply, then a metrics summary: wall time, LLM
calls, prompt tokens, cached tokens, TTFT per call. Each turn appends
an entry to `docs/design/agent/agent-improvement-log.md` (`--no-log`
to skip, `--note` to add context).

## Scenarios

The demo vault holds multiple lists across topics and persons:

| Scenario | Session | Data |
|---|---|---|
| Shared topic list | `topic:groceries` | `family/groceries/todos.md` (homer, marge) |
| Multi-person topic | `topic:camping` | `family/camping/todos.md` (homer, bart) |
| Multi-person topic | `topic:birthday` | `family/birthday/todos.md` (marge, lisa) |
| DM, not topic bound | `dm:homer` | `homer/notes/2026/09/errands.md` |
| DM, private data | `dm:marge` | `marge/notes/2026/09/gift-ideas.md` |

Conventions:

- One session per simulated room or DM: `--session topic:camping`,
  `--session dm:homer`.
- In multi-person sessions, prefix the message with the speaker:
  `"Bart: what is open on the packing list?"`.
- The gift-ideas page is a pollution trap. A correct agent does not
  surface it in the birthday topic session.

## Limitations (rig v1)

- Read path only. Vault writes (`stack memory write`) return exit 126
  from lab-api. The write path is the next rig increment; the
  seamless-lists goal needs it.
- No real Matrix sender. In production, the brief derives the speaker
  from the Matrix mxid. The CLI turn has no sender, so the speaker
  line in the brief can differ from production.
- lab-api reimplements `memory search/person/history` over the demo
  vault. Same protocol and exit codes, simpler ranking.

## Reading the results

Every `turn` prints a JSON summary and appends a table to
`docs/design/agent/agent-improvement-log.md`. The proxy also records the
byte-exact request and the oMLX `usage` object per LLM call in
`state/proxy-log.jsonl`. Read the log table first; open the proxy log when
you need the exact context a call sent.

### KPIs, and what each one tells you

| KPI | Where | Good | What it means |
|---|---|---|---|
| `wall_s` | turn summary | lower | Total time the family waits for one answer. The headline number. |
| `llm_calls` | turn summary | fewer | Model round trips for one question: initial answer plus one per tool iteration. Each call re-sends the prefix, so this multiplies latency. |
| `prompt_tokens` | per call | stable | Size of the context sent. The first call's value is the prefix plus the question; later calls add history. |
| `cached_tokens` | per call | high, stable | Prefix tokens oMLX served from cache. This is the single most important number. See the block rule below. |
| `ttft_s` | per call | low | Time to first token = prefill time. Tracks `(prompt_tokens - cached_tokens)`: uncached tokens re-prefill at ~440 tok/s on the M1 Max. |
| `duration_s` | per call | low | Full call time (prefill + generation). |

### The cache-block rule (read this before tuning tokens)

oMLX restores the prefix cache only at 4096-token block boundaries. Per-call
prefill cost is roughly `(prompt_tokens mod 4096) + new history tokens`.
Consequence: a smaller prefix is only faster if it lands just ABOVE a 4096
multiple. Cutting tokens blindly can cross a boundary and double per-call
latency (measured: 47 s vs 17.5 s for the same question). The `zz-cache-pad`
workspace skill parks the prefix just past 8192; re-measure `prompt_tokens`
with a trivial turn (`turn "Hi"`) and retune the pad after any prefix change.
Full detail: `docs/design/agent/agent-improvement-log.md`.

### How to read a turn

1. Look at `cached_tokens` on calls after the first. If it is ~one or two
   full blocks (4096, 8192) and stable across turns, the cache is working.
   If it drops to 0 on a follow-up, something rewrote the prefix (a prefix
   file changed, or a mid-history mutation like the old `lean_state` rewrite).
2. Count `llm_calls`. A read question should be 2-3, an edit 2-4. More means
   the model is probing: wrong retrieval route, ambiguous tool, or a search
   that returns unranked noise.
3. Check `ttft_s` against uncached tokens. High TTFT with high uncached count
   is a prefill problem (prefix too far past a block boundary). High TTFT
   with everything cached points at oMLX load or a cold start.
4. Open `state/proxy-log.jsonl` to see the actual tool calls and the exact
   context. This is how you tell "the model chose the wrong tool" from "the
   tool returned bad results".

### A/B method

Change one thing, keep the same question and session name, compare the log
tables. For an isolated behavior toggle, pass it as env, for example
`--env AGENT_TOOL_TRIM=0` or `--env AGENT_THREAD_SESSIONS=0`. Always keep a
baseline run so a result is a comparison, not a vibe check. For search-engine
work, keep a `--backend regex` baseline (see the retrieval handover).

### Validating correctness, not just speed

Latency KPIs say nothing about whether the answer was right. After a run,
read the reply and the resulting vault state:
- final reply text: `state/nanobot/workspace/sessions/<session>.jsonl`, the
  last assistant message with real content (not a `[...]` lean placeholder).
- vault end state: `state/vault/...` and `git -C state/vault log --oneline`.
- Sources line present and links resolvable for any answer from the vault.

## Rules

- Fabricated data only in `demo-vault/`. Never copy family content.
- The rig binds lab services to 127.0.0.1 and uses its own ports.
  It must never point at port 42001 (production famstack-api).
