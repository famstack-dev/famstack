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

## 2026-09-15 13:23 UTC - rig turn (topic:groceries)

- message: `What is still open on the shopping list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 47.07 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 7442 | 0 | 57 | 17.49 | 18.697 |
| 2 | 4 | 7631 | 4096 | 58 | 8.9 | 10.023 |
| 3 | 6 | 7842 | 4096 | 65 | 9.48 | 10.732 |

- note: A/B: tool trim (6 schemas dropped) + Sources compliance fix

## 2026-09-15 13:24 UTC - rig turn (dm:homer)

- message: `What is still on my errands list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 45.8 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 7442 | 4096 | 66 | 9.34 | 10.616 |
| 2 | 4 | 7647 | 4096 | 81 | 8.88 | 13.323 |
| 3 | 6 | 7881 | 4096 | 95 | 9.56 | 14.437 |

- note: A/B: tool trim (6 schemas dropped) + Sources compliance fix

## 2026-09-15 13:24 UTC - rig turn (topic:camping)

- message: `Bart: what is still open on the packing list for the camping trip?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 38.42 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 7449 | 4096 | 67 | 9.24 | 10.545 |
| 2 | 4 | 7655 | 4096 | 45 | 8.95 | 9.813 |
| 3 | 6 | 7863 | 4096 | 79 | 9.46 | 10.997 |

- note: A/B: tool trim (6 schemas dropped) + Sources compliance fix

## 2026-09-15 13:27 UTC - rig turn (topic:groceries)

- message: `What is still open on the shopping list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 17.5 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 8364 | 8192 | 69 | 2.05 | 3.426 |
| 2 | 4 | 8565 | 8192 | 59 | 1.74 | 2.889 |
| 3 | 6 | 8777 | 8192 | 77 | 2.34 | 3.862 |

- note: A/B: tool trim + cache-pad alignment (prefix ~8330, two stable blocks)

## 2026-09-15 13:27 UTC - rig turn (dm:homer)

- message: `What is still on my errands list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 33.8 s, 6 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 8364 | 8192 | 90 | 2.09 | 3.86 |
| 2 | 4 | 8474 | 8192 | 52 | 1.53 | 2.547 |
| 3 | 6 | 8567 | 8192 | 52 | 1.74 | 2.756 |
| 4 | 8 | 8772 | 8192 | 83 | 2.33 | 3.974 |
| 5 | 10 | 8898 | 8192 | 56 | 2.65 | 3.754 |
| 6 | 12 | 8993 | 8192 | 186 | 2.79 | 6.511 |

- note: A/B: tool trim + cache-pad alignment (prefix ~8330, two stable blocks)

## 2026-09-15 13:27 UTC - rig turn (topic:camping)

- message: `Bart: what is still open on the packing list for the camping trip?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 16.73 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 8371 | 8192 | 69 | 2.1 | 3.46 |
| 2 | 4 | 8517 | 8192 | 46 | 1.66 | 2.555 |
| 3 | 6 | 8726 | 8192 | 74 | 2.21 | 3.685 |

- note: A/B: tool trim + cache-pad alignment (prefix ~8330, two stable blocks)

## 2026-09-15 - Fix 4: tool trim, and the cache-block alignment finding

Tool trim: a new runtime shim (tool_trim.py, AGENT_TOOL_TRIM=0 to
disable) drops six unused tool schemas the config cannot disable:
cron, long_task, complete_goal, spawn, write_stdin,
list_exec_sessions. Prefix: 9,184 -> 7,442 tokens.

Result: wall times got WORSE (47/46/38 s). The rows show why: with a
7,442-token prefix only one 4096-token cache block lies fully inside
the stable prefix, so every call re-prefilled ~3.4K tokens (~9 s).
The 9,184-token prefix sat just past the 8,192 boundary and paid only
a ~1K tail.

