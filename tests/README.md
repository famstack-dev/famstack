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

Run the cheapest lane that proves what you changed. One name per lane; the
aliases are gone.

Nothing here is per commit. The hooks already run `ruff` on staged files and
check the commit subject, which costs about a second; a lane that takes a
minute and a quarter would only teach people to skip it.

| Lane | Time | Needs | Exclusive | Run it when |
|---|---|---|---|---|
| `make lint` | <1s | nothing | no | The whole tree, when you want more than the staged files the hook checks. What CI runs. |
| `make test-unit` | ~50s | nothing | no | Before a push, or when a coherent piece of work is done. Offline framework and stacklet tests: no Docker, no live services, no production data. |
| `make test-lifecycle` | ~6m | Docker, APFS | yes | You changed lifecycle, config rendering, `.env`, compose, container names, ports, volumes, health wiring or the backup engine. Owns a throwaway instance on fixed names, and mounts an APFS disk image per backup test. |
| `make test-demo` | ~4m | the demo instance running | yes | The behaviour has to work against the already-running bots and real service wiring. Tests create unique data and clean up after themselves; they never reset the instance. |
| `make test-smoke` | ? | the test rig or the Simpsons demo | yes | A quick answer on a cross-service path. Seeds secrets and brings the required stacklets up first, so it is not read-only. |
| `make test-e2e` | ? | the test rig or the Simpsons demo | yes | End of a branch, or before asking for review, when the change crosses container boundaries. On the demo it stashes the instance for the run and brings it back afterwards, pass or fail; any other instance is refused. |

**Exclusive** means the lane owns fixed container names and ports, so exactly
one run at a time on this Mac. Check nothing else is mid-run before starting
one, and see [RFC-003](../docs/adr/rfc-003-agentic-development-harness.md) for
why that is courtesy rather than enforcement today.

**?** means nobody has timed it. If you run one, put the number here.

`make` on its own prints a short version of this table.

Do not run managed integration cleanup or reset commands against a production
instance. `stacktests` guards this with a test-instance sentinel, but
destructive test-rig commands still require care.

## Structure

`tests/framework/` contains framework-level tests. Most are offline unit tests.
`tests/framework/test_config_to_container.py` is intentionally separate because
it talks to Docker and dominates runtime. `tests/stacklets/test_backup_e2e.py`
joins it in the lifecycle lane: it creates and mounts an APFS disk image per
test, about a second each, which was a third of the unit lane. The list lives
in `script/test` as `SLOW_FILES`.

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

