# RFC-003: An Agentic Development Harness on macOS

## Status
Draft. No decision taken. Written after a session where most of the effort
went into not breaking the one instance this machine has.

## Context

macOS has no container. That is the premise everything else follows from.
Linux gives an agent a throwaway root filesystem for the price of a syscall;
here the only true isolation is a virtual machine, and the cheap primitives
isolate the wrong half of this product.

famstack is half containers and half host: Docker services on one side, and on
the other the `./stack` CLI, Homebrew packages, launchd agents
(`dev.famstack.api`, `dev.famstack.whisper`), a crontab entry, `~/.omlx`
models, and the data directory. A Linux container isolates the first half,
which was never the problem.

### What macOS actually offers

| Mechanism | Isolates | Cost | Verdict |
|---|---|---|---|
| Linux containers (OrbStack) | the container half only | free, already here | the half that already behaves |
| macOS guest VMs (Virtualization.framework, Tart) | everything | tens of GB, minutes to boot, and Apple's licence allows two guests per host | too heavy per agent, and capped |
| A second macOS user account | `$HOME`, launchd domain, crontab, keychain, and its own Docker daemon (OrbStack's socket is `~/.orbstack/run/docker.sock`, per user) | one-time setup, slow to switch | real, but ports stay shared |
| `sandbox-exec` | processes, partially | deprecated and undocumented | no |
| APFS clonefile (`cp -c`) | nothing | 200 MB cloned in 0.00s, no extra space | not isolation, but instant state |
| git worktree + `STACK_DIR` | source and config | free | no runtime isolation |

Isolation is also not always the goal. On a development machine the instance
is the Simpsons demo and an agent may do anything to it, including destroy and
reinstall. What matters there is speed of recovery, not protection. Protection
matters on a production Mac, which is a different machine holding a real
family's data, and the two need opposite defaults rather than one careful
compromise.

No single mechanism isolates the whole product cheaply. So isolation has to be
**constructed in the framework**, and each OS mechanism used for the one thing
it is good at.

## What blocks an agent today

Observed, not imagined. Every item cost time in one session.

1. **One instance per Mac.** Container names (`stack-<id>`), the `stack`
   network, ports 42xxx, both launchd labels and the crontab entry are
   machine-global. Two instances cannot run at once.
2. **`project_states()` maps any `stack-*` compose project to a stacklet id**
   (`lib/stack/docker.py`). An agent working in a clone sees the other
   instance's containers as its own, so `stack list` lies before anything
   collides. This happened: a scratch clone reported the live rig's stacklets
   as running, and its restart advice named them.
3. **`_refresh_core` recreates core after any `stack up`**, so an instance
   that touches any stacklet touches the one shared bot runner.
4. **An agent cannot tell which machine it is on.** Development happens on a
   laptop whose instance is the Simpsons demo, where an agent may do anything,
   including destroy and reinstall. Production is a different Mac with a real
   family's photos, documents and messages, where an agent must never act
   invasively. Nothing in the CLI reports which one it is standing in, so
   caution has to be applied uniformly, which means it is applied where it
   costs and forgotten where it matters.
5. **Shared lanes are coordinated by courtesy.** `tests/README.md` asks you to
   check nothing else is mid-run. Nothing enforces it.
6. **Lane costs are learned by running them.** Unit is 75s, the Docker
   lifecycle lane is about 6 minutes, demo-rig needs a live instance. An agent
   discovers this the expensive way.

## Proposal

In cost order. Each stands alone and is worth having without the next.

### 1. A lane contract (an afternoon)

Publish what each lane costs and touches, as a table an agent reads before
choosing, and as `stacktests lanes` for one it can parse: name, wall time,
what it needs running, what it mutates, whether it is exclusive.

*Gate:* an agent that has never run the suite picks the right lane from the
table alone, and says what it will cost before running it.

### 2. Snapshot and restore an instance (a day)

`stack snapshot <name>` clones `~/famstack-data`, `.stack/` and `stack.toml`
with `cp -c`; `stack restore <name>` puts them back. On APFS that is instant
and takes no space, which is the macOS answer to what a container image gives
you elsewhere: a known state to return to.

This is the highest value per hour on this list. It turns every destructive
lane from "reseed for twenty minutes" into "restore and carry on", and it
makes an agent willing to run the destructive path at all.

*Gate:* destroy the docs stacklet, restore, and the archivist files a document
end to end without reseeding anything.

*Caveat:* a Postgres data directory cloned while running is a crash-recovery
scenario, not a clean backup. Snapshot with the stacklet down, or accept that
restore starts with WAL replay.

### 3. A lease on the shared lanes (an afternoon)

A lock file naming holder, worktree, lane and start time. `stacktests` takes
it, releases it on exit, and breaks a stale one loudly rather than silently.

*Gate:* a second agent starting the same lane is told who holds it and since
when, instead of corrupting the run.

### 4. Instance namespacing (weeks, structural)

Derive every runtime name from an instance id: compose project prefix,
container names, network, a port base (`42000 + n*100`), launchd labels, the
crontab tag. Scope `project_states()` to the instance's own prefix.

Then a git worktree, plus `STACK_DIR`, plus an instance id is a genuinely
parallel instance, and agents stop queueing. This is the same work
[RFC-002](rfc-002-installation-instances-and-updates.md) names as Phase 5, so
it is one job serving two goals: parallel agents and multiple households.

*Gate:* two instances up at once on one Mac, each `stack list` reporting only
its own containers, both reachable.

### 5. An instance that says which machine it is (an afternoon)

The rig already has half of this. `tests/e2e/stacktests` refuses to
run over a real stack unless `.stack/.test-instance` is present, and the
comment above it says why: "running the rig over someone's real stack errors
out instead of overwriting it". That sentinel protects one script.

Generalise it: an instance declares its role, `stack status` and `stack list
--json` report it, and every destructive path gates on it. A development
instance is disposable and an agent may destroy and reinstall it at will; a
production one refuses the same command and says why.

This is worth more than it costs, because it inverts the default. Today an
agent is careful everywhere, which is slow on the machine where care is
pointless and unreliable on the machine where it is the only thing standing
between an agent and a family's photos.

*Gate:* `stack destroy` on a production instance refuses without an explicit
override; the same command on a development instance runs without ceremony,
and an agent can read the difference before it acts.

### 6. A second macOS user as the bridge (optional)

If namespacing proves too invasive to do soon, a second user account gives
separate `$HOME`, launchd domain, crontab, keychain and Docker daemon today,
for the price of `su -` ergonomics. Ports remain the conflict, which is the
thing namespacing fixes anyway. A bridge, not a destination.

## What not to build

- **A macOS VM per agent.** Apple's licence caps guests at two per host, they
  cost tens of gigabytes and minutes to boot, and what they isolate is mostly
  the half already isolated by containers.
- **Mocks to avoid containers.** The repo's own testing rules reject this: a
  green run against a stub proves the wiring, not the behaviour.
- **A bespoke test runner.** The pytest lanes exist and work.

## Consequences

- Namespacing touches every compose file, both launchd hooks and the backup
  cron. It is the largest change here and the only one that needs design.
- Snapshots make the destructive lanes safe, which will make agents use them
  more. That is the point, and it will find bugs the careful path never did.
- APFS clones live on one volume. A data directory moved to an external SSD
  clones within that SSD, not across.
- Items 1 to 3 are worth doing even if 4 never happens, and none of them block
  it.

## Open questions

1. Is one parallel instance per agent enough, or do we want disposable
   instances created and destroyed per task?
2. What declares the role: a sentinel file like the rig's, a `[core] role`
   key, or inference from the data (the demo instance is recognisable by its
   Simpsons family)? A file is explicit and forgeable; inference is automatic
   and guessy.
3. Does the AI half (oMLX, Whisper, models under `~/.omlx`) get shared between
   instances, or does each one pay for its own models?
