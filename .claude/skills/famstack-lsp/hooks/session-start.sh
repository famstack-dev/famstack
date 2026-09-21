#!/bin/sh
# SessionStart: stdout becomes context for the agent.
#
# A hook cannot reach the language server Claude Code spawned, so it cannot
# warm the index itself. It tells the agent to, because the LSP tool is
# deferred (listed by name only, easy to read as unavailable) and a cold
# server answers from a partial index instead of waiting.

if ! command -v basedpyright-langserver >/dev/null 2>&1; then
    echo "famstack-lsp: basedpyright-langserver is not on PATH, so there is no language server this session. Install it with 'uv tool install basedpyright' (or run tools/init-repo), then /reload-plugins. Until then, use 'uvx basedpyright <paths>' for diagnostics and grep for navigation."
    exit 0
fi

echo "famstack-lsp: a Python language server is available through the deferred LSP tool. Load it with ToolSearch 'select:LSP' before the first question about a symbol, then warm it with one throwaway workspaceSymbol query for 'HookResolver' on lib/stack/hooks.py and ignore the result: a cold server answers from a partial index. See CLAUDE.md."
