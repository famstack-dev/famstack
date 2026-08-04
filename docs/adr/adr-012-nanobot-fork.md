# ADR-012: Fork nanobot instead of shimming it

## Status
Proposed

Supersedes the "no fork" position recorded in
[docs/design/agent/addressing.md](../design/agent/addressing.md) and in
`stacklets/agent/runtime/README.md`, both of which already name the
condition for revisiting it: "once we accumulate several nanobot changes".
We have.

## Context

The agent stacklet runs upstream `nanobot-ai==0.2.2` unmodified and reshapes
it at runtime from `stacklets/agent/runtime/sitecustomize.py`, which Python
auto-imports because the directory is on `PYTHONPATH`. That file now installs
**ten shims over thirteen symbols**:

| Shim | Patches | Kind |
|---|---|---|
| `brief` | `agent.context.runtime_lines` | public-ish |
| `lean_state` | `agent.context.ContextBuilder.build_messages` | public-ish |
| `memory_tool` | `agent.tools.loader.ToolLoader.discover` | addition |
| `person_tool` | same | addition |
| `history_tool` | same | addition |
| `grep_tool` | `agent.tools.search.GrepTool.execute` | replacement |
| `vault_write` | `WriteFileTool` / `EditFileTool` / `ApplyPatchTool.execute` | replacement |
| `name_trigger` | `channels.matrix.MatrixChannel._is_bot_mentioned` | **private** |
| `thread_trigger` | `_is_bot_mentioned`, `_on_message`, `_on_media_message` | **private** |
| `join_greeting` | `_on_room_invite`, `_handle_message` | **private** |

Five of those symbols are underscore-prefixed. Upstream owes us nothing for
them, and has already moved one: 0.2.x turned `nanobot.channels.matrix` from a
module into a package, which detached a shim and took a live instance off
Matrix with no error anywhere.

The decision to shim was right when there were two of them. This ADR records
what changed and what it has cost.

## Lessons

### 1. Failing soft makes a broken shim invisible

Every shim is wrapped in try/except-and-log, deliberately: a broken shim must
never stop the agent answering. The price is that a shim which fails to attach
looks exactly like one that worked. The container starts, reports healthy,
logs a line nobody reads, and the capability is simply gone. Three vault tools
sat dead in the image for weeks that way.

This is not a bug in the wrapping. It is the shape of the technique. A method
that is *supposed* to be there either is or is not; a monkeypatch that is
supposed to have replaced it has a third state, and that third state is silent.

### 2. The shim tests cannot catch the thing that actually breaks shims

`tests/stacklets/test_agent_runtime_shims.py` asserts every patch is attached,
which is real value: it catches *our* mistakes. It cannot catch upstream
moving a symbol, because it runs against a stub nanobot this repo hand-writes.
The stub still has the old symbol, so the lane stays green while production is
broken. The file says so itself.

What actually holds this line is the version pin in the Dockerfile, which is a
promise to re-read thirteen symbols by hand on every bump. That is a manual
gate guarding an automated system, and it is the wrong way round.

### 3. Private, synchronous seams force contorted shapes

`thread_trigger` is the clearest case. The question "is this message in a
thread the agent is part of" needs the homeserver. The gate nanobot exposes
(`_is_bot_mentioned`) is synchronous. So the shim had to split in half: an
async pre-resolution wrapped around `_on_message` that settles the question
and remembers it, and a synchronous set lookup in the gate. Plus the same
wrapper again on `_on_media_message`, because there are two entry points.

In a fork that is one `async def is_addressed(...)`. The split exists only
because we cannot change a method signature.

### 4. Shims compose by accident, not by design

`name_trigger` and `thread_trigger` both wrap `_is_bot_mentioned`. The second
wraps whatever the first left behind, so the chain is thread check, then name
check, then upstream's pill check. That works, and it is genuinely nice that
one failing leaves the other intact.

But the order is implicit in the order of two `try` blocks in one file, and
nothing anywhere states the intended precedence. The next person to add a gate
shim will get it right by luck or not at all.

### 5. The expensive bugs were not in the shims. They were under them

The failure that cost a five-minute runaway loop on a live rig had nothing to
do with monkeypatching:

* the container-side `stack` shim joined argv with spaces while the host
  rebuilt it with `shlex.split`, so every multi-word query lost its boundaries;
