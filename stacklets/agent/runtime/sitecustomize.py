"""Runtime shims: keep nanobot's per-turn context correct and lean, no fork.

Python auto-imports `sitecustomize` at interpreter startup for any module on
`sys.path`, so placing this on `PYTHONPATH` (see the Dockerfile) patches every
`nanobot` invocation in the container — the gateway and one-shot `nanobot agent`
alike — with no fork.

Two independent shims live here; each is a thin monkeypatch over a pure module:

1. brief (brief.py) — prepends a per-turn family briefing (who is speaking, the
   topic) to nanobot's runtime lines. Injected late (after the stable prompt and
   the user's text) so it stays KV-cache-friendly: measured 4096/4746 prompt
   tokens cached with late injection vs 0 injecting the same content early via
   USER.md.

2. lean_state (lean_state.py) — replaces previous-turn tool results with the
   call that produced them (`name(args)`), so the agent re-fetches instead of
   reciting stale data. The transcript (Matrix) keeps the full result; the state
   we feed the model keeps only a cheap pointer.

WHY SHIMS AND NOT A FORK
    nanobot has no plugin seam for per-turn context injection or state shaping.
    Shims keep us on upstream `nanobot-ai` (updates included) with the change
    contained in this stacklet. Tradeoff: they patch internals, so a nanobot
    refactor breaks them — loudly, since we log on failure. When these grow past
    a couple of hooks, fold the pure modules into a fork (each is already a clean
    function) and upstream real context-provider / state-shaping APIs.

TO REMOVE
    Delete this stacklet's `runtime/` dir and drop `PYTHONPATH` from the
    Dockerfile. nanobot reverts to stock behaviour with no other change.

PIN / RECHECK ON UPGRADE (re-verify after any `nanobot-ai` version bump)
    brief:      `nanobot.agent.context.runtime_lines(state, msg, workspace, *, skip=False) -> list[str]`
    lean_state: `nanobot.agent.context.ContextBuilder.build_messages(...) -> list[dict]`
"""

import logging

_log = logging.getLogger("agent.runtime.shim")

try:
    import nanobot.agent.context as _ctx
    from brief import brief_lines as _brief_lines

    _orig_runtime_lines = _ctx.runtime_lines

    def _runtime_lines(state, msg, workspace, *, skip=False):
        base = _orig_runtime_lines(state, msg, workspace, skip=skip)
        if skip:  # subagents skip runtime context; respect that
            return base
        try:
            extra = _brief_lines(msg, workspace)
        except Exception:
            # A briefing is an optimisation, never a dependency — never break a turn.
            _log.exception("brief failed; continuing without a briefing")
            extra = []
        return list(extra) + list(base)

    _ctx.runtime_lines = _runtime_lines
    _log.info("brief runtime-context shim active")
except Exception:
    # If the internal symbol moved (nanobot upgrade), fail loudly in the log but
    # do not stop the agent from starting.
    _log.exception("brief shim could not attach (nanobot internals changed?)")


# ── lean_state: previous-turn tool results -> a pointer naming the call ──────
# Also the single place to see the *state* (what the model receives) next to the
# *transcript* (the Matrix room): every turn logs the leaned message list, one
# line per message, greppable by "[llm-state]" in `docker logs stack-agent`.
try:
    import datetime as _dt
    import os as _os

    import nanobot.agent.context as _ctx_ls
    from lean_state import format_state_for_log as _format_state
    from lean_state import lean_messages as _lean_messages

    _orig_build_messages = _ctx_ls.ContextBuilder.build_messages
    # Bind-mounted home (~/.nanobot -> famstack-data/agent), so this file is
    # readable on the host for analysis, one appended block per turn.
    _STATE_LOG = _os.path.expanduser("~/.nanobot/llm-state.log")

    def _build_messages_lean(self, *args, **kwargs):
        # Post-process the assembled message list: stale prior-turn derived data
        # (tool results and tool-synthesized answers) become pointers; the
        # current turn stays intact.
        messages = _lean_messages(_orig_build_messages(self, *args, **kwargs))
        try:  # a debug view; never worth breaking a turn over
            stamp = _dt.datetime.now().isoformat(timespec="seconds")
            with open(_STATE_LOG, "a", encoding="utf-8") as fh:
                fh.write(f"\n===== {stamp}  {len(messages)} messages =====\n"
                         + _format_state(messages) + "\n")
            print(f"[llm-state] {len(messages)} msgs -> llm-state.log", flush=True)
        except Exception:
            pass
        return messages

    _ctx_ls.ContextBuilder.build_messages = _build_messages_lean
    _log.info("lean-state message shim active")
except Exception:
    _log.exception("lean-state shim could not attach (nanobot internals changed?)")
