---
name: testing
description: The testing strategy in one place. What a test drives and asserts, how it gets the module under test, how it gets a running stacklet (its test instance only), which lane to run, and the rules the 0.5 work taught (no sys.path edits or loading by path, no in-process stand-in for another process, no process-wide patch that counts other threads, module state reset, structure over wording, failed tests keep their logs). Use when writing, changing, moving or reviewing a test, choosing a lane, or when a test passes alone and fails in the parallel lane.
---

# Testing

This file is the strategy; `tests/README.md` has the lanes, their cost and
the commands. Where the two disagree, fix one of them.

## 1. What a test is

A test states an intent and pins the behaviour a caller can see, so both
survive every rewrite of the code underneath.

- **Drive the surface, from the caller's side.** Where a surface exists (a
  `./stack` command, a bot as the bot runner loads it, an HTTP API), the test
  drives it, and it is written first, red before the code. Where there is
  none, a module test exercises one coherent piece through its public
  functions. A test that reaches for a private helper tests how the code
  works, and breaks on refactors that broke nothing.
- **Name the behaviour.** The test's name says what a caller observes, its
  docstring is Given/When/Then and one sentence on why the case matters.
- **Real collaborators.** Real stacklet code, real git, a real curator, real
  Synapse in the rig lanes. A stand-in only at an external boundary: a
  network service we do not run here (Paperless, the model, Matrix in the
  unit lane), Docker, the clock. `pytest-httpserver` for HTTP stand-ins.
- **Assert against something external.** A spec, a service's real answer, an
  invariant we promise. A test written next to the code it covers proves
  the two agree, not that either is right; one that can only fail when
  someone changes their mind is deleted.
- **Structure and content, never wording.** Assert the answer's fields, the
  commits (who, what kind), the files and their parsed content. Not a
  sentence, a word order or an emoji: they are changed for readers, and a
  test that pins them fails on a change that broke nothing. Where a person
  must be told something, assert the facts the message has to carry (the
  card id, the path, the setting to change), not its phrasing.

## 2. Getting the module under test

By its package name, as any other code imports it:

```python
from stacklets.memory.cards import CardStore
from stack.cards.sources import Source
```

Every stacklet is the package `stacklets.<id>` (`stack.stacklet_code`); the
unit lane registers them before the first test. For a command, a hook or a
migration as the framework runs it, load a fresh copy with
`stack.stacklet_code.load(stacklet_dir, path)`, which is what
`Stack.run_cli_command` does, or run it through `Stack.run_cli_command`.

Never `sys.path.insert` and never `importlib.util.spec_from_file_location`
in a test. A module loaded twice under two names is two modules: the 0.5
conversion met two `Put` classes that refused each other's objects, and a
package registered at a directory a test had deleted. A command once passed
hundreds of tests and failed on its first real call, because the tests had
put its imports on the path for it.

## 3. Getting a running stacklet

From its test instance, and only from there. A stacklet with state has one
module that runs it for tests, `tests/<module>/unit/instance.py`:

```python
from tests.memory.unit.instance import running_memory

with running_memory(tmp_path, {LIST: CAMPING}) as memory:
    ...
```

Memory's own tests take it as the fixture `memory` (`tests/memory/unit/
conftest.py`); another module's tests import `running_memory` explicitly. A
test does not build its own Forgejo, its own curator API or its own door:
two setups of one stacklet drift apart, and a test then proves the setup,
not the stacklet. Missing a state or a read? Add it to the instance, once,
for every test.

**Each process in its own process.** What runs in another process on an
instance runs in another process in the test, never as an in-process
stand-in that swaps `sys.modules` or `sys.path` to look separate. Threads
are fine for a service reached only over HTTP, which shares no objects with
the test (the curator's API, core's card door).

## 4. The lanes

`tests/README.md` lists them, with what each needs and costs. Run the
cheapest one that proves the change. The unit lane is `tests/unit/` and
every `tests/<module>/unit/`, run by `script/test unit` (CI runs the same);
it is offline, no Docker, and parallel. Every other lane owns fixed
container names and ports, so one at a time on a Mac.

Tests are ordered by module, then by lane: `tests/<module>/<lane>/` holds a
module's tests and its test instance. Memory is the first; the framework
and the other stacklets are still under `tests/unit/`.

## 5. Rules the parallel lane taught

The unit lane runs on several workers, each a long-lived process running
tests in no fixed order. A test that passes alone and fails there has left
something behind, or found something another test left.

- **A test passes alone and in the lane, in any order.** Run the file on its
  own and with `-n 4 --dist load` (CI's shape) before calling it done.
- **Leave nothing behind.** No thread, server, process or module state a
  later test of the same worker could see. Servers stop when their block
  ends; subprocesses are waited for.
- **Reset module state a command sets.** A command that configures its
  process (the diary's language, from `LANGUAGE`) is right in its own
  process and wrong in the test's. The lane gives the diary its English back
  after each test (`tests/lanes/unit.py`); a new such setting gets the same,
  or the command runs as a subprocess.
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
  that runs a subprocess puts its stderr in the failure message.
