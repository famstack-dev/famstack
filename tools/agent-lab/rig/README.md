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

## Rules

- Fabricated data only in `demo-vault/`. Never copy family content.
- The rig binds lab services to 127.0.0.1 and uses its own ports.
  It must never point at port 42001 (production famstack-api).
