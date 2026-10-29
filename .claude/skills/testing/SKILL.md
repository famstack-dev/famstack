---
name: testing
description: The testing strategy in one place. What a test drives and asserts, how it gets the module under test, how it gets a running stacklet (its test instance only), where stand-ins belong, which lane to run, and the rules the 0.5 work taught (no sys.path edits or loading by path, no in-process stand-in for another process, no process-wide patch that counts other threads, module state reset, structure over wording, failed tests keep their logs). Use when writing, changing, moving or reviewing a test, choosing a lane, or when a test passes alone and fails in the parallel lane.
---

# Testing

This file is how we test. `tests/README.md` has the lanes: what each
needs, costs and covers, and the commands. AGENTS.md § 6 and
`docs/agent/dev.md` § Testing point here. Where two of them disagree, fix
the one that is wrong.

## 1. What a test is

A test states an intent and pins the behaviour a caller can see, so both
survive every rewrite of the code underneath. That is the most valuable
thing we produce: implementations get rewritten, intent does not.

- **Drive the surface, from the caller's side, red first.** Where a surface
  exists (a `./stack` command through `Stack.run_cli_command`, a bot as the
  bot runner loads it, an HTTP API), the test drives it, and it is written
  before the code and seen to fail. Where there is none, a module test
  exercises one coherent piece through its public functions. A test that
  reaches for a private helper tests how the code works, and breaks on
  refactors that broke nothing.
- **Name the behaviour.** The name says what a caller observes; the
  docstring is Given/When/Then and one sentence on why the case matters. A
  new reader learns what the module is for from its tests.
- **Assert against something external.** A spec, a service's real answer, an
  invariant we promise. A test written next to the code it covers proves
  the two agree, not that either is right. One that can only fail when
  someone changes their mind is deleted.
- **Structure and content, never wording.** Assert the answer's fields, the
  commits (who, what kind), the files and their parsed content. Not a
  sentence, a word order or an emoji: they change for readers, and a test
  that pins them fails on a change that broke nothing. Where a person must
  be told something, assert the facts the message carries (the card id, the
  path, the command to run), not its phrasing.
- **Fast.** In process through the real entry point by default; a process of
  its own where the code runs in one (section 3). Build what is expensive
  once per session. Surface tests average about 0.2 seconds; a test over a
  second says why in its docstring. A work item reports measured times.

## 2. Getting the module under test

By its package name, as any other code imports it:

```python
from stacklets.memory.cards import CardStore
from stack.cards.sources import Source
```

Every stacklet is the package `stacklets.<id>` (`stack.stacklet_code`); the
unit lane registers them before the first test. A command, a hook or a
migration as the framework runs it: through `Stack.run_cli_command`, or a
fresh copy with `stack.stacklet_code.load(stacklet_dir, path)`, which is
what the framework calls. Code with no package of its own (core's
`tools-server/` modules) loads the same way. A script under `tools/` is not
a package: run it as a process, or load it by its path as
`tests/unit/framework/test_commit_lint.py` does.

Never `sys.path.insert` in a test, and never load a stacklet's module by
its path. A module loaded twice under two names is two modules: the 0.5
conversion met two `Put` classes that refused each other's objects, and a
package registered at a directory a test had deleted. A command once passed
hundreds of tests and failed on its first real call, because the tests had
put its imports on the path for it.

## 3. Getting a running stacklet

From its test instance, and only from there. A stacklet with state has one
module that runs it for tests, `tests/<module>/unit/instance.py`. Memory's:

```python
from tests.memory.unit.instance import running_memory

with running_memory(tmp_path, {LIST: CAMPING}) as memory:
    marge = memory.person("marge")          # runs ./stack as Marge
    marge.writes(LIST, TENT_FOR_FOUR)
    assert memory.new_commits() == [commit(by="marge", saying="Tent for four")]
```

It is Forgejo (a bare repository), the curator's clone, card store and API,
core's card door, a Stack over a fabricated instance (`tests/springfield.py`),
and the bot runner. It reads back what Forgejo holds (`page`, `new_commits`,
`authors`, `committers`, `trailers`, `pages`) and sets the states a test
starts from (`edited_in_forgejo`, `forgejo_down()`, a seed time, a bundle's
history). Memory's own tests take it as the fixture `memory`
(`tests/memory/unit/conftest.py`); another module's tests import
`running_memory`.

A test does not build its own Forgejo, curator API or door: two setups of
one stacklet drift apart, and a test then proves its setup, not the
stacklet. Missing a state or a read? Add it to the instance, once, for
every test.

**Each process in its own process.** What runs in another process on an
instance runs in another process in the test. The instance puts a `docker`
first on `PATH` that answers for the bot runner (`tests/bot_runner.py`):
`docker exec stack-core-bot-runner python /stacklets/...` starts that entry
point on this Mac, in a process of its own, with the environment core gives
the bot runner, so the host's command reaches it unpatched. Threads are
fine for a service reached only over HTTP, which shares no objects with the
test (the curator's API, core's door). Two things are not that process:

