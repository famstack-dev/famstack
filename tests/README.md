# Test Profiles

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
