# Refactor and Cleanup Plan (Pre-1.0)

This document defines a cleanup direction for Python code quality and maintainability
before 1.0. It is intentionally opinionated: we accept short-term migration cost to
reduce long-term complexity.

## Why now

- We are still pre-1.0 and can change internal patterns safely.
- `sys.path.insert(...)` import wiring is widespread and brittle.
- Host framework and Docker bot runtimes share code without clear boundaries.
- Python 3.9 compatibility adds drag for testing and packaging.
- HTTP behavior is inconsistent (timeouts, TLS policy, error handling).

## Design goals

- One clear import model (no ad-hoc path surgery in runtime code).
- Explicit runtime boundaries:
  - host framework code
  - bot/container runtime code
  - shared pure-python code
- Predictable HTTP behavior and security defaults.
- Dependency discipline: minimal core, explicit extras for optional features.
- Incremental migration with low break risk.

## Proposed module structure

Target shape (conceptual):

```text
lib/
  stack/                      # host runtime framework
    cli.py
    stack.py
    hooks.py
    docker.py
    secrets.py
    ...
  stack_shared/               # runtime-agnostic shared code
    __init__.py
    contracts/
      __init__.py
      stack_api.py            # request/response models or TypedDicts
      bots.py                 # bot config + message contracts
    http/
      __init__.py
      client.py               # sync wrappers for stdlib urllib/httpx
      errors.py
      policy.py               # timeout/retry/TLS policy helpers
    util/
      __init__.py
      jsonio.py
      validation.py

stacklets/
  core/
    bot-runner/
      main.py                 # bot runtime entrypoint
      microbot.py
      ...
    tools-server/
      server.py               # FastAPI surface, thin adapters only
  docs/
    bot/
      ...
```

Notes:

- `stack` remains the host framework package.
- `stack_shared` contains code safe to import from both host and containers.
- Stacklet-specific code stays in `stacklets/...` and does not become global utility by default.

## Packaging and import mechanics (critical)

The current runtime wiring matters:

- `./stack` sets `PYTHONPATH="$SCRIPT_DIR/lib"` and runs `python3 -m stack`.
- Bot containers currently mount `../../lib/stack` to `/app/stack` (not the full `lib/` tree).

That means introducing `stack_shared` requires explicit runtime wiring updates.

### Required changes for `stack_shared`

1. Host runtime:
   - No change required to wrapper if `stack_shared` lives under `lib/` with `__init__.py`.
2. Bot runtime (required):
   - Mount `../../lib` to `/app/lib` (read-only) instead of only `../../lib/stack`.
   - Set `PYTHONPATH=/app/lib` in bot-related containers.
3. Tooling:
   - Keep imports package-based (`from stack...`, `from stack_shared...`).

Without these wiring changes, the proposed structure will fail in containers.

## Runtime boundary rules

1. Bots may import from:
   - `stack_shared.*`
   - their own stacklet package/module tree
2. Bots must not import deep host internals (`stack.stack`, `stack.cli`, etc.).
3. Host framework may import from:
   - `stack.*`
   - `stack_shared.*`
4. Cross-runtime operations happen via explicit API boundaries (stack API, HTTP endpoints),
   not by importing host-only classes inside containers.

## Import policy

### Policy

- Runtime code must not use `sys.path.insert(...)`.
- Allowed temporary exception: test bootstrap (`tests/`) during migration.
- Use package imports only (`from stack...`, `from stack_shared...`).

### Migration strategy

- Centralize bootstrap in one entrypoint where unavoidable.
- Remove per-file path hacks from:
  - `stacklets/*/cli/*.py`
  - `stacklets/*/hooks/*.py`
  - bot runner and bot entrypoints
- Keep dynamic bot loading, but replace `sys.path.insert(bot_dir)` with
  `importlib.util.spec_from_file_location(...)` to load by absolute file path.

## Python version policy

### Direction

- Move baseline to Python `>=3.11` for framework and bot runtime.
- End active 3.9 compatibility after migration window.