- Another process's modules, used by their package name: a module test of
  them (the archivist's mirror, filing through the curator's API). Fine.
- Its code dressed up as the process: an entry point loaded into the test,
  `sys.modules` cleared before and after, its constants patched. Never.

A canned answer at the boundary (`stack.bot_runner.call` returning a
proposal) stands in for the process like any other external stand-in. Where
a process cannot run here yet (a path fixed to its container), the test
says so in its docstring and names what would let it.

**An extension's tests** run through the extension lane (`script/test
extension <folder>`, `tests/lanes/extension.py`), which gives them the
extension installed and up in a Simpsons instance as the fixture `stack`
(`tests/extensions/instance.py`). Every command is a `./stack` process; a
container address resolves to the test's stand-in (`stack.serves`). The
lane's own tests drive the made-up `tests/extensions/kettle/`. How to write
an extension's tests is documented in the extensions repository.

## 4. Stand-ins and data

- **Real collaborators.** Real stacklet code and hooks, real git, the real
  curator with its real card check, real Synapse through the messages
  stacklet in the rig lanes, no test-only compose files. A stand-in only at
  an external boundary: a service we do not run here (Paperless, the model,
  Synapse in the unit lane, answering its client API over HTTP), Docker,
  the clock. `pytest-httpserver` for HTTP. Mock objects for loggers only.
- **A stand-in that agrees where the real one refuses is a self-confirming
  test.** Run against the real one. A behaviour the real one refuses is a
  finding: mark the test `xfail(strict=True)` with the reason, report it,
  and never swap in a stand-in that lets it pass.
- **Prefer a real model.** `tests/e2e/stacktests ai local` gives the rig real
  answers at no cost per call. A green run against a stub proves the wiring.
- **Seed through the front door** (AGENTS.md non-negotiable 11). Data goes in
  the way a family puts it in: documents through `tools/family-docs/ingest.py`,
  never the service's API behind the stacklet. In the unit lane, a test's
  starting state is what the instance can hold (its seed, an edit in
  Forgejo), not files written into the curator's clone.
- **Test helpers do one thing.** A parameter only when a second test needs it.

## 5. The lanes

`tests/README.md` lists them. Run the cheapest that proves the change. The
unit lane is `tests/unit/` and every `tests/<module>/unit/`, run by
`script/test unit` (CI runs the same): offline, no Docker, parallel. Every
other lane owns fixed container names and ports, so one at a time on a Mac;
check nothing else is mid-run first. The rig is shared, not off-limits:
agents may run its lanes, and `tests/e2e/stacktests help` says which
subcommands are autonomous, shared or destructive.

The rig lanes sit on top: they prove the wiring between real containers,
and neither they nor the unit lane replace the other. A change that crosses
a container boundary gets an integration or e2e test; anything that
touches a bot gets a rig surface test, driving the running test instance
with `tests/driver/driver`, before it merges. `tests/e2e/eval/` is opt-in.

Tests are ordered by module, then by lane: `tests/<module>/<lane>/` holds a
module's tests and its test instance. Memory's are in `tests/memory/unit/`;
the framework's and the other stacklets' are still under `tests/unit/`. The
unit lane's rules live in `tests/lanes/unit.py`, and each unit directory's
conftest takes them with one import.

## 6. Rules the parallel lane taught

The unit lane runs on several workers, each a long-lived process running
tests in no fixed order. A test that passes alone and fails there has left
something behind, or found something another test left.

- **A test passes alone and in the lane, in any order.** Run the file on its
  own and with `-n 4 --dist load` (CI's shape) before calling it done.
- **Leave nothing behind.** No thread, server, process, path or module state
  a later test of the same worker could see. Servers stop when their block
  ends; subprocesses are waited for; `PATH` is given back.
- **Reset module state a command sets.** A command that configures its
  process (the diary's language, from `LANGUAGE`) is right in its own
  process and wrong in the test's. The lane gives the diary its English back
  after each test (`tests/lanes/unit.py`); a new such setting gets the same,
  or the command runs as a process.
- **No process-wide patch that counts what other threads do.** A patch of
  `time.sleep` that expected no sleeps counted `subprocess`'s own while it
  waited for a process to exit, and failed on a loaded machine. Assert the
  one call the code under test makes (its poll interval), not the absence of
  every call in the process.
- **Offline.** The lane's network guard fails a test that connects anywhere
  but localhost, or to a port a running instance listens on. Stand the
  service in on localhost.
- **Failed tests keep their logs.** The container lanes keep each running
  container's log since the test started (`tests/failure_logs.py`). A test
  that runs a process puts its stderr in the failure message.
