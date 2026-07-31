# Test Profiles

## What we are trying to write

Module tests, first and foremost. A module test drives one coherent piece of
functionality from the outside, the way a client would, and its purpose is to
state the intent and pin the expected behaviour at the time of writing. That is
what survives refactors; the implementation underneath is disposable. Write them
to read like API documentation: name the behaviour, say why the case matters.

Two failure modes to avoid, both of which cost tokens and buy nothing:

- **Tests that mirror the implementation.** Written next to the code they cover,
  they prove the two agree, not that either is right. Assert against something
  external instead: a spec, a real service's response, an invariant we promise.
- **Stubs standing in for things we can run.** A green run against a stub proves
  the wiring, not the behaviour. `stacktests ai local` gives real model answers
  at no cost per call; reach for `mock` when you need determinism, not when you
  need a pass.

The demo-rig and e2e lanes sit on top: they prove the wiring holds between real
containers. They do not replace module tests, and module tests do not replace
them.

## Choosing a lane

Use the shortest command that proves the behavior you changed. The profiles
below are ordered from cheapest to most disruptive.

| Profile | Alias | Command | What belongs here |
|---|---|---|---|
| `unit` | `fast`, `test` | `make test-unit` | Offline framework and stacklet tests. No Docker daemon, no live services, no production data. |
| `demo-rig` | `demo` | `make test-demo` | Tests against the already-running Simpsons demo instance in this checkout. Tests must create unique data and clean up after themselves. |
| `container-lifecycle` | `lifecycle` | `make test-lifecycle` | Docker lifecycle tests for stack orchestration: config rendering, `.env`, `stack up`, `stack down`, `stack destroy`, and container environment behavior. |
| `container-e2e` | `e2e` | `make test-e2e` | Full managed integration rig tests with real stacklets and Docker. Use this for cross-container behavior and release confidence. |
| `smoke` | none | `make test-smoke` | Small managed-rig e2e subset for quick checks inside the container e2e lane. |

## Structure

`tests/framework/` contains framework-level tests. Most are offline unit tests.
`tests/framework/test_config_to_container.py` is intentionally separate because
it talks to Docker and dominates runtime.

`tests/stacklets/` contains stacklet-level unit tests. These should exercise
stacklet modules through public boundaries with local fixtures, fake HTTP
servers, temporary repositories, or mocked external services. They belong in
`unit` unless they require real containers.

`tests/integration/test_demo_rig_e2e.py` contains live demo-rig tests. These
run against the operator's current demo instance and must not reset or own the
instance. Use this lane when you need read-your-writes confidence against the
running bots and real service wiring.

`tests/integration/test_*_e2e.py` contains managed container e2e tests. These
run through `tests/integration/stacktests`, which seeds a test-owned instance,
starts required stacklets, and keeps the rig reusable between runs.

`tests/integration/eval/` is opt-in prompt and model evaluation. It is excluded
from normal pytest collection and is run with `tests/integration/stacktests eval`.

## Choosing A Profile

Run `make test-unit` while coding. It is the default pre-commit check.

Run `make test-demo` when the behavior must work in the already-running demo
instance and the test can be written to clean up after itself.

Run `make test-lifecycle` when changing framework lifecycle, compose generation,
environment rendering, container names, ports, volumes, or health wiring.

Run `make test-smoke` for a quick managed-rig e2e check after changing a
cross-service path.

Run `make test-e2e` at the end of a branch or before asking for review when the
change crosses container boundaries.

Do not run managed integration cleanup/reset commands against a production
instance. `stacktests` guards this with a test-instance sentinel, but destructive
test-rig commands still require care.
