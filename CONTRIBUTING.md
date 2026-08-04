# Contributing

This is the developer toolchain: what to install, and how the static checks are wired. For how to write code here (stacklets, hooks, tests, commits) read [AGENTS.md](AGENTS.md) and [docs/agent/dev.md](docs/agent/dev.md). For running famstack rather than changing it, read the [README](README.md).

famstack targets Apple Silicon Macs only. Intel macOS, Linux and Windows are not targets.

## Setup

One entry point, [Homebrew](https://brew.sh), which brings the rest:

```bash
brew install uv
uv sync --extra test
```

`uv sync` creates `.venv` in the repo root. Nothing needs activating: `uv run` and `uvx` find it, and the type checker is pointed at it by `pyproject.toml`. The `test` extra declares every dependency the test suite needs, including the bot runtime libraries that tests import directly.

The CLI itself depends only on the standard library. Stacklet containers carry their own runtime dependencies, which is why `.venv` will not contain all of them: `nanobot` and `fastapi`, for example, exist only inside their images.

## Static checks

```bash
uvx ruff check .        # undefined names, unused imports, syntax-level mistakes
make typecheck          # types across the shipped code
uvx basedpyright <path> # types in just the files you touched
```

Ruff is the fast pass. [basedpyright](https://docs.basedpyright.com) is the layer above it: wrong argument types, attribute access on a value that can be `None`, variables possibly unbound on one branch. A full run takes a few seconds.

Neither is a merge gate, and the type checker is not clean today. Run it on what you changed and read what it says about that. Treating a 198-error baseline as a wall to bring to zero is not the job.

## Language server

Agents working in this repo get a language server, so they can ask for a definition or every reference to a symbol instead of grepping for a name and hoping it is unique. Install the binary once:

```bash
uv tool install basedpyright
```

The wiring is checked in at [.claude/skills/famstack-lsp/](.claude/skills/famstack-lsp/), which Claude Code discovers in place as a skills-directory plugin. There is no install step and no marketplace; it starts after you accept the workspace trust dialog. Editors other than Claude Code point at the same `basedpyright-langserver` binary through their own LSP configuration.

That trust dialog is the one thing likely to bite you, because it can fail to appear. Trust is recorded per directory, but permissions inherit from a trusted parent, so opening this repo under an already-trusted parent directory gets you a session with no dialog, no error, and no language server. Nothing reports it except `claude plugin list`, which names the suppressed directory outright. If the LSP is missing, run that first; the fix is to accept trust for this directory and then `/reload-plugins`.

The `lspServers` field is a Claude Code CLI feature. Agent harnesses built on the Claude Agent SDK read the rest of a plugin but do not start its language servers, so an agent running under one has no navigation tools and should say so rather than pretend. The `uvx basedpyright` CLI is the fallback for the diagnostics half, and it is the same engine reading the same config.

Because the language server and the `basedpyright` CLI are the same engine reading the same `[tool.basedpyright]` table, they cannot drift apart on what the config means. They can still disagree about which config they are on: the server reads `pyproject.toml` at startup and does not watch it, so after editing that table the CLI is current and the server is not. `/reload-plugins` resyncs it.

One trap while editing the table: a `typeCheckingMode` inside an `executionEnvironments` entry is accepted and then ignored, with no warning that it did nothing. Per-environment diagnostic overrides are not the lever they look like. Verify a config change by the error count it produces, not by whether the file parsed.

## Import roots, and why the type config is long

This repo has no installed package. The framework lives in `lib/`, and every stacklet bootstraps its own directory onto `sys.path` at import time, in more than two hundred places. A checker that knows only about the repo root therefore reads `import stack.config`, `from microbot import ...` and `import memory.lib` as unresolved, and the noise buries every real finding.

So `[tool.basedpyright]` in `pyproject.toml` declares one execution environment per stacklet, each naming the roots that stacklet actually puts on the path. They cannot be collapsed into a single global `extraPaths`, because `hooks/`, `cli/` and `bot/` exist under several stacklets and would resolve to whichever came first.

**If you add a stacklet, add its execution environment.** Otherwise its imports go dark and it silently stops being checked at all.

Three roots recur, and are worth recognising when you read that table:

| Root | Why |
|---|---|
| `lib` | the framework, `stack.*` |
| `stacklets/core/bot-runner` | every bot imports `microbot` from there |
| `stacklets` | cross-stacklet reads such as `memory.lib` |

A bare `make typecheck` reports on `lib`, `stacklets`, `tools` and `hooks`. `tests` is in `include` as well, but listed under `ignore`, which is a different thing from being left out: the files are still analysed, so the language server resolves them and find-references reaches test callers, while their diagnostics are suppressed. Fixtures pass deliberately loose dicts into typed SDK calls, and their several hundred complaints would bury the roughly two hundred about shipped code.

`exclude` would have been the wrong tool for that. It drops files from the index and takes navigation down with them, which is the thing worth having. Note that `ignore` beats a filename passed on the command line, so `uvx basedpyright tests/...` reports nothing: to check a test file, comment the `ignore` line out.

## Tests, style, commits

All three live in [docs/agent/dev.md](docs/agent/dev.md), which is the canonical reference and stays shorter than a duplicate here would. The short version: `make test-unit` before every commit, module tests over unit tests, semantic commit prefixes, feature branches only, never push without asking.
