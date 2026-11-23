"""removal: `stack destroy` and `stack uninstall`, the two commands that delete.

    ./stack destroy <id>...            one or more stacklets, data included
    ./stack destroy <id> --yes         without the question
    ./stack uninstall                  the whole stack; asks about the data last
    ./stack uninstall --yes --not ai   without the first question, keeping ai

What it does: `destroy` stops a stacklet and removes its containers,
secrets, setup markers and data folder (`CLI.destroy`, `Stack.destroy`).
`uninstall` tears every stacklet down (`Stack.tear_down`: no secrets, no
data), removes the config and the runtime state, and then asks one
question: "Type 'delete' to remove all data".

Why it exists as its own module: both commands decide what of the
family's data goes, and both follow one rule. The stack deletes nothing
of the family's without a confirmation that names it (AGENTS.md,
non-negotiable 13). So:

- **The uninstall's data waits for its answer.** The data folders and
  the secrets that open them (the databases' passwords in
  `.stack/secrets.toml`) stay until the admin types `delete`. Any other
  answer, a closed terminal and Ctrl+C keep both, and a new install over
  the kept data opens it with the old passwords.
- **`destroy` asks before it deletes leftover data with files in it.** A
  stacklet that is not set up can still have a data folder: a failed
  setup leaves one, and so does a declined uninstall, whose folder holds
  the family's photos. The two look the same, so a folder with files
  is deleted only after `destroy` is typed or with `--yes`; without a
  terminal it refuses.

`cli.py` dispatches to `handle_destroy` and `handle_uninstall`. The
helpers this module shares with the other commands (`CLI`, `print_error`,
the Server room's notices) stay in `cli.py` and are imported inside the
functions, because `cli.py` imports this module.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from . import docker, global_command, run_log, runs
from .prompt import DIM, GREEN, ORANGE, RED, RESET, TEAL

__all__ = ["handle_destroy", "handle_uninstall"]

# The file under `.stack/` that holds every secret; the uninstall keeps it
# until the data question is answered.
SECRETS_FILE = "secrets.toml"


# ── stack destroy ──────────────────────────────────────────────────────

def handle_destroy(stck, args):
    """`stack destroy <id>...`: each stacklet named, dependents first."""
    from .cli import _many_ids, _reverse_dependency_order, print_error

    ids = _many_ids(args)
    if not ids:
        _destroy_one(stck, args)
        return

    unknown = [sid for sid in ids if not stck._find_stacklet(sid)]
    if unknown:
        print_error({"error": f"Unknown stacklet: {', '.join(unknown)}"})
        sys.exit(1)
    # Dependents first, as down does. Each one keeps its own confirmation.
    for sid in _reverse_dependency_order(stck.discover(), set(ids)):
        args.stacklet = sid
        _destroy_one(stck, args)


def _destroy_one(stck, args):
    """Destroy `args.stacklet` after its confirmation; exit 1 on a failure."""
    from .cli import CLI, _notify, _server_room_lines, print_error

    stacklet = stck._find_stacklet(args.stacklet)
    if not stacklet:
        print_error({"error": f"Stacklet '{args.stacklet}' not found"})
        sys.exit(1)

    name = stacklet.get("name", args.stacklet)
    data_path = stck.data / args.stacklet

    # Not installed and no containers: data a failed setup left behind, or
    # data a declined uninstall kept. A first `up` that failed after
    # starting containers leaves those too, holding their names and ports,
    # so that case takes the full path.
    if not stck.is_installed(args.stacklet) and args.stacklet not in docker.all_project_ids():
        _delete_leftover(stck, args, name, data_path)
        return

    if not getattr(args, "yes", False):
        if not sys.stdin.isatty():
            print_error({"error": "Pass --yes to confirm (non-interactive)"})
            sys.exit(1)
        print(f"\n  {ORANGE}⚠  Destroy {name}?{RESET}\n")
        print("  This will permanently remove:")
        print("    · Containers and volumes")
        if data_path.exists():
            print("    · All stored data")
        print("    · Secrets and config\n")
        print("  You may want to back up your data first.\n")
        if not _typed("destroy", "  Type 'destroy' to confirm: "):
            return

    print(f"\n  Destroying {name}...\n", file=sys.stderr)
    result = CLI(stck).destroy(args.stacklet)
    if result.get("ok"):
        print(f"  {GREEN}✓{RESET} {name} destroyed")
        if args.stacklet != "messages":
            _notify(stck, _server_room_lines(stck)["uninstalled"].format(name=name))
    else:
        print_error(result)
        sys.exit(1)


def _delete_leftover(stck, args, name: str, data_path: Path) -> None:
    """Delete the data folder of a stacklet that is not set up.

    A folder that holds files is deleted only once the admin confirmed
    it (`_leftover_confirmed`), because it can be the family's data that
    a declined uninstall kept. A folder with no file in it goes at once.
    """
    from .cli import _waiting, print_error

    if not data_path.exists():
        print(f"  {DIM}{name} is not set up, nothing to destroy.{RESET}")
        return
    if not _leftover_confirmed(args, name, data_path):
        return
    # A first run in progress has made this folder and is filling it.
    with runs.hold(stck.instance_dir, [args.stacklet], waiting=_waiting(stck)):
        # Asked again once the run is ours: a start that ran while this
        # one waited may have set the stacklet up, and then the folder
        # holds its data, not a failed setup's.
        if stck.is_installed(args.stacklet) or args.stacklet in docker.all_project_ids():
            result = {"error": f"{name} was set up while this waited; nothing was deleted",
                      "hints": [f"To remove it: stack destroy {args.stacklet}"]}
            run_log.record_result(stck.instance_dir, args.stacklet, result)
            print_error(result)
            sys.exit(1)
        shutil.rmtree(data_path)
        run_log.record_result(stck.instance_dir, args.stacklet,
                              {"ok": True, "stacklet": args.stacklet})
    print(f"  {GREEN}✓{RESET} Cleaned up leftover data for {name}")


def _leftover_confirmed(args, name: str, data_path: Path) -> bool:
    """Whether the leftover `data_path` may go: it holds no file, `--yes`
    was given, or the admin typed `destroy` at the question, which names
    the folder and its file count. Without a terminal and without `--yes`
    it refuses: exit 1, nothing deleted."""
    from .cli import _short_path, print_error

    files = sum(1 for path in data_path.rglob("*") if path.is_file())
    if not files or getattr(args, "yes", False):
        return True
    where = _short_path(data_path)
    count = f"{files} file{'' if files == 1 else 's'}"
    if not sys.stdin.isatty():
        print_error({"error": f"{name} is not set up, but {where} holds {count}. Nothing was deleted.",
                     "hints": [f"To delete them: stack destroy {args.stacklet} --yes"]})
        sys.exit(1)
    print(f"\n  {ORANGE}⚠  {name} is not set up, but its data folder holds {count}:{RESET}")
    print(f"  {where}")
    print("  This can be data an uninstall kept. Deleting it cannot be undone.\n")
    return _typed("destroy", "  Type 'destroy' to delete it: ")


# ── stack uninstall ────────────────────────────────────────────────────

def handle_uninstall(stck, args):
    """`stack uninstall [--yes] [--not <id>...]`: the whole stack, after
    the confirmation; the data after a second one."""
    from .cli import print_error

    # `--not X [Y ...]` excludes one or more stacklets from the uninstall.
    # The state of excluded stacklets (container, setup marker, data dir)
    # is preserved across this call so expensive state, most notably the
    # ai stacklet's downloaded model weights, can survive a teardown
    # without a multi-gigabyte re-download on the next install.
    exclude = list(getattr(args, "exclude", None) or [])

    # 'stack uninstall chatai' is a common mistake: catch it early. The
    # values consumed by `--not` are intentional and must be filtered out
    # before we treat a bare token as a "did you mean destroy?" signal.
    remaining = [a for a in sys.argv[2:] if not a.startswith("-")]
    remaining = [a for a in remaining if a not in exclude]
    if remaining:
        sid = remaining[0]
        print(f"\n  {RED}✗{RESET}  'uninstall' removes the entire stack, not a single stacklet.")
        print(f"  To remove {sid}, use: {TEAL}stack destroy {sid}{RESET}\n")
        sys.exit(1)

    name = stck.product_name()
    config_exists = (stck.instance_dir / "stack.toml").exists()
    state_exists = (stck.instance_dir / ".stack").exists()
    has_containers = bool(docker.all_project_ids())
    if not config_exists and not state_exists and not stck.data.exists() and not has_containers:
        print(f"\n  {DIM}{name} is not set up. Run 'stack install' to get started.{RESET}\n")
        return

    if not sys.stdin.isatty() and not getattr(args, "yes", False):
        print_error({"error": "Pass --yes to confirm (non-interactive)"})
        sys.exit(1)

    if not getattr(args, "yes", False):
        print(f"\n  {ORANGE}⚠  Uninstall {name}?{RESET}\n")
        print("  This will:")
        print("    • Stop every service and remove its containers")
        print("    • Remove stack.toml, users.toml and the runtime state (.stack/)")
        print("    • Then ask whether to delete all data and the secrets that open it\n")
        if not _typed("uninstall", "  Type 'uninstall' to confirm: "):
            return

    # Every stacklet's lock, the excluded ones too, for the whole
    # uninstall and without a run log of its own (`runs.reserve`): a run
    # of any stacklet (a preparation outside the queue included) refuses
    # it before anything is deleted, and the uninstall deletes stack.toml
    # and .stack/, which an excluded stacklet's run reads as well.
    with runs.reserve(stck.instance_dir, [st["id"] for st in stck.discover()]):
        _uninstall(stck, exclude, name)


def _uninstall(stck, exclude: list[str], name: str) -> None:
    """`handle_uninstall` once confirmed, holding every stacklet's lock.

    Tear down, remove the config and the runtime state, then the data
    question. Until it is answered with `delete`, every data folder and
    `.stack/secrets.toml` stay: the secrets hold the passwords of the
    databases in the data folders, and kept data without them cannot be
    opened by a new install.
    """
    from .cli import CLI, print_error

    # An excluded stacklet keeps its container, data dir, setup marker and
    # secrets: everything needed to resume on the next `stack up`.
    removed = [st for st in stck.discover() if st["id"] not in exclude]
    failed = _tear_down_every(stck, CLI(stck), removed, exclude)
    if failed:
        # The config, `.stack/` with the secrets and the data folder stay:
        # a stacklet whose service still runs needs them.
        print_error({"error": f"Not uninstalled, a service still runs: {', '.join(failed)}. "
                              "Stop it, then run 'stack uninstall' again."})
        sys.exit(1)

    _remove_config(stck)
    _remove_state(stck, exclude)

    # The installer puts the global command in place for the checkout's
    # own instance, so only uninstalling that instance takes it away. A
    # STACK_DIR instance (a test run, a sandbox) leaves it for the others.
    if stck.instance_dir == stck.root and (bin_dir := global_command.bin_dir()):
        if global_command.uninstall(stck.root, bin_dir):
            print(f"  {GREEN}✓{RESET} Removed the {global_command.NAME} command")

    if _data_deleted(stck, exclude):
        _remove_secrets(stck, [st["id"] for st in removed], exclude)

    print(f"\n  {GREEN}✓{RESET} {name} uninstalled")
    print(f"  {DIM}Run 'stack install' to start fresh.{RESET}\n")


def _tear_down_every(stck, cli, stacklets: list[dict], exclude: list[str]) -> list[str]:
    """Tear down each of `stacklets` that has state: data, a setup marker
    or a container. `exclude` is only named, as preserved. Returns the ids
    whose teardown failed, after trying every one; the others are torn
    down all the same.

    The data folders and the secrets stay (`Stack.tear_down`): `_uninstall`
    deletes them only after the admin typed `delete`."""
    failed: list[str] = []
    for sid in exclude:
        print(f"  {DIM}Preserving {sid} (excluded via --not){RESET}", file=sys.stderr)
    container_ids = docker.all_project_ids()
    for s in stacklets:
        sid = s["id"]
        has_data = (stck.data / sid).exists()
        has_marker = s.get("enabled")
        has_container = sid in container_ids
        if not has_data and not has_marker and not has_container:
            continue
        print(f"  {ORANGE}Uninstalling {s['name']}...{RESET}", file=sys.stderr)
        result = cli.tear_down(sid)
        if result.get("ok"):
            print(f"  {GREEN}✓{RESET} {s['name']} uninstalled")
        else:
            print(f"  {RED}✗{RESET} {s['name']} not uninstalled: "
                  f"{result.get('error', 'unknown error')}")
            failed.append(sid)
    return failed


def _remove_config(stck) -> None:
    """Remove the instance's stack.toml and users.toml.

    The instance's, never the checkout's: under STACK_DIR the checkout
    may hold another instance's config."""
    for filename in ("stack.toml", "users.toml"):
        path = stck.instance_dir / filename
        if path.exists():
            path.unlink()
            print(f"  {GREEN}✓{RESET} Removed {filename}")