Rule for this serving stack (4096-token snapshot blocks):
per-call prefill cost ~= (prefix mod 4096) + new history tokens.
A smaller prefix is only faster if it lands just ABOVE a block
boundary. Blind token cutting can cross a boundary and double the
per-call cost.

Fix: a declarative alignment pad, workspace skill `zz-cache-pad`
(inert text, always-on, sorts last among skills). Tuned in the rig to
land the prefix at ~8,330 tokens, just past 8,192.

Result table (same scenarios; baseline from this morning):

| Scenario | Baseline | Diet (9,184) | Trim (7,442) | Trim+pad (~8,330) |
|---|---|---|---|---|
| topic:groceries | 59.8 s | 23.5 s | 47.1 s | 17.5 s |
| dm:homer | 54.2 s | 24.3 s | 45.8 s | 33.8 s* |
| topic:camping | 41.6 s | 23.8 s | 38.4 s | 16.7 s |

Warm TTFT is now 1.5-2.8 s per call with both blocks cached.

*The DM turn took 6 calls: a rig artifact. Without a Matrix sender
the brief cannot name the speaker, so the model searched for who
"my" refers to. Production supplies the speaker line per turn. Rig
convention from now on: prefix the speaker in every message, DMs
included.

## 2026-09-15 - Retrieval research result (queued work)

