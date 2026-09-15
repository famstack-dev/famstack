# Agent improvement log

This log records the agent improvement project step by step. Each entry
has a date, an action, and a result. The harness in `tools/agent-lab/`
appends measurement entries. The goal of the project: fix context
pollution, fix response latency, keep memory access. The full review is
in `review-2026-09.md`. Source material for a future blog post.

## 2026-09-15 - Project start

- Problem: simple questions take 3 to 5 minutes. Context bleeds between
  threads and speakers. The todo flow is not reliable.
- We reviewed the code and the current research. Result: five latency
  multipliers stack on one endpoint. See `review-2026-09.md` section 1.
- Decision: build a validation harness first. Measure before each
  change. Log all results here.
- Rule for this log: Simplified Technical English (ASD-STE100).

## 2026-09-15 - Harness built

- New tool: `tools/agent-lab/lab.py` (stdlib only).
- `probe` finds the usage fields that oMLX reports.
- `cache` tests the central hypothesis: the `lean_state` history rewrite
  invalidates the oMLX prefix cache and forces a full re-prefill.
- Method: four phases. Cold sends a fresh synthetic conversation. Warm
  and warm2 only append turns. Mutate rewrites one early message, as
  `lean_state.py:64-103` does with prior tool results.

## 2026-09-15 12:00 UTC - lab probe

- endpoint: `http://localhost:8888/v1`, model: `Qwen3.6-35B-A3B-UD-MLX-4bit`
- raw result: `tools/agent-lab/results/20260915T120024Z-probe.json`

| phase | ttft_s | total_s | prompt_tokens | completion_tokens | cache fields |
|---|---|---|---|---|---|
| first | 1.092 | 1.093 | 34 | 2 | {"input_tokens": 34, "output_tokens": 2, "prompt_tokens_details": {"cached_tokens": 0}, "time_to_first_token": 1.09, "total_time": 1.09, "prompt_eval_duration": 1.09, "generation_duration": 0.0, "prompt_tokens_per_second": 31.33, "generation_tokens_per_second": 6961.56} |
| second (identical prompt) | 0.441 | 0.441 | 34 | 2 | {"input_tokens": 34, "output_tokens": 2, "prompt_tokens_details": {"cached_tokens": 0}, "time_to_first_token": 0.44, "total_time": 0.44, "prompt_eval_duration": 0.44, "generation_duration": 0.0, "prompt_tokens_per_second": 77.78, "generation_tokens_per_second": 7650.61} |

- note: first probe: find cache fields in usage

## 2026-09-15 12:01 UTC - lab cache

- endpoint: `http://localhost:8888/v1`, model: `Qwen3.6-35B-A3B-UD-MLX-4bit`
- raw result: `tools/agent-lab/results/20260915T120103Z-cache.json`

| phase | ttft_s | total_s | prompt_tokens | completion_tokens | cache fields |
|---|---|---|---|---|---|
| cold | 10.419 | 10.922 | 4413 | 31 | {"input_tokens": 4413, "output_tokens": 31, "prompt_tokens_details": {"cached_tokens": 0}, "time_to_first_token": 10.35, "total_time": 10.85, "prompt_eval_duration": 10.35, "generation_duration": 0.51, "prompt_tokens_per_second": 426.53, "generation_tokens_per_second": 61.19} |
| warm | 1.555 | 2.303 | 4461 | 43 | {"input_tokens": 4461, "output_tokens": 43, "prompt_tokens_details": {"cached_tokens": 4096}, "time_to_first_token": 1.51, "total_time": 2.26, "prompt_eval_duration": 1.51, "generation_duration": 0.75, "prompt_tokens_per_second": 2951.74, "generation_tokens_per_second": 57.15} |
| warm2 | 1.697 | 1.766 | 4520 | 8 | {"input_tokens": 4520, "output_tokens": 8, "prompt_tokens_details": {"cached_tokens": 4096}, "time_to_first_token": 1.66, "total_time": 1.73, "prompt_eval_duration": 1.66, "generation_duration": 0.07, "prompt_tokens_per_second": 2728.16, "generation_tokens_per_second": 110.34} |
| mutate | 9.986 | 10.044 | 4464 | 7 | {"input_tokens": 4464, "output_tokens": 7, "prompt_tokens_details": {"cached_tokens": 0}, "time_to_first_token": 9.95, "total_time": 10.01, "prompt_eval_duration": 9.95, "generation_duration": 0.06, "prompt_tokens_per_second": 448.7, "generation_tokens_per_second": 113.26} |

