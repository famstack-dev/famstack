"""stack backup sync — run a backup now.

Discovers ``[[backup.archive]]`` sources from every enabled stacklet
(append-only stores) and routes them through every configured
``[backup.targets.*]`` engine. Each engine writes a structured result
to ``$BACKUP_DATA_DIR/logs/history.jsonl``; the orchestrator reads
that file, formats a per-target summary, and posts it to the
``#famstack`` room as ``stacker-bot``.

Exit code is 0 only if every target succeeded. A missing notification
(Matrix unavailable, room not yet created) is soft-failed — the sync
itself is recorded regardless.

Usage:
  stack backup sync                  full sync of all sources to all targets
  stack backup sync --dry-run        preview only — no writes, no mounts
  stack backup sync --no-eject       keep the disk mounted after sync
  stack backup sync --verbose        rsync file-level output
  stack backup sync --verify         compare file counts source vs vault
"""

HELP = "Run a backup now (all sources to all configured targets)"

import argparse
import sys
from pathlib import Path
from typing import Optional

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from _orchestrator import (
    discover_archive_sources,
    format_notification,
    get_targets,
    invoke_engine,
    read_latest_run,
)

# MatrixClient lives in the messages stacklet — the canonical Matrix
# interface for any CLI plugin that needs to post. Cross-stacklet import
# here is fine because messaging is a hard prerequisite for sending a
# notification anyway: if messages isn't around, neither is MatrixClient.
_messages_cli = _here.parent.parent / "messages" / "cli"
sys.path.insert(0, str(_messages_cli))


def _parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="stack backup sync", description=HELP)
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would be synced (no changes).")
    parser.add_argument("--no-eject", action="store_true",
                        help="Keep vault mounted after sync.")
    parser.add_argument("--verbose", action="store_true",
                        help="Show rsync file-level details.")
    parser.add_argument("--verify", action="store_true",
                        help="Compare file counts source vs vault after sync.")
    return parser.parse_args(argv)


def _resolve_backup_data_dir(config: dict) -> Path:
    """Render the backup stacklet's own ``BACKUP_DATA_DIR`` from its
    manifest. The default is ``{data_dir}/backup`` — we don't import the
    full template renderer here; substituting one variable is enough."""
    manifest = config.get("manifest", {})
    template = manifest.get("env", {}).get("defaults", {}).get(
        "BACKUP_DATA_DIR", "{data_dir}/backup"
    )
    data_dir = config.get("data_dir", "")
    try:
        return Path(template.format(data_dir=data_dir))
    except (KeyError, IndexError):
        # Fall back to the well-known default.
        return Path(data_dir) / "backup"


def _post_notification(plain: str, html: str, config: dict) -> Optional[str]:
    """Post a notification to ``#famstack`` as stacker-bot.

    Returns an error string on failure, ``None`` on success. The
    orchestrator soft-fails on notification problems — failing to
    deliver the message must not mask the actual sync outcome.
    """
    try:
        from _matrix import MatrixClient  # type: ignore
    except ImportError:
        return "MatrixClient not importable (messages stacklet missing?)"

    stack_cfg = config.get("stack", {})
    secrets = config.get("secrets", {})
    server_name = stack_cfg.get("messages", {}).get("server_name", "home")

    bot_pass = secrets.get("core__STACKER_BOT_PASSWORD", "")
    if not bot_pass:
        return "stacker-bot password not in secrets — is core set up?"

    instance_dir = config.get("instance_dir", config.get("repo_root", "."))
    # Synapse host port: the messages stacklet binds 42031. We read it
    # from the messages manifest if available; otherwise use the
    # well-known default. The send.py plugin uses the same constant.
    synapse_port = _read_synapse_port(Path(config.get("repo_root", ".")))
    base_url = f"http://localhost:{synapse_port}"

    client = MatrixClient(base_url, server_name, instance_dir)
    if not client.login("stacker-bot", bot_pass):
        return "stacker-bot login failed — run 'stack messages setup' to re-create the account"

    ok, detail = client.send("famstack", plain, html=html)
    if not ok:
        return f"Matrix send failed: {detail}"
    return None


