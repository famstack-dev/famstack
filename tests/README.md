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
| `make test-integration` | ~6m | Docker, APFS | yes | You changed lifecycle, config rendering, `.env`, compose, container names, ports, volumes, health wiring or the backup engine. Owns a throwaway instance on fixed names, and mounts an APFS disk image per backup test. |
| `make test-demo` | ~4m | the demo instance running | yes | The behaviour has to work against the already-running bots and real service wiring. Tests create unique data and clean up after themselves; they never reset the instance. |
| `make test-smoke` | ~3.5m (remote, M4 16 GB) | the test rig or the Simpsons demo | yes | A quick answer on a cross-service path. Seeds secrets and brings the required stacklets up first, so it is not read-only. |
| `make test-e2e` | ~9.5m (remote, M4 16 GB) | the test rig or the Simpsons demo | yes | End of a branch, or before asking for review, when the change crosses container boundaries. On the demo it stashes the instance for the run and brings it back afterwards, pass or fail; any other instance is refused. |
| `script/test remote e2e` | ~9.5m | ssh to a Mac with Docker and uv | on the remote | The e2e lane, or `remote smoke`, on another Mac with AI mocked, while this one keeps its own instance. See [Remote e2e](#remote-e2e). |

**Exclusive** means the lane owns fixed container names and ports, so exactly
one run at a time on this Mac. Check nothing else is mid-run before starting
one, and see [RFC-003](../docs/adr/rfc-003-agentic-development-harness.md) for
why that is courtesy rather than enforcement today.

**?** means nobody has timed it. If you run one, put the number here.

`make` on its own prints a short version of this table.

## Remote e2e

`script/test remote e2e [pytest args]` (or `remote smoke`) runs the lane on
another Mac over SSH and exits with pytest's exit code. Nothing on this Mac
changes: no containers, no `stack.toml`, no AI mode.

1. Takes a lock on the remote (`mkdir`), naming the holder and start time.
   A second run refuses at once and names the first.
2. Syncs this working tree, uncommitted changes included, with `rsync`.
   The remote's instance config and `.venv` are excluded, so they persist
   and an unchanged `uv.lock` installs nothing.
3. Runs `stacktests <lane>` there in mock AI mode, whatever mode this Mac
   is in, stops the rig and releases the lock, also after Ctrl-C.
4. Copies the junit XML and container logs to `tests/e2e/remote-results/`.

| Variable | Default | |
|---|---|---|
| `FAMSTACK_REMOTE_HOST` | `famstack-e2e` | ssh destination; key auth, no prompts |
| `FAMSTACK_REMOTE_DIR` | `~/famstack-dev` | base dir on the remote; quote the `~` |

Everything the run writes on the remote stays under the base dir
(`e2e/` tree and instance, `data/`, `cache/`, `lock/`, `results/`), apart
from Docker's images and volumes. The remote needs Docker and uv; Python
3.11+ is fetched by uv if missing. Only mocked lanes run remotely: `eval`
needs the `ai` stacklet and is refused.

Do not run managed integration cleanup or reset commands against a production
instance. `stacktests` guards this with a test-instance sentinel, but
destructive test-rig commands still require care.

## Structure

Each lane is a directory, so where a test lives says what it costs.

```
tests/unit/framework/     unit          offline, no Docker, no live services
tests/unit/stacklets/     unit
tests/integration/        integration   real things on the host
tests/e2e/                demo, smoke, e2e, eval, all through stacktests
```

`tests/unit/framework/` holds framework tests and `tests/unit/stacklets/`
stacklet tests. The latter exercise stacklet modules through public
boundaries with local fixtures, fake HTTP servers, temporary repositories, or
mocked external services. A test that needs real containers or other host
resources does not belong under `unit/`.

`tests/integration/` holds the tests that drive the host rather than Python:
`test_config_to_container.py` runs the Docker lifecycle on a throwaway
instance, and `test_backup_e2e.py` mounts an APFS disk image per test, about a
second each.

`tests/e2e/test_demo_rig_e2e.py` contains live demo-rig tests. These
run against the operator's current demo instance and must not reset or own the
instance. Use this lane when you need read-your-writes confidence against the
running bots and real service wiring.

`tests/e2e/test_*_e2e.py` contains managed container e2e tests. These
run through `tests/e2e/stacktests`, which seeds a test-owned instance,
starts required stacklets, and keeps the rig reusable between runs.

`tests/e2e/eval/` is opt-in prompt and model evaluation. It is excluded
from normal pytest collection and is run with `tests/e2e/stacktests eval`.