### Why

- 3.9 is EOL and increases compatibility overhead (`pytest<9`, shims, guard code).
- 3.11 improves performance, typing ergonomics, and dependency support.

### Transition (short-lived)

- One migration cycle can run dual CI (3.9 + 3.11) to catch regressions.
- After parity, remove 3.9 constraints and simplify codepaths.

## HTTP request conventions

Create a shared HTTP policy in `stack_shared.http` and use it everywhere.

### Defaults

- TLS verification ON by default.
- `verify=False` only for explicit local-development cases and only through one
  documented escape hatch.
- Explicit timeout on every request (no implicit infinite waits).
- Structured error mapping (`timeout`, `connect`, `http_status`, `decode`).

### Client split

- Host framework (mostly sync): shared sync wrapper based on stdlib `urllib`
  to preserve the current lightweight host dependency model.
- Bot/services that are async: shared async wrapper (httpx-based) with the same policy.
- Keep endpoint-specific code thin; centralize retries/timeouts/TLS/error behavior.

### Dependency boundary for HTTP

- Do not require `httpx` for the base host framework runtime.
- If sync `httpx` is introduced later, treat it as an explicit decision with
  measured value, not as a side effect of refactoring.

### Retries

- Retry only idempotent operations by default (`GET`, safe probes).
- No blind retries on mutating operations unless endpoint contract is idempotent.

### Response contract

- Prefer returning typed/validated result objects from adapters, not raw dict soup.
- Keep wire-format parsing near transport adapters.

## Dependency policy

### Principles

- Default framework runtime should stay light.
- Add dependencies only when there is clear repeated value.
- Avoid one-off utility dependencies when stdlib is enough.

### Suggested dependency layers

- Core runtime deps: minimal and stable.
- Optional extras by domain:
  - `test`
  - `bots`
  - `dev`
- Keep version ranges broad but controlled; pin only where needed for reproducibility.

### Rules of thumb

- If 3+ modules duplicate transport/parsing logic, promote to `stack_shared`.
- If a dependency is used by one stacklet only, keep it stacklet-local if possible.
- Avoid introducing both sync and async clients for the same concern without a shared policy.

## Module testing strategy

Testing should enforce module boundaries and protect refactors from silent regressions.

### Principles

- Prefer blackbox tests at module boundaries over implementation-coupled tests.
- Avoid mocks by default; only mock external interfaces when unavoidable.
- Keep tests close to the runtime reality (real stacklets, real hooks, real lifecycle paths).
- Add tests before or alongside refactors that move imports, contracts, or runtime wiring.

### Test tiers by module type

1. Framework core modules (`lib/stack/*`)
   - Unit tests for pure functions and state transformations.
   - Boundary tests for public methods (`Stack`, CLI command handlers, hook resolver behavior).
   - Integration tests for lifecycle-critical paths (`up/down/destroy`, health checks, env rendering).

2. Shared modules (`lib/stack_shared/*`)
   - Contract tests for request/response schemas and validation behavior.
   - Transport-policy tests for timeout/retry/TLS/error mapping.
   - Compatibility tests proving imports work in both host and container runtimes.

3. Bot/runtime modules (`stacklets/*/bot`, `stacklets/core/bot-runner`)
   - Discovery/loading tests (bot declaration -> module load -> class resolution).
   - Integration tests for startup and command/event handling with real runner flow where possible.
   - Failure-path tests (missing secret, malformed bot.toml, unreachable Matrix/API bridge).

4. Stacklet CLI/hook modules (`stacklets/*/cli`, `stacklets/*/hooks`)
   - Module tests for argument parsing and business decisions.
   - Integration tests for hook invocation contracts (`ctx.*` behavior, env wiring, idempotency).
   - Regression tests for known fragile transitions (`on_install_success`, `on_start_ready` ordering).

### Minimal mocking guidance

- Allowed mock targets:
  - External network boundaries
  - Docker daemon interactions in unit-level tests
  - Time/sleep where deterministic timing is required