Research verdict for the search upgrade (full brief in the session,
key sources: sqlite.org/fts5, arXiv 2605.15184 "Is Grep All You
Need?", Liquid AI LFM2.5-Embedding):

1. First iteration: SQLite FTS5 + BM25, one DB file next to the vault
   clone, incremental upsert on git change, `unicode61
   remove_diacritics 2` tokenizer (German umlauts), quoted prefix
   queries from the model's 2-4 keywords, snippet() output. Stdlib
   only, <10 ms per query. Optional trigram companion table for
   German compound words.
2. Stretch: hybrid via sqlite-vec + LFM2.5-Embedding-350M (already
   served by oMLX), RRF fusion, whole-page embeddings. ~50-250 ms.
   Evidence says hybrid mainly cuts search iterations on paraphrase
   and cross-language queries; lexical-first is the 2026 consensus
   for iterating agents.
3. Skip: Meilisearch/Typesense (extra service), DuckDB FTS (no
   incremental update), tantivy (unneeded below ~100K docs).

Next work package (user priority): map the context build for threaded
communication (nanobot session keys, max_messages replay, idle
compaction), fold the thread root into the session key, and cap long
threads.

## 2026-09-15 - Fix 5: thread-scoped sessions and the context-build map

How nanobot 0.2.2 builds context (mapped from the pinned source):

1. Session key: `channel:chat_id` (the room id), unless the inbound
   message carries `session_key_override` - a seam upstream added
   exactly for thread-scoped sessions, unused by the Matrix channel.
2. History replay: last `max_messages` (default 120) messages, then a
   token-budget slice from the tail.
3. Idle compaction: after `session_ttl_minutes` (default 15) idle,
   the session is archived and an LLM summary replaces it on resume.
   This is compaction at a natural boundary; it also resets the
   cache at a moment nobody is waiting.

Changes:

1. New runtime shim thread_session.py (AGENT_THREAD_SESSIONS=0 to
   disable): threaded messages get
   `matrix:<room>#thread:<root>` as session key. Threads stop
   sharing the room transcript; replies still route by room id.
   Known v1 gap: a new thread session does not carry the root
   message's text (it lives in the room session).
2. `max_messages: 60` in config.json: a hard cap for long threads on
   top of the idle compaction. When the cap engages, the history
   window slides and costs a cache re-prefill; the 15-minute
   compaction keeps that case rare.
3. Verification: all four patch points confirmed in the built image
   (handle_with_thread_session, register_trimmed,
   _build_messages_lean, _runtime_lines).

Validation gap: the rig cannot produce real Matrix thread events.
Thread isolation needs a manual Matrix test (two parallel threads in
one room, check that answers do not cross) or the e2e rig lane.

## 2026-09-15 14:02 UTC - rig turn (topic:birthday)

- message: `Marge: what is still open on the party checklist, and is the science kit gift wrapped yet?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 59.45 s, 5 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 8469 | 0 | 132 | 20.5 | 23.141 |
| 2 | 5 | 8702 | 8192 | 108 | 2.09 | 4.27 |
| 3 | 8 | 8988 | 8192 | 134 | 2.85 | 5.534 |
| 4 | 11 | 9185 | 8192 | 128 | 3.33 | 5.912 |
| 5 | 14 | 10016 | 8192 | 187 | 5.46 | 9.224 |

- note: batch-search test: two independent lookups in one turn via queries[]

## 2026-09-15 - Fix 6: batched search, cap lowered to 40

Question 1: is max_messages 60 too long? Grounded answer: the token
budget in nanobot's history replay only activates when a context
window is configured, and ours is not. So max_messages is the only
cap. 60 messages of chat plus tool results is ~5-9K history tokens on
top of the ~8.3K prefix; with lean_state on, that tail re-prefills
every call (~11-20 s/call at the top). Thread-scoped sessions and the
15-minute idle compaction keep typical sessions far below the cap, so
the cap is a backstop. Lowered to 40: rare loss, bounded worst case.

Question 2: multiple searches per tool call? nanobot executes ALL
tool calls of one assistant message in one iteration, so parallel
calls already collapse round trips. Added both levers:
1. memory_search gains an optional `queries` array (up to three
   keyword sets, run concurrently, labeled result blocks).
2. One skill line instructs batching independent lookups.

Rig test (two-part question, topic:birthday): the model emitted TWO
parallel memory_search calls in iteration 1. Both question parts
answered correctly with correct Sources. 5 iterations total, because
part two lives in marge's personal bucket and the family/ scope
guidance made the first probes miss.

Two observations for the backlog:
1. Scope-first guidance costs iterations when the answer is in a
   personal bucket. The FTS5 upgrade with ranked global search will
   remove most of this class.
2. Privacy gap found by the gift-ideas trap: Marge asked in the
   shared birthday room, and the reply named her private gift note
   content and linked the page. No page-audience concept exists yet.
   Needs a design (audience frontmatter or bucket rules) before
   private notes and shared rooms mix in production.

## 2026-09-15 14:07 UTC - rig turn (topic:groceries)

- message: `Homer: please add butter to the shopping list`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 30.26 s, 5 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 8458 | 8192 | 72 | 2.18 | 3.606 |
| 2 | 4 | 8642 | 8192 | 55 | 1.98 | 3.043 |
| 3 | 6 | 8850 | 8192 | 101 | 2.5 | 4.526 |
| 4 | 8 | 8975 | 8192 | 88 | 2.81 | 4.568 |
| 5 | 10 | 9084 | 8192 | 37 | 3.07 | 3.788 |

- note: write-path scenario 1: add an item

## 2026-09-15 14:08 UTC - rig turn (topic:groceries)

- message: `Homer: how many items are still open on the shopping list, and which ones?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 18.3 s, 2 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 12 | 9258 | 8192 | 76 | 4.22 | 5.74 |
| 2 | 14 | 9512 | 8192 | 105 | 4.19 | 6.259 |

- note: staleness test, lean_state ON: list changed out of band after the agent's last read; correct answer is 6 open incl. eggs+flour, rye bread ticked

## 2026-09-15 14:10 UTC - rig turn (stale:off)

- message: `Homer: how many items are still open on the shopping list, and which ones?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 10.89 s, 1 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 8 | 9013 | 8192 | 77 | 3.77 | 5.313 |

- note: staleness test, lean_state OFF: prior read still verbatim in context; correct answer excludes oat milk and includes jam

## 2026-09-15 - Rig write path, and the staleness A/B

Write path shipped in the rig: lab-api now implements
`memory write <page> --by <actor> [--patch] [--dry-run]` with the
production buffer-file contract, checkbox-aware diff sentences
("added 1: butter"), and git commits authored as the actor.
Scenario "Homer: please add butter to the shopping list": correct
apply_patch, correct file state, correct commit, reply relays the
store's sentence. 5 calls, 30.3 s.

Staleness A/B (the question: what happens when the agent must rely
on current data but stale data sits in its context). Setup: agent
reads the list, another person edits the list out of band, agent is
asked again.

| Arm | Behavior | Answer |
|---|---|---|
| lean_state ON (production) | prior read decayed to a re-run pointer; model re-read the page (2 calls, 18.3 s) | CURRENT list: eggs+flour present, rye bread gone. Cosmetic slip: wrote "7" above a correct 6-item list. |
| lean_state OFF (append-only) | one LLM call, no re-fetch | STALE list: oat milk still open, jam missing, and a Sources line citing the page it never re-read |

Decision: lean_state STAYS. The review's removal recommendation is
reversed. With the aligned prefix its rewrites land in the tail that
re-prefills anyway, so it costs nothing until history crosses the
next 4096 boundary, which the 40-message cap and idle compaction
bound. The AGENT_LEAN_STATE switch remains for experiments.

Open staleness risk noted: the idle-compaction summary is written by
the consolidator LLM and could bake point-in-time facts ("the list
has 5 items") into the resumed context with no pointer to decay.
Check the consolidator prompt; if needed, shim a "state facts as
of-then, not as current" instruction into it.

## 2026-09-15 14:15 UTC - rig turn (topic:groceries)

- message: `Homer: what is on the shopping list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 17.58 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 8458 | 8192 | 68 | 2.14 | 3.472 |
| 2 | 4 | 8638 | 8192 | 45 | 1.93 | 2.811 |
| 3 | 6 | 8836 | 8192 | 61 | 2.5 | 3.702 |

- note: list-lifecycle test 1/4: read

## 2026-09-15 14:15 UTC - rig turn (topic:groceries)

- message: `Homer: please add butter, eggs and flour to the list`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 21.25 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 8 | 8776 | 8192 | 63 | 3.19 | 4.418 |
| 2 | 10 | 8992 | 8192 | 127 | 2.83 | 5.359 |
| 3 | 12 | 9144 | 8192 | 44 | 3.27 | 4.128 |

- note: list-lifecycle test 2/4: add three items

## 2026-09-15 14:16 UTC - rig turn (topic:groceries)

- message: `Homer: please restructure the list by grocery category (dairy, bakery, produce, household)`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 33.98 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 14 | 9273 | 8192 | 55 | 4.35 | 5.44 |
| 2 | 16 | 9505 | 8192 | 500 | 4.09 | 14.273 |
| 3 | 18 | 10028 | 8192 | 70 | 5.51 | 6.917 |

- note: list-lifecycle test 3/4: restructure by category via write_file

## 2026-09-15 14:17 UTC - rig turn (topic:groceries)

- message: `Homer: show me the new list, and cross off the apples`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 45.54 s, 4 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 20 | 10193 | 8192 | 56 | 6.71 | 7.82 |
| 2 | 22 | 10478 | 8192 | 127 | 6.73 | 9.285 |
| 3 | 24 | 10661 | 8192 | 174 | 7.15 | 10.688 |
| 4 | 26 | 10858 | 8192 | 119 | 7.78 | 10.213 |

- note: list-lifecycle test 4/4: show restructured list and tick one item

## 2026-09-15 14:21 UTC - rig turn (topic:groceries)

- message: `Homer: what is on the shopping list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 28.73 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 8502 | 4096 | 70 | 11.97 | 13.468 |
| 2 | 4 | 8684 | 8192 | 45 | 2.06 | 2.941 |
| 3 | 6 | 8882 | 8192 | 64 | 2.59 | 3.856 |

- note: list-lifecycle rerun after frontmatter guard + restore rule

## 2026-09-15 14:21 UTC - rig turn (topic:groceries)

- message: `Homer: please add butter, eggs and flour to the list`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 22.07 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 8 | 8822 | 8192 | 63 | 3.3 | 4.565 |
| 2 | 10 | 9038 | 8192 | 121 | 3.0 | 5.4 |
| 3 | 12 | 9184 | 8192 | 44 | 3.37 | 4.237 |

- note: list-lifecycle rerun after frontmatter guard + restore rule

## 2026-09-15 14:22 UTC - rig turn (topic:groceries)

- message: `Homer: please restructure the list by grocery category (dairy, bakery, produce, household)`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 77.29 s, 7 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 14 | 9313 | 8192 | 55 | 4.53 | 5.636 |
| 2 | 16 | 9545 | 8192 | 614 | 4.29 | 16.957 |
| 3 | 18 | 10182 | 8192 | 108 | 5.97 | 8.168 |
| 4 | 20 | 10332 | 8192 | 88 | 6.32 | 8.075 |
| 5 | 22 | 10476 | 8192 | 62 | 6.74 | 7.967 |
| 6 | 24 | 10750 | 8192 | 169 | 7.44 | 10.871 |
| 7 | 26 | 10943 | 8192 | 145 | 7.92 | 10.884 |

- note: list-lifecycle rerun after frontmatter guard + restore rule

## 2026-09-15 14:23 UTC - rig turn (topic:groceries)

- message: `Homer: show me the new list, and cross off the apples`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 57.04 s, 4 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 28 | 11250 | 8192 | 56 | 9.7 | 10.837 |
| 2 | 30 | 11519 | 8192 | 81 | 9.49 | 11.112 |
| 3 | 32 | 11656 | 8192 | 174 | 9.97 | 13.546 |
| 4 | 34 | 11853 | 8192 | 106 | 10.47 | 12.651 |

- note: list-lifecycle rerun after frontmatter guard + restore rule

## 2026-09-15 14:35 UTC - rig turn (topic:groceries)

- message: `Homer: what is on the shopping list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 25.78 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 8252 | 4096 | 56 | 11.28 | 12.528 |
| 2 | 4 | 8440 | 8192 | 55 | 1.37 | 2.459 |
| 3 | 6 | 8648 | 8192 | 65 | 2.0 | 3.301 |

- note: list_edit experiment, prose instructions

## 2026-09-15 14:35 UTC - rig turn (topic:groceries)

- message: `Homer: please add butter, eggs and flour to the list`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 34.14 s, 4 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 8 | 8559 | 4096 | 87 | 12.34 | 14.183 |
| 2 | 10 | 8667 | 8192 | 69 | 2.0 | 3.382 |
| 3 | 12 | 8757 | 8192 | 68 | 2.27 | 3.658 |
| 4 | 14 | 8846 | 8192 | 26 | 2.47 | 2.967 |

- note: list_edit experiment, prose instructions

## 2026-09-15 14:36 UTC - rig turn (topic:groceries)

- message: `Homer: please restructure the list by grocery category (dairy, bakery, produce, household)`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 56.44 s, 6 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 16 | 9136 | 8192 | 64 | 4.15 | 5.418 |
| 2 | 18 | 9377 | 8192 | 279 | 3.76 | 9.41 |
| 3 | 20 | 9679 | 8192 | 97 | 4.66 | 6.609 |
| 4 | 22 | 9988 | 8192 | 129 | 5.48 | 8.145 |
| 5 | 24 | 10173 | 8192 | 174 | 6.01 | 9.629 |
| 6 | 26 | 10371 | 8192 | 82 | 6.5 | 8.15 |

- note: list_edit experiment, prose instructions

## 2026-09-15 14:37 UTC - rig turn (topic:groceries)

- message: `Homer: show me the new list, and cross off the apples`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 35.05 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 28 | 10483 | 8192 | 61 | 7.58 | 8.81 |
| 2 | 30 | 10757 | 8192 | 78 | 7.45 | 9.02 |
| 3 | 32 | 10858 | 8192 | 117 | 7.78 | 10.178 |

- note: list_edit experiment, prose instructions

## 2026-09-15 14:40 UTC - rig turn (topic:groceries)

- message: `Homer: what is on the shopping list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 25.41 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 8222 | 4096 | 59 | 11.14 | 12.407 |
| 2 | 4 | 8413 | 8192 | 55 | 1.29 | 2.368 |
| 3 | 6 | 8621 | 8192 | 66 | 1.9 | 3.205 |

- note: list_edit + pseudocode skill variant

## 2026-09-15 14:40 UTC - rig turn (topic:groceries)

- message: `Homer: please add butter, eggs and flour to the list`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 34.21 s, 4 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 8 | 8532 | 4096 | 91 | 12.3 | 14.225 |
| 2 | 10 | 8644 | 8192 | 70 | 1.99 | 3.374 |
| 3 | 12 | 8735 | 8192 | 69 | 2.18 | 3.538 |
| 4 | 14 | 8825 | 8192 | 26 | 2.4 | 2.892 |

- note: list_edit + pseudocode skill variant

## 2026-09-15 14:41 UTC - rig turn (topic:groceries)

- message: `Homer: please restructure the list by grocery category (dairy, bakery, produce, household)`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 36.75 s, 4 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 16 | 9115 | 8192 | 61 | 4.06 | 5.273 |
| 2 | 18 | 9353 | 8192 | 329 | 3.75 | 10.399 |
| 3 | 20 | 9705 | 8192 | 88 | 4.68 | 6.459 |
| 4 | 22 | 10021 | 8192 | 99 | 5.46 | 7.438 |

- note: list_edit + pseudocode skill variant

## 2026-09-15 14:42 UTC - rig turn (topic:groceries)

- message: `Homer: show me the new list, and cross off the apples`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 31.08 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 24 | 10020 | 8192 | 77 | 6.41 | 7.962 |
| 2 | 26 | 10120 | 8192 | 55 | 5.79 | 6.885 |
| 3 | 28 | 10404 | 8192 | 112 | 6.52 | 8.781 |

- note: list_edit + pseudocode skill variant

## 2026-09-15 14:43 UTC - rig turn (topic:groceries)

- message: `Homer: what is on the shopping list?`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 26.47 s, 3 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 2 | 8222 | 4096 | 66 | 11.1 | 12.541 |
| 2 | 4 | 8400 | 8192 | 55 | 1.3 | 2.389 |
| 3 | 6 | 8608 | 8192 | 67 | 1.89 | 3.222 |

- note: final validation: list_edit + pseudocode skill + untick guard

## 2026-09-15 14:44 UTC - rig turn (topic:groceries)

- message: `Homer: please add butter, eggs and flour to the list`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 30.75 s, 2 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 8 | 8552 | 4096 | 212 | 12.32 | 16.697 |
| 2 | 12 | 8805 | 8192 | 33 | 3.24 | 3.877 |

- note: final validation: list_edit + pseudocode skill + untick guard

## 2026-09-15 14:45 UTC - rig turn (topic:groceries)

- message: `Homer: please restructure the list by grocery category (dairy, bakery, produce, household)`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 48.85 s, 5 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 14 | 9102 | 8192 | 70 | 4.07 | 5.463 |
| 2 | 16 | 9349 | 8192 | 237 | 3.73 | 8.499 |
| 3 | 18 | 9644 | 8192 | 170 | 4.56 | 8.0 |
| 4 | 20 | 9873 | 8192 | 213 | 5.09 | 9.449 |
| 5 | 22 | 10110 | 8192 | 78 | 5.75 | 7.313 |

- note: final validation: list_edit + pseudocode skill + untick guard

## 2026-09-15 14:45 UTC - rig turn (topic:groceries)

- message: `Homer: show me the new list, and cross off the apples`
- model: `Qwen3.6-35B-A3B-UD-MLX-4bit`, wall time 26.64 s, 2 LLM call(s), exit 0

| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |
|---|---|---|---|---|---|---|
| 1 | 24 | 10445 | 8192 | 109 | 7.47 | 9.693 |
| 2 | 27 | 10809 | 8192 | 105 | 7.54 | 9.696 |

- note: final validation: list_edit + pseudocode skill + untick guard

## 2026-09-15 - List-lifecycle experiments: verdict

Test: one conversation - read the list, add three items, restructure
by category, show and tick one item. Oracle: the file's end state.

| Variant | Calls | Wall | End state |
|---|---|---|---|
| Free-form editing (baseline) | 17 | 185 s | WRONG first run (lost [x], broken frontmatter); correct after guards + repair rule, at 7-call restructures |
| list_edit tool, prose skill | 16 | 151 s | correct (one untick + model repair) |
| list_edit tool, pseudocode skill | 14 | 127 s | WRONG: restructure unticked an item, model did not repair |
| list_edit + pseudocode + untick guard | 12 | 133 s | CORRECT, no repair needed |

Shipped from this:
1. list_edit tool (runtime/list_tool.py): add/tick/untick/remove, one
   item per call; the store matches the item, refuses ambiguity with
   candidates, preserves all other state by construction.
2. Pseudocode skill: the family-memory skill rewritten as terse rule
   constructs. ~300 tokens saved, fewer calls, Sources kept.
3. Untick guard at the write seam (memory/cli/write.py, mirrored in
   lab-api): a whole-page todos write that reopens or removes items
   is refused with the items named. Lesson: routing rules compress
   into pseudocode fine; safety rules move OUT of the prompt into
   deterministic code. The pseudocode variant's one failure was a
   safety rule; the guard closed it.
4. Cache pad retuned twice (53 -> 80 lines); prefix at ~8,213.

Production gap: `stack memory list-edit` exists only in the rig's
lab-api. The memory stacklet needs the same verb (same contract,
matching, sentences) plus a famstack-api allowlist entry before the
agent image ships.

On a dedicated list service (user question): recommendation is no.
The fights were about model discipline on whole-page rewrites and
missing item verbs, not about storage. Both are now solved at the
write seam, and the end state is correct with service-grade
semantics. A separate service would fork the source of truth away
from the vault (files, git audit, wiki, human editing) and re-import
a sync problem. The pragmatic endpoint of the current path delivers
the same reliability: item ops via list-edit (done), and later a
server-side list-restructure op (model sends {section: [items]} as
data, the store rebuilds the page deterministically) so write_file
disappears from list handling entirely.

## 2026-09-15 - Prior-art research for the pseudocode post (material only)

Archived for the separate blog session. Key points from the research
brief (full sources in the session transcript):

- Pseudocode prompting is established: Mishra et al., EMNLP 2023
  (+7-16 F1 on non-instruction-tuned models); Puerto et al., EMNLP
  2024 (code format helps conditional reasoning, +7 to +22 points);
  arXiv:2411.10541 (format alone swings up to 40%, small models are
  the format-sensitive ones). SudoLang (2023) is the practitioner
  canon. Do not claim novelty for pseudocode prompts.
- Guards-over-prompts is also established: 12-Factor Agents, NeMo
  Guardrails, Claude Code hooks. Do not claim novelty there either.
- The defensible novel angle is the interaction, with our measured
  failure as the worked example: compressing prose to pseudocode
  preferentially drops rare-firing conditionals, which is exactly the
  rule class the guardrails literature says should not live in
  prompts at all. Nearest published analogue: KV-cache compression
  degrading a defense instruction first (arXiv:2510.00231). Nobody
  has published this for prompt-style compression on small local
  models. n=1, one model: state that up front.
- pi's <1K-token prompt argues minimal prompts because frontier
  models are strong; our result is the inversion for weak local
  models: not less prompt, denser encoding plus code guards.
- Candidate framing ranked first by the research: "Pseudocode is
  lossy compression, and it drops exactly the rules you can least
  afford to lose."
