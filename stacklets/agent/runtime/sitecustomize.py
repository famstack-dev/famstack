"""Runtime shim: inject a per-turn family briefing into nanobot's prompt.

Python auto-imports `sitecustomize` at interpreter startup for any module on
`sys.path`, so placing this on `PYTHONPATH` (see the Dockerfile) patches every
`nanobot` invocation in the container — the gateway and one-shot `nanobot agent`
alike — with no fork.

WHAT IT DOES
    Wraps `nanobot.agent.context.runtime_lines` so that, on each turn, our
    `brief.brief_lines(msg, workspace)` lines are prepended to nanobot's
    own runtime lines. nanobot appends the whole runtime block AFTER the stable
    system prompt and the user's text, so this stays KV-cache-friendly: the big
    prompt prefix stays cached and only our ~150-token speaker/topic block is
    recomputed when the speaker changes. (Measured on our endpoint: late
    injection keeps 4096/4746 prompt tokens cached; injecting the same content
    early, via USER.md, cached 0.)

WHY A SHIM AND NOT A FORK
    nanobot has no plugin seam for per-turn context injection — `runtime_lines`
    is hardcoded to the cli-app and mcp sources, and hooks are not pluggable.
    A shim keeps us on upstream `nanobot-ai` (updates included) with the change
    contained in this stacklet. The tradeoff: it patches an internal function,
    so a nanobot refactor of `context.runtime_lines` will break it — loudly,
    since we log on failure. If our nanobot changes ever grow past this one hook,
    fold them into a fork and upstream a real context-provider API instead.

TO REMOVE
    Delete this stacklet's `runtime/` dir and drop `PYTHONPATH` from the
    Dockerfile. nanobot reverts to stock behaviour with no other change.

PIN / RECHECK ON UPGRADE
    Patched symbol: `nanobot.agent.context.runtime_lines`
    Expected signature: `(state, msg, workspace, *, skip=False) -> list[str]`
    Re-verify both after any `nanobot-ai` version bump.
"""

import logging

_log = logging.getLogger("brief.shim")

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
