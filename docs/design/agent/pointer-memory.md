# Idea — a pointer-based, layered memory for agents

> Status: Idea / research direction (not scheduled). Seeded 2026-07-05 from the
> lean-state work while building todo strike.
> Sibling docs:
>   - [addressing.md](addressing.md) — how the agent is addressed
>   - [../brain/memory-mutations.md](../brain/memory-mutations.md) — the git-backed vault write path
> Code already in place (the first slices): `stacklets/agent/runtime/lean_state.py`
>   (tool-result + tool-turn-answer decay) and the `[llm-state]` log at
>   `~/famstack-data/agent/llm-state.log`.

## The thesis

**A raw conversation transcript is a naive memory for an agentic system.** It is
full of junk for the model's purposes: acknowledgements, repeated questions, and
especially *stale derived data* — a todo list read out three turns ago, a count
that has since changed. Replaying it verbatim pollutes the context and actively
misleads the model, which recites an old answer instead of re-deriving. We saw
this live: with five "what's open?" turns in the room, the agent recited its own
earlier "8 open" list instead of checking.

The distinction that matters (Arthur's framing):
- **Transcript** — the chat as humans see it (the Matrix room). Complete,
  immutable, always replayable. We never need to *carry* it.
- **State** — the lean working memory we actually feed the model each turn. It
  should be a *projection* of the transcript, not a copy.

Today nanobot's state *is* the transcript, rebuilt from the room every turn.
That is the bug at the root.

## The direction: pointers, not payloads; layers by volatility

Keep durable conversation; replace volatile derived-data with a **pointer** that
says how to get the current value. Structure the state in **layers**:

- **Hot** — the current turn and the last turn or two, verbatim.
- **Warm** — older turns, compressed to their gist (a decision, a fact stated).
- **Cold** — derived data (tool results, tool-synthesized answers, injected
  lists): a pointer naming the source (`exec(stack memory topic x todo)`, a vault
  path), re-resolved on demand. Never the stale payload.

The full detail is never lost — it lives in the transcript and in the sources the
pointers name.

## Why git might be the substrate

famstack's knowledge is *already* a git repo (the Forgejo vault). Git gives
exactly the primitives a pointer memory wants:

- **content-addressed blobs** — a pointer is a ref to an object, not a copy;
- **history + diffs** — "what changed since the agent last looked" is `git diff`;
- **refs/paths** — a durable name for a fact (`family/bart/about.md`) that
  re-resolves to the *current* value after a pull;
- **pull = refresh** — the freshness mechanism is already there (search and the
  todo read use it).

So a layered memory could be: durable facts = files in the tree; the transcript =
the commit log; the agent's *state* = a small set of pointers (paths, refs, line
ranges) plus the hot verbatim window. Volatile answers collapse to "re-read
`<path>`" or "re-run `<call>`". This is not nanobot-specific — it is a memory
model for any agent sitting on a versioned knowledge store.

## What the experiment taught us (so we don't repeat it)

The first slices (decay tool results, then tool-turn answers) are correct but
**insufficient alone**: they key on "was a tool involved," and the agent
short-circuits tools — it answered from the injected brief, so there was no tool
call to anchor the decay to, and the stale lists were structurally
indistinguishable from conversation. Two lighter fixes to weigh, with the
`[llm-state]` log as the instrument:

1. **Weights / volatility scoring.** Score a message by the *volatility of its
   content* (a bare list or count is volatile however it was produced), not by
   whether a tool was involved. Recency and type are free inputs; content
   importance is the hard part, and this is where the lean pre-selection
   classifier idea finally earns its keep. The projection then fills a token
   budget by score.
2. **Force derived data through tools.** Strip recitable data from every injected
   context (we already made the brief hand a count + fetch command, not the
   list), so the only way to state data is to fetch it — which makes the
   structural "tool-derived → decays" rule reliable again.

Likely both, composed.

## Open questions

- Who assigns weight/volatility — heuristics, a cheap classifier, the model
  itself, or a hook the tool declares ("my output is volatile")?
- Granularity of a pointer — whole file, section, line range, a call signature?
- How to keep continuity ("you said 8, now 7?") — the agent reads the transcript
  (`stack messages read`) or the git diff when a user references the past.
- Does this become a nanobot fork feature, or a general library other agents use?

## Next step when we return

Read `~/famstack-data/agent/llm-state.log` from real usage first — let it show
which junk actually accumulates and whether weights or forced-tool-use is the
lighter lever. Build from data, not from guesses.
