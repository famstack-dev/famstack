# AGENTS.md

Load this file first. Then load the file that matches your role.
Skip the rest until you need it.

## Repo

**famstack** - self-hosted family server stack for macOS on Apple Silicon.
A single-file Python CLI (`./stack`) orchestrates Docker-based services
("stacklets") on the host.

- Source: https://github.com/famstack-dev/famstack
- License: AGPLv3
- Status: pre-1.0, beta. **Coherence over process.**

## Two roles

| You are a... | Load this | Use it to... |
|---|---|---|
| **Operator** (running famstack on a Mac) | [docs/agent/ops.md](docs/agent/ops.md) | install, start/stop, troubleshoot, back up |
| **Engineer** (changing famstack code) | [docs/agent/dev.md](docs/agent/dev.md) | write stacklets, hooks, CLI plugins, tests, commits |

If you might do both, load both. They are short on purpose.

## Approach (universal)

Six principles. The first four are distilled from [Andrej Karpathy's observations on LLM coding pitfalls](https://github.com/multica-ai/andrej-karpathy-skills); the fifth is classic separation of concerns; the sixth is what we test and why. Apply to every change, every role. **Tradeoff:** these bias toward caution over speed. For trivial tasks (typos, obvious one-liners), use judgment.

### 1. Think before acting
Don't assume. Don't hide confusion. Surface tradeoffs.
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them. Don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity first
Minimum code/change that solves the problem. Nothing speculative.
- No features, abstractions, or flexibility beyond what was asked.
- No error handling for impossible scenarios.
- If 200 lines could be 50, rewrite.
- Test: would a senior engineer call this overcomplicated?

### 3. Surgical changes
Touch only what you must. Clean up only your own mess.
- Don't "improve" adjacent code, comments, or formatting.
- Match existing style even if you'd do it differently.
- Notice unrelated dead code? Mention it. Don't delete it.
- Remove orphans your changes created. Don't sweep pre-existing dead code.
- Test: every changed line traces directly to the user's request.

### 4. Goal-driven execution
Define success criteria. Loop until verified.
- "Add validation" → "Write tests for invalid inputs, then make them pass."
- "Fix the bug" → "Write a test that reproduces it, then make it pass."
- "Refactor X" → "Ensure tests pass before and after."
- For multi-step tasks, state a brief plan with a verify check per step.

### 5. Separate concerns where they belong
Put each responsibility with the component that owns the resource or contract, and keep one producer's domain knowledge out of a generic consumer.
- **Own the resource, own the concern.** Only the thing wired to a resource should act on it (only the archivist talks to Paperless, so filing lives there; don't duplicate it or route around it).
- **Consume framework contracts generically.** A generic seam (`dev.famstack.source`, `dev.famstack.attachment`, hooks, the manifest) is handled by reading the contract, not by hardcoding one producer's schema. The archivist files *any* source card; it does not know email's fields. Email's shape stays in the mail bot.
- **Split by kind of work.** Pure transformation → its own module, no I/O (unit-testable). I/O and orchestration → the bot/CLI. Domain schema → the producer of that domain.
- **Before adding a handler, ask "is this this component's concern, or just convenient here?"** If the logic is specific to another producer, keep the generic seam and push the specifics back to where they belong. Convenience is not a reason to couple.

### 6. Module tests over unit tests
A module test exercises one coherent piece of functionality from the outside, as a client of it would. Its job is to state the *intent* and pin the *expected behaviour* at the time of writing, so both survive every later refactor. This is the single most valuable thing we produce: implementations get rewritten, intent does not.

- **Write from the caller's side.** Drive the module through its public surface. If a test reaches for a private helper, it is testing how the code works instead of what it promises, and it will break on refactors that broke nothing.
- **Read like good API documentation.** Name the behaviour, not the function. Say why the case matters. A new reader should learn what the module is *for* from its tests alone.
- **Prefer real collaborators.** Real stacklets, real Synapse, a real model via `stacktests ai local`. Mock only what you cannot run, and only at an external boundary.
- **Beware self-confirming tests.** A test written alongside the code it covers proves the two agree, not that either is right. When the fixture encodes the same assumption as the implementation, both pass and reality still disagrees. Assert against an external contract (a spec, a service's real response, an invariant we promise) rather than a restatement of the code.
- **Delete tests that only mirror the implementation.** If it could only fail when someone deliberately changes their mind, it is costing tokens and buying nothing.
- **demo-rig and e2e sit on top.** Module tests carry the intent; the rig lanes prove the wiring holds between real containers. Neither replaces the other.

## Universal non-negotiables

Apply to every role, every session.

1. **Apple Silicon only.** Intel macOS, Linux, Windows are not targets.
2. **Lowercase "famstack".** Never "FamStack" or "Famstack". Product name is always lowercase.
3. **Never `git push` without explicit human approval.** Every push, every branch, every time.
4. **Never commit to `main`.** Feature branches only.
5. **No `Co-Authored-By:` trailers** in commit messages.
6. **Semantic commit prefixes:** `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `test:`, `ci:`, `style:`.
7. **Never read or copy a user's production family vault.** Reference paths only; fabricate test data.
8. **Destructive ops need confirmation.** `stack destroy`, `stack uninstall`, `rm -rf`, `git reset --hard`, force-push.
9. **Announce actions before running them.** No silent long running or integration test runs, scripts, or background commands.
10. **No em dashes in user-facing prose.** Use hyphens or sentence breaks.
11. **Put data in through the front door.** Seed and exercise a stacklet the way a family does, never by writing to the service behind it. Documents go through `tools/family-docs/ingest.py` (Matrix room -> archivist -> OCR -> classify -> mirror), never a direct `POST /api/documents/post_document/`. The back door skips the pipeline, so what lands is not what users get: no tags, no correspondent, no document type, no rewritten title, no summary note, no vault entry. Any conclusion drawn from that data is about a system famstack does not ship.

## Deeper docs (load on demand)

| Doc | Purpose |
|---|---|
| [README.md](README.md) | Intro + quickstart |
| [docs/admin-guide.md](docs/admin-guide.md) | Full operator manual (prose) |
| [docs/user-guide.md](docs/user-guide.md) | Family-facing chat usage guide |
| [docs/stack-reference.md](docs/stack-reference.md) | Framework reference: manifest, hooks, env, lifecycle |
| [docs/creating-stacklets.md](docs/creating-stacklets.md) | How to author a stacklet |
| [docs/adr/](docs/adr/) | Architecture decision records - the "why" |

## How to use this file

- **Weaker models:** load only `AGENTS.md` + your role file. Stop there until a task requires more.
- **Stronger agents:** load both role files and pull deeper docs as the task demands.
- **Humans skimming:** the role file is faster than the user guide for "what am I allowed to do here".