- note: hypothesis test: append-only turns hit the prefix cache, the lean_state-style rewrite forces re-prefill

## 2026-09-15 12:07 UTC - lab replay

- endpoint: `http://localhost:8888/v1`, model: `Qwen3.6-35B-A3B-UD-MLX-4bit`
- raw result: `tools/agent-lab/results/20260915T120700Z-replay.json`

**lean_state (production)**

| phase | ttft_s | total_s | prompt_tokens | cached_tokens | completion_tokens |
|---|---|---|---|---|---|
| turn 1 | 7.926 | 8.758 | 3390 | 0 | 48 |
| turn 2 | 7.656 | 8.25 | 3537 | 0 | 35 |
| turn 3 | 8.026 | 8.554 | 3684 | 0 | 32 |
| turn 4 | 8.26 | 8.794 | 3831 | 0 | 32 |

**append-only**

| phase | ttft_s | total_s | prompt_tokens | cached_tokens | completion_tokens |
|---|---|---|---|---|---|
| turn 1 | 7.276 | 8.088 | 3392 | 0 | 48 |
| turn 2 | 8.025 | 8.653 | 3728 | 0 | 37 |
| turn 3 | 8.805 | 9.368 | 4066 | 0 | 32 |
| turn 4 | 9.725 | 10.298 | 4403 | 0 | 34 |

- note: replay with the real lean_messages transform vs append-only; same scripted tool conversation

## 2026-09-15 12:09 UTC - lab replay

- endpoint: `http://localhost:8888/v1`, model: `Qwen3.6-35B-A3B-UD-MLX-4bit`
- raw result: `tools/agent-lab/results/20260915T120930Z-replay.json`

**lean_state (production)**

| phase | ttft_s | total_s | prompt_tokens | cached_tokens | completion_tokens |
|---|---|---|---|---|---|
| turn 1 | 15.955 | 16.764 | 6717 | 0 | 48 |
| turn 2 | 7.05 | 7.532 | 6864 | 4096 | 29 |
| turn 3 | 7.427 | 7.963 | 7010 | 4096 | 32 |
| turn 4 | 7.714 | 8.235 | 7157 | 4096 | 31 |

**append-only**

| phase | ttft_s | total_s | prompt_tokens | cached_tokens | completion_tokens |
|---|---|---|---|---|---|
| turn 1 | 14.86 | 15.694 | 6709 | 0 | 48 |
| turn 2 | 7.49 | 8.147 | 7044 | 4096 | 38 |
| turn 3 | 8.251 | 8.834 | 7379 | 4096 | 34 |
| turn 4 | 9.116 | 9.776 | 7715 | 4096 | 38 |

- note: block-size hypothesis: with a ~8K-token prefix the shared prefix spans 4096-token blocks; append-only should now hit, lean_state should hit only the system-prompt blocks

## 2026-09-15 - Findings from the endpoint experiments

Interpretation of the three runs above.

1. oMLX reports full cache telemetry in `usage`: `cached_tokens`,
   `time_to_first_token`, `prompt_eval_duration`, and token rates. The
   agent never reads this today. Step 1 of the plan stays valid.
2. The prefix cache works and it is fast. An append-only follow-up turn
   on a ~4.4K-token prompt: TTFT 1.5 s instead of 10.4 s.