- Prefer integration alternatives first:
  - test stacklets
  - real local service fixtures
  - existing integration rig

### Coverage gates per phase

- Phase 1 gate:
  - New `stack_shared` modules have direct tests for contracts and importability.
- Phase 2 gate:
  - Refactored runtime files keep or improve behavior coverage; no path-hack regressions.
- Phase 3 gate:
  - HTTP wrappers validated for timeout/auth/decode/connect failure classes.
- Phase 4 gate:
  - Test matrix proves 3.11 baseline; 3.9 jobs removed only after parity confirmation.
- Phase 5 gate:
  - Large-module splits preserve command/lifecycle behavior via unchanged blackbox tests.

### Required regression suite for this cleanup

- Stack API bridge contract tests:
  - `famstack-api` command allowlist and malformed JSON behavior.
  - Caller adapters (`tools-server`, `stacker`) parse and surface errors consistently.
- Import/runtime wiring tests:
  - Bot and tools containers can import `stack_shared` without `sys.path` hacks.
- Hook and CLI execution tests:
  - Return values are checked on critical paths (no silent success on failure).
- HTTP policy tests:
  - Verified TLS default, explicit insecure escape hatch, and timeout semantics.

## Prioritized migration plan

## Phase 1 - Foundation (low risk)

- Add `stack_shared` package skeleton.
- Define shared contracts for stack API messages.
- Introduce shared HTTP policy wrapper(s).
- Add import lint/check guidance (no new `sys.path.insert` in runtime code).
- Update container wiring (`PYTHONPATH` + `lib` mount) to make shared imports work.

Exit criteria:

- New shared modules are importable in host and bot runtime.
- No behavioral changes yet.

## Phase 2 - Path hack removal in runtime code

- Migrate bot runner and 1-2 stacklets (docs/messages) to package imports.
- Migrate selected CLI/hook modules off per-file `sys.path.insert`.
- Keep tests temporarily as-is to minimize churn.

Exit criteria:

- Critical runtime paths no longer depend on per-file `sys.path` hacks.

## Phase 3 - HTTP unification

- Replace ad-hoc urllib/httpx request code with shared wrappers.
- Normalize timeout/TLS/error behavior.
- Add integration tests for failure classes (timeout, auth, bad JSON, unreachable host).

Exit criteria:

- Runtime HTTP callsites use consistent policy and error semantics.

## Phase 4 - Python baseline lift

- Switch project baseline to 3.11.
- Remove 3.9-only constraints and compatibility leftovers.
- Update CI and container base images.
- Add explicit startup/preflight message in host CLI when interpreter is below baseline.

Exit criteria:

- CI green on 3.11 baseline.
- No remaining 3.9 compatibility blockers.

## Phase 5 - Structural cleanup

- Break up large orchestration files (`cli.py`, `stack.py`) into focused modules.
- Convert high-traffic dict contracts to typed contracts.
- Reduce duplicate command surfaces where practical.

Exit criteria:

- Smaller modules with clear ownership and reduced coupling.

## Acceptance checks

- Runtime code has zero new `sys.path.insert(...)` usage.
- Shared code is imported via package boundaries only.
- HTTP defaults are secure and explicit.
- Dependency additions have rationale and ownership.
- Critical paths covered by integration tests.
- Bot and tools containers import `stack_shared` without local path hacks.

## Non-goals for this cleanup

- Full plugin sandbox/security model redesign.
- Rewriting all stacklets in one pass.
- Eliminating every dynamic import pattern immediately.

## Suggested first implementation slice

1. Introduce `stack_shared.contracts.stack_api` used by:
   - `stacklets/core/famstack-api.py`
   - `stacklets/core/tools-server/server.py`
   - `stacklets/core/bot/stacker.py`
2. Introduce `stack_shared.http` and migrate one vertical path end-to-end
   (for example tools-server -> Paperless/Immich).
3. Remove path hacks in bot runner entrypoint and one stacklet bot entrypoint.

This gives immediate wins in structure and consistency without a risky big-bang rewrite.