* that shim exited 0 whatever happened, so a usage error reached the model
  dressed as search results and it asked the same question again, forever.

Both live at the seam between the agent and the rest of famstack. The shim
machinery is where the attention went; the defects were one layer down, in
plumbing nobody had a test for. **Complexity at one layer buys inattention at
the next.**

### 6. Seams between two components are where contracts rot

Two artifacts, both written against an output format that has never existed:

* `grep_tool._PATH_RE` parses `#1 ... score=` to build a "Paths to read:"
  block. `stack memory search` prints `2026-08-03 [Marge] path.md`. The regex
  has never matched, so that block has always been empty.
* `memory_tool`'s description promises the model "rank, score, vault path,
  snippet, and source links". There is no rank, no score, no source links.

Nobody wrote these carelessly. They were written against an imagined
contract and never run against the real one, because the only thing that
exercises them is a live model in a container.

### 7. A shim cannot fix a contract mismatch. It relocates it

`grep_tool` routes vault greps into `memory_search` so the agent gets semantic
hits instead of literal ones. But `stack memory search` takes a Python regex,
so the routing changes *which* wrong answer the model gets, not whether it
gets one. See [the memory query-language note](../design-notes.md).

The lesson generalises: shims are good at "call our code instead of theirs".
They are bad at "make two components agree", and reaching for one there hides
the disagreement instead of resolving it.

## Decision

Fork `nanobot-ai`, land the internals-patching shims as real code, and keep
the pure modules exactly as they are.

**What moves into the fork.** Everything that patches a nanobot internal:
`brief`, `lean_state`, `grep_tool`, `vault_write`, `name_trigger`,
`thread_trigger`, `join_greeting`. Each becomes a method or a real extension
point rather than a replaced attribute.

**What does not move.** The pure modules are the good part of the current
design and they survive the transition unchanged. `name_trigger.py` is text
in, bool out. `thread_trigger.py` is a policy plus two homeserver reads.
`brief.py` assembles from the vault and never raises. Each is unit-testable
without a container, and each is specified by tests that read as documentation
of intent. The fork calls them; it does not absorb them.

That split is the thing to preserve: **the fork owns the wiring, our modules
own the decisions.** A fork that swallows the policy logic trades one
maintenance problem for a worse one.

**The three vault tools are a separate question.** `memory_search`,
`memory_person` and `memory_history` are *additions* through
`ToolLoader.discover`, not replacements of upstream behaviour. If upstream
keeps a discovery seam they can stay outside the fork. Simpler is to move them
in with everything else and stop having two mechanisms.

## Migration shape

1. Fork at the currently pinned release. No behaviour change in step one, so
   the diff is reviewable as "shims became methods".
2. Land the seven internals shims as real code, keeping the pure modules as
   imports.
3. Replace stub-based attachment tests with tests against the real package.
   A fork means we can import what we changed, which retires the whole class
   of "green lane, broken production" described in lesson 2.
4. State the addressing precedence in one place (pill, name, thread) now that
   it is one function instead of two wrappers.
5. Upstream the seams that are generally useful: a context-provider API, a
   group-policy hook, an async addressing gate. Every accepted upstream patch
   is a line the fork no longer carries.

## Consequences

**We own updates.** Today a nanobot release is `docker build`; afterwards it
is a rebase. That is the real cost and it is not small.

It buys: symbol drift becomes a merge conflict instead of a silent runtime
detach; tests run against the actual code; the sync/async contortions go away;
and the pin stops being a manual thirteen-symbol audit.

**Fork rot is the risk.** The mitigation is a rule, not a hope: anything that
could be upstream is offered upstream first, and the fork's diff is expected
to shrink over time. If it is still growing after two releases, that is the
signal to reconsider nanobot itself rather than to keep patching.

## Alternatives considered

**Keep shimming.** Rejected. The technique is sound at two or three patches
and we are at ten, five of them on private methods. Lesson 2 says the cost is
not "more maintenance" but "no way to know it is broken".

**Vendor nanobot into the repo.** Rejected without a fork's upstream link: we
would inherit every maintenance cost and lose the path back.

**Replace nanobot.** Out of scope here. Worth revisiting only if the fork
diff keeps growing.

## Open

* Where the fork lives. Arthur refers to reactivating an existing one; it is
  not visible under `famstack-dev` or `arthware-dev` from this machine.
* Whether the vault tools move in or stay on the discovery seam.