3. An in-place rewrite of one early message removes the full cache hit.
   TTFT goes back to 10.0 s. This confirms the `lean_state` hypothesis.
4. New finding: the cache reuses the prefix in 4096-token blocks. A
   shared prefix below 4096 tokens gives `cached_tokens: 0`. The first
   replay run (prompts 3.4K to 4.4K tokens) hit zero cache in BOTH
   arms. The second run (~6.7K-token prefix) hit exactly 4096 in both
   arms. Consequence: each turn re-prefills up to 4096 tail tokens
   (~9 s at the measured 430-460 tokens/s). The block size is a
   serving-side lever. We must check if oMLX can use smaller blocks.
5. Measured prefill rate: 430-460 tokens/s cold on the 35B MoE. The
   production ~10K-token prefix therefore costs ~23 s per cold call.
   Twelve tool iterations with cache loss explain multi-minute turns
   without any failed tool call.

## 2026-09-15 - Next tier: isolated end-to-end rig

- Rationale: the endpoint lab proves mechanisms, but it simulates the
  agent. The user wants measurements through the real nanobot harness.
- Design: run the production agent image with the real runtime shims,
  driven by `nanobot agent -m` (a direct turn, no Matrix channel).
  A demo vault (fabricated data, git repo) replaces the family vault.
  `lab-api.py` (port 42011) replaces the production famstack-api for
  `stack memory` commands, with the same line protocol and exit codes,
  and the same optional LLM keyword-rewrite hop. `proxy.py` (port
  42012) sits between the agent and oMLX and records the byte-exact
  request and the usage object for every call.
- The proxy answers a standing requirement: see the exact context the
  agent sends on each request.
- Product goal recorded: lists (todo, shopping, checklist) must work
  seamlessly, similar to Apple's Lists. The rig scenario is therefore
  a shopping list. The rig write path (vault writes through lab-api)
  is the next rig increment.

## 2026-09-15 12:19 UTC - rig turn (rig:lists)

- message: `What is still open on the shopping list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 59.77 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 11281 | 0 | 58 | 27.87 | 29.082 |
| 2 | 4 | 11423 | 8192 | 55 | 9.31 | 10.437 |
| 3 | 6 | 11631 | 8192 | 80 | 9.91 | 11.554 |

- note: rig smoke test: first end-to-end turn through the real nanobot image, demo vault, lab-api, proxy

## 2026-09-15 12:20 UTC - rig turn (rig:lists)

- message: `Who usually adds items to the shopping list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 58.34 s, 4 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 8 | 11592 | 8192 | 88 | 10.42 | 12.256 |
| 2 | 10 | 11698 | 8192 | 67 | 10.09 | 11.492 |
| 3 | 12 | 11874 | 8192 | 98 | 10.62 | 12.636 |
| 4 | 14 | 12096 | 8192 | 46 | 11.6 | 12.556 |

- note: turn 2 in the same session: cross-turn cache with lean_state active; also a grounded question that needs person pages

## 2026-09-15 12:24 UTC - rig turn (dm:homer)

- message: `What is still on my errands list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 54.22 s, 4 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 11281 | 8192 | 61 | 9.49 | 10.809 |
| 2 | 4 | 11457 | 8192 | 54 | 9.57 | 10.664 |
| 3 | 6 | 11552 | 8192 | 52 | 9.66 | 10.722 |
| 4 | 8 | 11757 | 8192 | 89 | 10.18 | 11.984 |

- note: DM scenario: personal list, not topic bound; fresh state after vault extension

## 2026-09-15 12:25 UTC - rig turn (topic:camping)

- message: `Bart: what is still open on the packing list for the camping trip?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 41.58 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 11288 | 8192 | 72 | 9.54 | 11.006 |
| 2 | 4 | 11511 | 8192 | 55 | 9.5 | 10.59 |
| 3 | 6 | 11729 | 8192 | 98 | 10.1 | 12.099 |

