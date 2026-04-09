# ADR-008: Convention-Based Bot Runner

## Status
Accepted

## Context
famstack bots run in Matrix chat rooms and automate tasks (filing documents, transcribing voice, managing the server). We needed a way to discover and run bots from different stacklets without a central registry or config file.

Alternatives considered:
- Central bot config in `stack.toml`: couples all stacklets to a single file
- Bot registration API: over-engineered for the scale
- Separate Docker container per bot: wasteful, Matrix clients are heavy

## Decision
Bots are discovered by filesystem convention. Any stacklet can ship a bot by adding a `bot/` directory with a `bot.toml` manifest and a Python module. The bot runner (in core) scans all enabled stacklets for `bot/bot.toml` on startup and runs everything in one async process.

Convention: `bot.toml` declares identity, `{id}.py` (with `-bot` stripped) contains the class. Example: `archivist-bot` in `bot.toml` maps to `archivist.py` and class `ArchivistBot`.

## Consequences
- Adding a bot to a stacklet requires zero framework changes
- All bots share one Matrix connection pool and one Python process
- A crashing bot can take down other bots (mitigated by exception isolation per callback)
- Bot discovery happens at startup. Adding a bot requires `stack restart core`
- The convention is rigid but simple. No ambiguity about where bot code lives