def _read_synapse_port(repo_root: Path) -> int:
    """Pull synapse port from the messages stacklet manifest, falling
    back to the standard 42031."""
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        from stack._vendor import tomli as tomllib  # type: ignore
    manifest_path = repo_root / "stacklets" / "messages" / "stacklet.toml"
    try:
        with manifest_path.open("rb") as f:
            manifest = tomllib.load(f)
        return int(manifest.get("ports", {}).get("synapse", 42031))
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return 42031


def _engine_script(repo_root: Path, engine_name: str) -> Path:
    """Resolve the engine script path. Today only ``external-disk``
    exists; future engines slot in beside it under ``engines/``."""
    return repo_root / "stacklets" / "backup" / "engines" / engine_name / "sync.py"


def _print_summary(target_name: str, result: Optional[dict]) -> None:
    """Echo a one-line summary to stdout for the user running interactively."""
    if result is None:
        print(f"  [{target_name}] engine crashed before writing a result")
        return
    outcome = "ok" if result.get("success") else "FAILED"
    new_total = sum(s.get("new_files", 0) for s in result.get("sources", []))
    duration = int(result.get("duration_seconds", 0))
    print(
        f"  [{target_name}] {outcome} — {new_total} new files, "
        f"{duration}s, vault_state={result.get('vault_state', '?')}"
    )


def run(args, stacklet, config):
    """Entry point invoked by the framework via ``stack backup sync``.

    ``args`` is the list of unparsed arguments after the subcommand.
    ``stacklet`` is the backup stacklet's discovered record.
    ``config`` is the framework-supplied context dict (see Stack.run_cli_command).
    """
    parsed = _parse_args(args or [])

    repo_root = Path(config.get("repo_root", "."))
    instance_dir = Path(config.get("instance_dir", repo_root))
    data_dir = Path(config.get("data_dir", "."))
    backup_data_dir = _resolve_backup_data_dir(config)

    targets = get_targets(config.get("stack", {}))
    if not targets:
        return {
            "error": "No backup targets configured. Add a [backup.targets.<name>] "
                     "block to stack.toml (see stack.example.toml)."
        }

    sources = discover_archive_sources(repo_root, instance_dir, data_dir)
    if not sources:
        return {
            "error": "No backup sources discovered. No enabled stacklet declares "
                     "[[backup.archive]] in its manifest."
        }

    # Run each target sequentially. Each engine call appends its run to
    # history.jsonl; we read the latest entry (this target's) right after
    # it returns, before the next target appends its own.
    target_results: list = []
    any_failed = False

    for target in targets:
        engine_script = _engine_script(repo_root, target.engine)
        if not engine_script.exists():
            print(f"  [{target.name}] engine '{target.engine}' not found at {engine_script}",
                  file=sys.stderr)
            target_results.append((target, None))
            any_failed = True
            continue

        invoke_engine(
            engine_script, backup_data_dir,
            target, sources, parsed,
        )
        result = read_latest_run(backup_data_dir)
        target_results.append((target, result))

        _print_summary(target.name, result)
        if result is None or not result.get("success"):
            any_failed = True

        # Post notification per target. Soft-fail: messaging problems
        # are reported but don't mask the sync outcome.
        if result is not None:
            plain, html = format_notification(target.name, result)
            notify_error = _post_notification(plain, html, config)
            if notify_error:
                print(f"  [{target.name}] notification skipped: {notify_error}",
                      file=sys.stderr)

    if any_failed:
        return {"error": "One or more targets failed; see history.jsonl for details"}
    return {
        "ok": True,
        "targets": [t.name for t, _ in target_results],
        "sources": len(sources),
    }