- note: multi-person topic scenario: speaker prefix convention, camping packing list (homer+bart)

## 2026-09-15 - Findings from the first rig runs

The rig is live: real agent image, real shims, demo vault, lab-api,
logging proxy. Four turns measured (entries above). Results:

1. The real stable prefix is ~11,280 tokens, not the ~10K the runtime
   README estimates. It spans two full cache blocks (8192 tokens).
   These two blocks hit on every call, in every session, even a fresh
   one. Cross-session prefix sharing works.
2. The third block never stabilizes. The prompt ends at ~11.3K-12.1K
   tokens, and the tail past 8192 contains the end of the system
   prompt plus the mutable history. Every LLM call therefore
   re-prefills ~3.1K-3.9K tokens: ~9.5-11.6 s TTFT per call, on every
   call, cold or warm.
3. A simple list question costs 3-4 LLM calls (initial + one per tool
   iteration): 42-60 s wall time. This is the production latency
   floor. The observed 3-5 min turns are this floor times more
   iterations, plus the rewrite hop, plus contention.
4. Quality is good in the rig: correct list answers, correct person
   attribution, and grounded citations ("Sources: ...") in all four
   turns. The grounded-answers product goal seems reachable with the
   current model; latency is the blocker, not capability.
5. Levers, ranked by measured effect: (a) shrink the stable prefix
   below one block boundary distance from the prompt end, or shrink it
   in total (11.2K tokens for four small files plus tool schemas is a
   lot); (b) check if oMLX can use a smaller cache block size than
   4096; (c) fewer tool iterations per question (better retrieval
   instructions); (d) remove the lean_state rewrite so blocks past the
   prefix can stabilize between turns.
- Rig gap noted: lab-api's keyword-rewrite calls go to oMLX directly,
  so the proxy does not count them. Point lab-api at the proxy in the
  next runs.

Next scenario work: multi-person sessions with the speaker-prefix
convention, the gift-ideas pollution trap, and the rig write path for
list mutations (tick off, add item).

## 2026-09-15 12:35 UTC - rig turn (topic:birthday)

- message: `What is still open on the party checklist?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 41.0 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 11281 | 8192 | 54 | 9.43 | 10.569 |
| 2 | 4 | 11489 | 8192 | 56 | 9.72 | 10.91 |
| 3 | 6 | 11701 | 8192 | 70 | 10.12 | 11.51 |

- note: sanity check of the new AGENT_LEAN_STATE=0 switch: append-only history, birthday checklist scenario

## 2026-09-15 - oMLX settings analysis, switch from measuring to fixing

We inspected the oMLX v0.6.4 settings (UI screenshot and
`~/.omlx/settings.json`). Findings:

1. `scheduler.prefill_priority` is `"context"` (Max Context). This
   mode trades prefill speed for large-prompt headroom. Our measured
   430-460 tokens/s possibly runs in this slow mode.
2. `scheduler.chunked_prefill` is `false`. Enabled, it reduces TTFT
   when requests overlap. The keyword-rewrite hop overlaps with agent
   calls, so this applies.
3. The kernel GPU limit is 51.8 GB (`iogpu.wired_limit_mb`). The UI
   recommends `sudo sysctl iogpu.wired_limit_mb=59392`. The value does
   not survive a reboot; persistence needs a LaunchDaemon (note: this
   Mac reboots monthly).
4. There is no cache block-size setting. The `gdn_*` cache entries
   (Gated DeltaNet sidecars) indicate the model uses hybrid linear
   attention. Linear-attention layers carry a recurrent state, so the
   cache can only restore at state snapshots. This explains the strict
   4096-token cache granularity. The partial-block re-prefill per call
   is a model-architecture property, not a config mistake.
5. Hot cache is 6 GB, SSD cache 372 GB, `preserve_mid_system_cache`
   is on.

Decision: stop broad measuring, apply fixes one at a time, and re-run
the three rig scenarios (groceries, dm:homer, topic:camping) after
each change as the A/B check.

Fix order:
1. Serving side (operator action, needs an oMLX restart):
   `prefill_priority: "speed"`, `chunked_prefill: true`, raise the
   kernel limit. Expected effect: every call gets faster; this
   multiplies with all later fixes.
2. Retrieval consolidation (code): one documented retrieval route,
   align workspace AGENTS.md and SKILL.md, so a list question costs
   1-2 calls instead of 3-4.
3. lean_state A/B: the runtime now has an `AGENT_LEAN_STATE=0` switch
   (append-only history). Sanity check passed (entry above). The
   effect shows in longer sessions, once the prompt crosses the next
   4096-token boundary; short sessions are dominated by the partial
   block anyway.
4. Prefix diet: the stable prefix is 11,281 tokens. Target: well under
   8192, so the whole prefix fits two stable blocks and the mutable
   tail starts earlier and costs less.

## 2026-09-15 12:42 UTC - lab cache

- endpoint: `http://localhost:8888/v1`, model: `Qwen3.6-35B-A3B-UD-MLX-4bit`
- raw result: `tools/agent-lab/results/20260915T124224Z-cache.json`

