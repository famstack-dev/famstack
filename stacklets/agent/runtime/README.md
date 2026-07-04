# Agent runtime shim — per-turn family briefing

This directory is a **contained modification of nanobot**, loaded into the agent
container. It exists because nanobot has no plugin seam for injecting per-turn
context, and we did not want to fork nanobot for a single hook. Everything here
lives in the stacklet; upstream `nanobot-ai` is installed unchanged.

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
  `PYTHONPATH`; see the stacklet `Dockerfile`). It wraps
  `nanobot.agent.context.runtime_lines` to prepend our lines. nanobot appends
  the runtime block **after** the stable prompt + user text, which is the
  KV-cache-safe position.

```
Dockerfile:  COPY runtime/ /app/runtime  +  ENV PYTHONPATH=/app/runtime
startup:     python imports sitecustomize -> patches context.runtime_lines
every turn:  build_messages() -> runtime_lines() -> [our briefing] + [nanobot's]
             -> appended after the user text -> cache-hot prefix preserved
```

## Maintenance

- **Patched symbol:** `nanobot.agent.context.runtime_lines`,
  signature `(state, msg, workspace, *, skip=False) -> list[str]`.
- **On every `nanobot-ai` bump:** re-verify that symbol and signature. If they
  moved, the shim logs `brief shim could not attach` at startup and the
  agent runs with stock behaviour (no crash) — check the container logs.
- **To remove entirely:** delete this `runtime/` dir and the `PYTHONPATH` line
  from the `Dockerfile`.
- **When to graduate to a fork:** once we accumulate several nanobot changes
  (candidates already in view: the coding-flavoured tool contract, the
  single-user `USER.md` model, the `M_UNKNOWN_TOKEN` no-reauth bug), fold them
  into a fork and upstream a proper context-provider API so we can un-fork.
