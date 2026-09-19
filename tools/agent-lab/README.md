# agent-lab

A validation harness for agent experiments. It measures the LLM serving
path in isolation. It sends only synthetic data to the oMLX endpoint.
It does not touch stack services, Matrix, or family data.

The harness uses only the Python standard library. It reads endpoint
defaults from `stack.toml [ai]`. Flags override the defaults.

## Commands

```
python3 tools/agent-lab/lab.py probe
python3 tools/agent-lab/lab.py cache --prefix-words 3000 --history-turns 6
```

- `probe`: sends one small completion two times. It shows the raw usage
  object. Use it to find the cache fields that oMLX reports.
- `cache`: measures prefix-cache behavior in four phases: cold, warm,
  warm2, mutate. The mutate phase rewrites one early message in place.
  This simulates a rewrite of the history, for example an old tool
  result replaced by a placeholder.
  Expected result: warm turns have a low TTFT. The mutate turn has a
  TTFT near the cold value.

## Output

Each run writes two artifacts:

- A raw JSON record in `tools/agent-lab/results/`.
- A summary entry in `docs/design/agent/agent-improvement-log.md`.
  Use `--no-log` to skip the log entry. Use `--note` to add context.

## Rules

- Synthetic data only. Do not point the harness at family content.
- The endpoint runs on the production Mac. Keep runs short. Do not run
  the harness while the family uses the agent.