| phase | ttft_s | total_s | prompt_tokens | cached_tokens | completion_tokens |
|---|---|---|---|---|---|
| cold | 1.897 | 2.413 | 4413 | 4096 | 31 |
| warm | 1.47 | 2.213 | 4461 | 4096 | 43 |
| warm2 | 1.644 | 1.71 | 4520 | 4096 | 8 |
| mutate | 1.537 | 1.585 | 4464 | 4096 | 7 |

- note: post-restart check: did the scheduler changes take effect? baseline cold prefill was 426-453 tok/s

## 2026-09-15 12:43 UTC - lab cache

- endpoint: `http://localhost:8888/v1`, model: `Qwen3.6-35B-A3B-UD-MLX-4bit`
- raw result: `tools/agent-lab/results/20260915T124303Z-cache.json`

| phase | ttft_s | total_s | prompt_tokens | cached_tokens | completion_tokens |
|---|---|---|---|---|---|
| cold | 10.879 | 11.32 | 4416 | 0 | 28 |
| warm | 1.593 | 2.447 | 4461 | 4096 | 48 |
| warm2 | 1.678 | 1.725 | 4525 | 4096 | 7 |
| mutate | 10.206 | 10.243 | 4468 | 0 | 6 |

- note: true cold measurement with fresh seed 99: prompts never seen, no SSD reuse possible; checks whether scheduler changes took effect

## 2026-09-15 - Serving-side settings: what we change and why

Change set for oMLX v0.6.4 (config: `~/.omlx/settings.json`) and the
kernel. Rationale per setting:

| Setting | From | To | Why |
|---|---|---|---|
| `scheduler.prefill_priority` | `context` | `speed` | "Context" trades prefill speed for large-prompt headroom under memory pressure. Our prompts are ~12K tokens, far below any limit. Prefill speed multiplies every LLM call, so the agent pays this cost 3-4 times per question. |
| `scheduler.chunked_prefill` | `false` | `true` | Interleaves prefill chunks with decode steps. Reduces TTFT when requests overlap. The agent's keyword-rewrite hop overlaps with agent calls, and family members ask in parallel. |
| kernel `iogpu.wired_limit_mb` | 51.8 GB ceiling | 59392 | The oMLX UI recommends this value. It raises the GPU memory ceiling, so the memory guard throttles prefill later. Caution: the value resets on reboot, and this Mac reboots monthly. Persistence needs a LaunchDaemon. |

Verification after the restart (entries above):

1. `iogpu.wired_limit_mb` is 59392. Applied.
2. The settings file still shows `prefill_priority: "context"` and
   `chunked_prefill: false`. The UI toggles did not persist.
