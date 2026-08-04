# famstack-lsp

A skills-directory plugin: Claude Code discovers it in place as `famstack-lsp@skills-dir` on the next session, with no marketplace and no install step. It only starts once you have accepted the workspace trust dialog for this repo.

It wires one thing: `basedpyright-langserver` over stdio for `.py` and `.pyi`. That gives an agent working in this repo live diagnostics after each edit, plus go-to-definition and find-references, instead of grepping for a symbol and hoping.

## Requirement

The binary is not bundled. Install it once:

```bash
uv tool install basedpyright     # provides basedpyright and basedpyright-langserver
```

If `/plugin` shows `Executable not found in $PATH`, that install is missing or `~/.local/bin` is not on your `PATH`.

## Configuration

There is none here on purpose. The language server reads `[tool.basedpyright]` in the repo's `pyproject.toml`, the same table the `uvx basedpyright` CLI reads, so the two never disagree. That table is where the import roots live: this repo has no installed package, and every stacklet bootstraps its own directory onto `sys.path` at import time, so each one needs an execution environment or its imports go dark.

See [CONTRIBUTING.md](../../../CONTRIBUTING.md) for the full toolchain.