def _remove_state(stck, exclude: list[str]) -> None:
    """Remove the instance's `.stack/` but the secrets file and what the
    excluded stacklets own there (`_belongs_to_stacklet`)."""
    state_dir = stck.instance_dir / ".stack"
    if not state_dir.exists():
        return
    for item in state_dir.iterdir():
        if item.name == SECRETS_FILE or _belongs_to_stacklet(item.name, exclude):
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    kept = f" (kept: {', '.join(exclude)})" if exclude else ""
    print(f"  {GREEN}✓{RESET} Cleaned .stack/{kept}")


def _remove_secrets(stck, stacklet_ids: list[str], exclude: list[str]) -> None:
    """Remove the secrets of the deleted data. Without exclusions the
    whole `.stack/` goes; with them, only the secrets of `stacklet_ids`,
    and the shared ones (the admin's and the people's passwords) stay
    with the accounts the excluded stacklets keep."""
    state_dir = stck.instance_dir / ".stack"
    if not exclude:
        if state_dir.exists():
            shutil.rmtree(state_dir)
            print(f"  {GREEN}✓{RESET} Removed .stack/")
        return
    for sid in stacklet_ids:
        stck.secrets.clear(sid)
    print(f"  {GREEN}✓{RESET} Secrets cleared (kept: {', '.join(exclude)})")


