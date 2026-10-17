# Agent runtime shims — per-turn briefing

This directory is a **contained modification of nanobot**, loaded into the agent
container. Everything here lives in the stacklet; upstream `nanobot-ai` is
installed unchanged.

The main context shim is **brief** (what the agent knows going in), registered
through nanobot's runtime-context provider seam. History size is left to
nanobot's own settings in `config.json`:

| Mechanism | Setting | Value | Effect |
|---|---|---|---|
| Context window | `model_presets.primary.context_window_tokens` | 32768 | nanobot compacts the history into a summary checkpoint when a request comes close to it |
| Idle compaction | `idleCompactAfterMinutes` | 15 (default) | An idle session is summarised before its next turn |

A small window keeps prefill short on the local model. `AGENT_STATE_LOG=1`
writes the message list of each turn to `~/.nanobot/llm-state.log`.

## What it does

On every incoming message, it prepends a short **briefing** to the prompt:

```
You are speaking with marge (@marge). Homemaker and mother; leads a campaign to
reform violent children's TV; runs the Springfield Elementary PTA.
(Their full profile: vault/marge/about.md)
Topic 'itchy-scratchy-land': the family's outing to the amusement park...
Open todos (8): pick up entrance bands; charge the camera; pack Maggie's clothes; ...
Recently: capture Vorbereitung fuer Itchy Park Besuch; tick popcorn off the list
(Full topic: vault/family/itchy-scratchy-land/)
```

So a shared family room works correctly: **each turn is primed with whoever
actually spoke**, not with a single fixed `USER.md`. The agent gets the essence
plus pointers; it reads full pages with its file tools only if it needs depth.

## Why this design (and why not the obvious alternatives)

- **Not USER.md.** `USER.md` sits early in the prompt. Rewriting it per turn
  would invalidate the KV cache for the entire ~10K-token prefix that follows
  (identity + tool contract + skills), re-prefilling it every message. Measured
  on our endpoint: a per-turn change via `USER.md` cached **0** of 4746 prompt
  tokens; the same content injected late kept **4096** cached. So we inject late.
- **Not per-user nanobot instances.** Topic rooms are shared, multi-person
  conversations; per-user instances fragment them and duplicate the vault's
  person pages as a competing memory store.
- **Not MCP.** Too many tokens, and the agent surface is meant to be the `stack`
  CLI, not a protocol.
- **The vault is the source of truth.** The briefing is assembled *from* the
  git-backed vault (person pages, topic pages, todos, git log) on each turn — we
  do not maintain a second profile store.

## How it works

- `brief.py` — assembles the briefing from the vault (speaker page,
  topic page, open todos, recent *human* git activity). Compact, read-only,
  never raises.
- `sitecustomize.py` — auto-loaded by Python at startup (this dir is on
  `PYTHONPATH`; see the stacklet `Dockerfile`). It registers the briefing as a
  runtime-context provider on every `AgentLoop`. nanobot appends the block to
  the user's message and keeps it with that message in the history, so the
  next turn replays exactly what the model saw: the prompt prefix stays cached.
- `dream_aside.py` — run once by the entrypoint, not a shim. nanobot's Dream
  is off in `config.json`; this moves what it wrote before (profile files,
  `MEMORY.md`, its own skills) into `~/.nanobot/dream-archive/`.

```
Dockerfile:  COPY runtime/ /app/runtime  +  ENV PYTHONPATH=/app/runtime
startup:     python imports sitecustomize -> AgentLoop registers the brief provider
every turn:  provider(request) -> RuntimeContextBlock -> appended to the user text,
             stored with it -> the next turn's prefix is this turn's prompt
```

## Maintenance

- **Seam:** `AgentLoop.register_runtime_context_provider`, called from a wrapped
  `AgentLoop.__init__`. A provider is `async (RequestContext) -> RuntimeContextBlock`.
- **On every `nanobot-ai` bump:** re-verify the PIN list in `sitecustomize.py`.
  A shim that no longer attaches logs `... could not attach` at startup and the
  agent runs with stock behaviour (no crash): check the container logs.
- **To remove entirely:** delete this `runtime/` dir and the `PYTHONPATH` line
  from the `Dockerfile`.
- **Fork or not:** see `docs/adr/adr-012-nanobot-fork.md`.