3. Measurement agrees: true cold prefill (fresh seed 99) is 408
   tokens/s, the same as the 426-453 baseline. The scheduler changes
   are NOT active. Re-apply and re-verify before any A/B comparison.
4. Positive side finding: the SSD cache tier survives the server
   restart. A byte-identical prompt from an earlier run hit the cache
   in every phase, including the mutate phase. Deterministic prompts
   plus the cold tier give cross-restart reuse. For the agent this
   means the stable prefix stays warm across oMLX restarts.

## 2026-09-15 12:48 UTC - lab cache

- endpoint: `http://localhost:8888/v1`, model: `Qwen3.6-35B-A3B-UD-MLX-4bit`
- raw result: `tools/agent-lab/results/20260915T124818Z-cache.json`

| phase | ttft_s | total_s | prompt_tokens | cached_tokens | completion_tokens |
|---|---|---|---|---|---|
| cold | 10.144 | 10.601 | 4410 | 0 | 23 |
| warm | 1.551 | 2.096 | 4450 | 4096 | 32 |
| warm2 | 1.578 | 1.724 | 4498 | 4096 | 12 |
| mutate | 9.731 | 9.887 | 4447 | 0 | 7 |

- note: scheduler settings verified active (speed + chunked prefill): true cold measurement with fresh seed 123

## 2026-09-15 - Scheduler settings active: no single-request gain

The re-applied settings are saved (`prefill_priority: "speed"`,
`chunked_prefill: true`) and verified in the file. Fresh-seed cold
measurement: 437 tokens/s, identical to the 426-453 baseline.

Conclusion: "context" mode only trades speed under memory pressure,
and this machine is not under pressure with ~12K-token prompts. The
prefill rate ~440 tokens/s is the compute-bound hardware rate for
this 35B model on the M1 Max. Chunked prefill remains useful for
overlapping requests (rewrite hop, parallel family questions), not
for single-request speed.

Consequence: the serving side is done. All remaining latency levers
are in famstack code:
1. Fewer LLM calls per question (retrieval consolidation) - next.
2. Shorter stable prefix (11,281 tokens today).
3. Stable blocks across turns (lean_state A/B in long sessions).

## 2026-09-15 12:52 UTC - rig turn (topic:groceries)

- message: `What is still open on the shopping list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 72.83 s, 4 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 11411 | 0 | 59 | 28.44 | 29.624 |
| 2 | 4 | 11490 | 8192 | 57 | 9.54 | 10.701 |
| 3 | 6 | 11618 | 8192 | 58 | 9.88 | 11.05 |
| 4 | 8 | 11829 | 8192 | 76 | 10.42 | 11.949 |

- note: A/B after retrieval consolidation (brief pointers, one-search rule, no --nl hop); baseline: 59.8s / 3 calls

## 2026-09-15 12:58 UTC - rig turn (topic:groceries)

- message: `What is still open on the shopping list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 39.01 s, 4 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 9184 | 4096 | 56 | 13.73 | 14.95 |
| 2 | 4 | 9260 | 8192 | 49 | 3.5 | 4.463 |
| 3 | 6 | 9380 | 8192 | 59 | 3.85 | 5.019 |
| 4 | 8 | 9592 | 8192 | 65 | 4.36 | 5.625 |

- note: prefix diet A/B: disabled builtin skills memory+my, minimal USER.md, compressed AGENTS.md and family-memory skill; baseline 11281-token prefix, 59.8s / 3 calls

## 2026-09-15 13:00 UTC - rig turn (topic:groceries)

