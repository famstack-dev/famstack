# AGENT.md

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

Four principles, distilled from [Andrej Karpathy's observations on LLM coding pitfalls](https://github.com/multica-ai/andrej-karpathy-skills). Apply to every change, every role. **Tradeoff:** these bias toward caution over speed. For trivial tasks (typos, obvious one-liners), use judgment.

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

## Deeper docs (load on demand)

| Doc | Purpose |
|---|---|
| [README.md](README.md) | Intro + quickstart |
| [docs/user-guide.md](docs/user-guide.md) | Full operator manual (prose) |
| [docs/stack-reference.md](docs/stack-reference.md) | Framework reference: manifest, hooks, env, lifecycle |
| [docs/creating-stacklets.md](docs/creating-stacklets.md) | How to author a stacklet |
| [docs/adr/](docs/adr/) | Architecture decision records - the "why" |

## How to use this file

- **Weaker models:** load only `AGENT.md` + your role file. Stop there until a task requires more.
- **Stronger agents:** load both role files and pull deeper docs as the task demands.
- **Humans skimming:** the role file is faster than the user guide for "what am I allowed to do here".
