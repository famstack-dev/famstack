# Test Profiles

## What we are trying to write

Surface tests, first and foremost, written before the code and seen to fail.
A surface test drives a change the way its user does: a command through the
real `./stack` entry point (in process, via `Stack.run_cli_command`), a bot
loaded the way the bot runner loads it. Where there is no surface, a module
test drives one coherent piece of functionality from the outside, the way a
client would. Either way the purpose is to state the intent and pin the
expected behaviour at the time of writing. That is what survives refactors; the
implementation underneath is disposable. Write them to read like API
documentation: name the behaviour, say why the case matters.

Two failure modes to avoid, both of which cost tokens and buy nothing:

- **Tests that mirror the implementation.** Written next to the code they cover,
  they prove the two agree, not that either is right. Assert against something
  external instead: a spec, a real service's response, an invariant we promise.
- **Stubs standing in for things we can run.** A green run against a stub proves
  the wiring, not the behaviour. `stacktests ai local` gives real model answers
  at no cost per call; reach for `mock` when you need determinism, not when you
  need a pass.

Host surface tests run in `make test-unit` and CI: no Docker, no model,
stand-ins only at the network edge. A module's live with its other unit
tests (memory's in `tests/memory/unit/`), the framework's in
`tests/unit/surface/`. Copy `tests/memory/unit/test_memory_write.py`;
`tests/unit/surface/test_every_command_loads.py` and `test_every_bot_loads.py`
check that every command and bot loads through its real loader. They average about 0.2 seconds each (100 in about 20 seconds),
and a test over 1 second says why in its docstring. Rig surface tests drive
the running test instance with `tests/driver/driver`, before merging anything
that touches a bot.

The demo-rig and e2e lanes sit on top: they prove the wiring holds between real
containers. They do not replace surface and module tests, and those do not
replace them.

## Choosing a lane

Run the cheapest lane that proves what you changed. One name per lane; the
aliases are gone.

Nothing here is per commit. The hooks already run `ruff` on staged files and
check the commit subject, which costs about a second; a lane that takes a
minute and a quarter would only teach people to skip it.

| Lane | Time | Needs | Exclusive | Run it when |
|---|---|---|---|---|
| `make lint` | <1s | nothing | no | The whole tree, when you want more than the staged files the hook checks. What CI runs. |
| `make test-unit` | ~2m | nothing | no | Before a push, or when a coherent piece of work is done. Offline framework and stacklet tests: no Docker, no live services, no production data. |
| `make test-integration` | ~6m | Docker, APFS | yes | You changed lifecycle, config rendering, `.env`, compose, container names, ports, volumes, health wiring or the backup engine. Owns a throwaway instance on fixed names, and mounts an APFS disk image per backup test. |
| `make test-demo` | ~4m | the demo instance running | yes | The behaviour has to work against the already-running bots and real service wiring. Tests create unique data and clean up after themselves; they never reset the instance. |
| `make test-smoke` | ~3.5m (remote, M4 16 GB) | the test rig or the Simpsons demo | yes | A quick answer on a cross-service path. Seeds secrets and brings the required stacklets up first, so it is not read-only. |
| `make test-e2e` | ~9.5m (remote, M4 16 GB) | the test rig or the Simpsons demo | yes | End of a branch, or before asking for review, when the change crosses container boundaries. On the demo it stashes the instance for the run and brings it back afterwards, pass or fail; any other instance is refused. |
| `script/test remote e2e` | ~9.5m | ssh to a Mac with Docker and uv | on the remote | The e2e lane, or `remote smoke`, on another Mac with AI mocked, while this one keeps its own instance. See [Remote e2e](#remote-e2e). |
| `script/test remote install` | ~1.5m, ~2m with `--remove-images` (M4 16 GB) | ssh to a Mac with Docker and uv, nothing else of famstack's | the whole remote | Before a release, or after touching the installer, the messages or core first start, or anything the final screen prints. See [First-run install](#first-run-install). |

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

### First-run install

`script/test remote install [--remove-images] [ref] [pytest args]` runs
the first-run installer the way a new admin does, on the same remote and
under the same lock as `remote e2e`, and checks that what it printed is
true. `ref` is anything pushed to the public repo (default `main`); the
remote clones it from GitHub, never from this working tree. Only the
test module (`tests/e2e/install/`) comes from here.

1. Resets the remote to pristine: no `stack-*` containers, no `stack`
   network, no `~/famstack-data`, no `dev.famstack.api` LaunchAgent, no
   clone. A leftover from a crashed run must not make a broken install
   pass. It deletes `~/famstack-data` only when the lane created it; any
   other is reported and the run refuses.
2. Clones the ref, and with `--remove-images` deletes the images messages
   and core use, so the pulls are tested too. That costs minutes.
3. Installs the way a script does: `./stack install --family <file> --yes`
   from a fresh login shell with stdin closed, for the Simpsons. Nothing
   may wait for input. Then `./stack up messages`, which asks nothing
   either once the family named the chat's server.
4. Checks the family is in users.toml and stack.toml, the last line says
   where the dashboard is and the dashboard greets the family, messages
   and core are healthy, every member can log in with their username as
   first password and is in the family space and rooms,
   `.stack/secrets.toml` holds the service admin password, and nothing
   printed is a traceback, `None` or an unfilled template.
5. Resets the remote to pristine again, pass, fail or Ctrl-C, and brings
   the transcripts (`install-transcript.txt`, `messages-transcript.txt`)
   home with the container logs.

The first run an admin gets by typing `./stack` happens in the browser;
`tests/integration/test_first_run_in_the_browser.py` drives it in headless
Chrome against a local tools server.

The installer writes into its checkout and to `~/famstack-data`, and the
container names and ports are fixed, so the lane owns the whole remote
while it runs. It removes the e2e rig's containers too; the next
`remote e2e` recreates them from its instance under the base dir.

Not automated: the Homebrew and OrbStack install paths at the start of
`./stack`. They need a Mac without either, and OrbStack's first-run
setup is a GUI. Check them by hand before a release.

Do not run managed integration cleanup or reset commands against a production
instance. `stacktests` guards this with a test-instance sentinel, but
destructive test-rig commands still require care.

## Structure

Each lane is a directory, so where a test lives says what it costs.

```
tests/unit/framework/     unit          offline, no Docker, no live services
tests/unit/stacklets/     unit
tests/<module>/unit/      unit          one module's unit tests, its test instance
tests/integration/        integration   real things on the host
tests/e2e/                demo, smoke, e2e, eval, all through stacktests
tests/js/                 `node --test tests/js/`, the dashboard's pure JavaScript; not in CI yet
```

`tests/unit/framework/` holds framework tests and `tests/unit/stacklets/`
stacklet tests. The latter exercise stacklet modules through public
boundaries with local fixtures, fake HTTP servers, temporary repositories, or
mocked external services. A test that needs real containers or other host
resources does not belong under `unit/`.

A module whose tests have moved to a tree of their own keeps them in
`tests/<module>/<lane>/`, next to the module's test instance: memory's are
in `tests/memory/unit/`, its instance `tests/memory/unit/instance.py`.
`script/test unit` runs `tests/unit/` and every `tests/*/unit/`. The unit lane's rules
(the network guard, the stacklet packages, the stand-ins' address) live in
`tests/lanes/unit.py`, and each unit directory's conftest takes them with one
import, because pytest applies a conftest's hooks only to the tests below it.

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

`tests/js/` tests the dashboard components' pure JavaScript (the family's
pair words, the greeting's hours) with Node's own test runner: plain Node, no
packages, no build, about 0.1 seconds. CI does not run it yet; run it after
changing a file under `stacklets/core/tools-server/dashboard/components/`.

`tests/e2e/eval/` is opt-in prompt and model evaluation. It is excluded
from normal pytest collection and is run with `tests/e2e/stacktests eval`.

### How a correction is worked into a note

`tests/e2e/eval/correction_fold.py` measures whether a refiled note's
clean version works a correction in where it belongs, instead of
appending it as a point of its own. Eight fabricated notes (one in German)
with one or two corrections each, built into the correction prompt by the
real `correct_note` call the refile makes for a note with corrections
alone (the capture prompt that reads its answer is not asked), and
checked in code: the corrected value stands where the old one was, the
old one is gone, nothing restates the correction, and the filing check
(`clean_words.rebuild_problems`) finds nothing lost. `--conversation`
asks the same corrections as the card conversation instead, which a note
a person edited is rebuilt with. It needs only a model server, no rig and
no `./stack`, and it is a script, so pytest never collects it.

```bash
AI_DEFAULT_MODEL=<model id> EVAL_AI_URL=<base url, /v1> EVAL_AI_KEY=<key> \
  uv run --extra test python tests/e2e/eval/correction_fold.py
```

It prints a pass count per case and the first reason a run failed;
`--runs N` (default 3), `-k <words>` picks cases by name, `--show` prints
every clean version. Cost: one model call per case and run, 24 by
default (32 with `--conversation`); the requests it sent are printed at the end.

`tests/e2e/eval/edit_replay.py` measures whether a person's edit of a
note's clean version survives a rebuild. Eight notes, each with a history
of corrections and edits played into a git repository, read back by the
shipped code (`stack.cards.history`) and asked as the card conversation; checked
like `correction_fold.py`, plus the person's own wording and the words
nothing changed. Same environment variables and flags; one call per case
and run, 24 by default.