- message: `What is still open on the shopping list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 33.36 s, 4 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 9184 | 8192 | 68 | 5.03 | 8.736 |
| 2 | 4 | 9367 | 8192 | 56 | 3.76 | 4.865 |
| 3 | 6 | 9494 | 8192 | 58 | 4.16 | 5.337 |
| 4 | 8 | 9705 | 8192 | 54 | 4.65 | 5.695 |

- note: A/B rerun after lab-api regex fix; diet prefix; baseline 59.8s / 3 calls

## 2026-09-15 13:01 UTC - rig turn (topic:groceries)

- message: `What is still open on the shopping list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 23.48 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 9184 | 8192 | 60 | 4.14 | 5.3 |
| 2 | 4 | 9376 | 8192 | 59 | 3.79 | 4.974 |
| 3 | 6 | 9588 | 8192 | 54 | 4.42 | 5.493 |

- note: A/B rerun 2: lab-api date ranking fixed; diet prefix

## 2026-09-15 13:04 UTC - rig turn (dm:homer)

- message: `What is still on my errands list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 24.28 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 9184 | 8192 | 64 | 4.15 | 5.415 |
| 2 | 4 | 9387 | 8192 | 89 | 3.81 | 5.609 |
| 3 | 6 | 9629 | 8192 | 80 | 4.52 | 6.127 |

- note: A/B: DM scenario after diet + consolidation; baseline 54.2s / 4 calls

## 2026-09-15 13:04 UTC - rig turn (topic:camping)

- message: `Bart: what is still open on the packing list for the camping trip?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 23.79 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 9191 | 8192 | 80 | 4.29 | 5.886 |
| 2 | 4 | 9348 | 8192 | 51 | 3.77 | 4.772 |
| 3 | 6 | 9562 | 8192 | 73 | 4.32 | 5.789 |

- note: A/B: camping scenario after diet + consolidation; baseline 41.6s / 3 calls

## 2026-09-15 - Fix 2+3 results: retrieval consolidation and prefix diet

Changes measured together (entries above):

1. Retrieval consolidation: brief-pointer-first and one-search rule in
   workspace AGENTS.md and SKILL.md; `memory_search` builds an OR-regex
   from model keywords in code, which removes the hidden second LLM
   call (`--nl` rewrite) from every search.
2. Prefix diet: `disabled_skills: ["memory", "my"]` in config.json
   (nanobot built-in skills we never used), minimal USER.md seed
   instead of nanobot's ~1K-char profile template, token-lean rewrite
   of AGENTS.md and the family-memory skill.

Rig fidelity fixes on the way (lab-api, not agent code): treat the
query as a regex like the real CLI, and rank hits by frontmatter date
descending like the real CLI. Both bugs distorted one A/B run each.

Result table, same three scenarios, same questions:

| Scenario | Baseline | After fixes |
|---|---|---|
| topic:groceries | 59.8 s, 3 calls | 23.5 s, 3 calls |
| dm:homer | 54.2 s, 4 calls | 24.3 s, 3 calls |
| topic:camping | 41.6 s, 3 calls | 23.8 s, 3 calls |

- Stable prefix: 11,281 -> 9,184 tokens. System prompt: 21,988 ->
  13,319 chars.
- Warm calls: 9.5-11.6 s TTFT -> 3.5-4.7 s TTFT. The mutable tail
  past the 8192-token cache boundary shrank from ~3.4K to ~1.1K
  tokens.
- All replies stayed correct (verified from the session store).
- Open quality item: only 1 of 3 replies kept the Sources line; the
  baseline cited in 3 of 3. The skill compression likely cut citation
  compliance. Watch it; strengthen the Sources rule with few tokens
  if it stays weak.
- Remaining floor: 3 calls x ~5 s + container start. Next reductions:
  a 2-call turn (brief pointer straight to read_file; needs the room
  label, which the rig CLI mode does not provide), and a shorter tail.
- Queued (user request): research pragmatic retrieval/search
  upgrades. Candidates: SQLite FTS5/BM25 index over the vault, and
  hybrid search with a small embedding model. oMLX already serves
  embedding models (Qwen3-Embedding-8B, bge-m3, LFM2.5-Embedding).