def _belongs_to_stacklet(entry_name: str, sids: list[str]) -> bool:
    """True when a .stack/ entry belongs to one of the named stacklets.

    Setup markers look like `<sid>.setup-done`; any future per-stacklet
    state under .stack/ is expected to use the same `<sid>.` prefix so
    it gets preserved automatically when the user excludes that sid.
    """
    return any(entry_name.startswith(f"{sid}.") for sid in sids)


def _data_deleted(stck, exclude: list[str]) -> bool:
    """Ask the data question and delete on `delete`. True when the data is
    gone or there was none, so its secrets may go too; False when it is
    kept: any other answer, a closed terminal or Ctrl+C."""
    from .cli import _short_path

    if not stck.data.exists():
        return True
    display = _short_path(stck.data)
    print(f"\n  {RED}Data directory: {display}{RESET}")
    print(f"  {RED}This contains all your photos, messages, documents, etc.{RESET}")
    # Extension stacklets are source code, not data, and they live in
    # the data dir by default. Say so before the wipe, not after.
    for d in stck.extension_dirs:
        if d.exists() and d.is_relative_to(stck.data):
            print(f"  {RED}It also contains your extension stacklets ({_short_path(d)}).{RESET}")
    print(f"  {RED}This action is irreversible.{RESET}\n")
    if not _typed("delete", "  Type 'delete' to remove all data: ", aborted=""):
        print(f"  {DIM}Data kept at {display}{RESET}")
        return False
    if exclude:
        for item in stck.data.iterdir():
            if item.name in exclude:
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        print(f"  {GREEN}✓{RESET} Removed {display} (kept: {', '.join(exclude)})")
    else:
        shutil.rmtree(stck.data)
        print(f"  {GREEN}✓{RESET} Removed {display}")
    return True


# ── The question ───────────────────────────────────────────────────────

def _typed(word: str, question: str, aborted: str = "Aborted") -> bool:
    """Whether the person typed `word` at `question`. A closed terminal and
    Ctrl+C are a no; any other answer prints `aborted` (nothing when empty)."""
    try:
        answer = input(question).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if answer != word and aborted:
        print(f"  {DIM}{aborted}{RESET}")
    return answer == word
