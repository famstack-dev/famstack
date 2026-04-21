"""Docs CLI commands — one module per subcommand.

Each command module exposes a single async entry point:

    async def run(paperless, classifier, argv: list[str]) -> int

Dispatched from `stacklets/docs/bot/cli_entrypoint.py`. Shared plumbing
(stderr helper, dry-run flag set, DryRunPaperless wrapper, mirror
bootstrap) lives in the `_`-prefixed sibling modules.
"""
