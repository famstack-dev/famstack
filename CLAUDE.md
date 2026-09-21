@AGENTS.md
@docs/agent/ops.md
@docs/agent/dev.md

## Claude Code: the language server

The `famstack-lsp` plugin (`.claude/skills/famstack-lsp/`) runs basedpyright for this repo. Two things about it are specific to this harness:

- **The `LSP` tool is deferred.** It is listed by name only, not with the other tools, so it reads as unavailable. Load it with ToolSearch `select:LSP` before the first question about a symbol.
- **Warm it before trusting it.** Run one `workspaceSymbol` query for `HookResolver` on `lib/stack/hooks.py` and ignore the result. It returns a single line, and it makes the server index the workspace.

The plugin's SessionStart hook says both at the start of every session, and says when `basedpyright-langserver` is missing instead. If neither message appears, the plugin is not loaded: run `claude plugin list` (see CONTRIBUTING.md § Language server).

Use it for definitions, references, callers (`incomingCalls`) and types (`hover`). Use grep for text. How to read its answers is in the engineer file, § Static checks.
