# ADR-006: .env as a Derived Artifact

## Status
Accepted

## Context
Most Docker Compose projects expect users to create and maintain `.env` files manually. This works for single-service setups but falls apart with multiple stacklets sharing config (domain, data dir, timezone) and secrets (generated passwords, API tokens). Manual `.env` editing leads to drift, copy-paste errors, and secrets checked into version control.

## Decision
`.env` files are never edited by hand. They are generated on every `stack up` from three sources:

1. `stack.toml` for global config (paths, domain, timezone)
2. `stacklet.toml [env.defaults]` for templates like `{data_dir}/photos/library`
3. `.stack/secrets.toml` for auto-generated passwords

The `.env` file is overwritten on every run. Gitignored. Treated as a build artifact.

## Consequences
- Users edit one file (`stack.toml`), not six `.env` files
- Secrets are auto-generated and never need to be invented or typed
- Changing the data directory or domain propagates to all stacklets on next `stack up`
- Anyone who edits `.env` directly will lose their changes. The CLI warns about this but it still surprises people
- Template variables are a closed set. Adding a new one requires a framework change, not just a stacklet change
