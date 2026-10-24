"""stack core schedule — run a stack command at a time.

    stack core schedule add dates "daily 07:00" -- memory list show dates
    stack core schedule add tea "in 10m" -- ai say "The tea is ready"
    stack core schedule list
    stack core schedule run dates
    stack core schedule remove dates
    stack core schedule log [dates]

`when` is `daily HH:MM`, `weekly <day> HH:MM`, `at YYYY-MM-DD HH:MM` or
`in <duration>` (`10m`, `2h`), in household time (`[core] timezone`). The
command is a stacklet command, as typed after `stack`; framework commands
(`up`, `down`, `destroy`) are refused. Options for `add`, before the
command: `--grace 15m` (how late a missed time still runs), `--timeout 5m`,
`--title "<what it does>"`, `--by <person>` (who created it, `admin` by
default), `--for <person>` (who it is for). Adding a name that exists
replaces it. `list --by <person>` shows what a person created, `list --for
<person>` what is for them, both together what one set up for the other.

The scheduler service core runs (`stacklets/core/scheduler.py`) checks
every 30 seconds and survives restarts: a time that passed while it was
down runs once within its grace, later it is logged as missed.
"""

HELP = "Run a stack command at a time: add, list, run, remove, log"

import re
import shlex

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stack.plugins import stacklet_commands
from stack.schedule import (GRACE, TIMEOUT, Entry, Schedule, as_text, parse_duration,
                            parse_when, refusal)
from stack.stack import Stack


USAGE = ('stack core schedule add <name> "<when>" [--grace 15m] [--timeout 5m] '
         '[--title "..."] [--by <person>] [--for <person>] -- <command ...> | '
         'list [--by <person>] [--for <person>] | run <name> | remove <name> | log [<name>]')
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_OPTIONS = {"--grace", "--timeout", "--title", "--by", "--for"}


def run(args, stacklet, config):
    args = list(args)
    action, rest = (args[0], args[1:]) if args else ("list", [])
    context = _Context(config)
    actions = {"add": _add, "list": _list, "run": _run, "remove": _remove, "log": _log}
    if action not in actions:
        return {"error": f"Unknown action '{action}'. Usage: {USAGE}"}
    return actions[action](context, rest)


class _Context:
    """What every action needs: the instance's schedule, time zone and commands."""

    def __init__(self, config):
        self.config = config
        self.schedule = Schedule(Path(config["data_dir"]) / "core" / "schedule")
        core = config.get("stack", {}).get("core", {})
        try:
            self.tz = ZoneInfo(core.get("timezone") or "UTC")
        except (KeyError, ValueError):
            self.tz = ZoneInfo("UTC")
        self.now = datetime.now(self.tz)

    def commands(self) -> dict:
        return stacklet_commands(Stack(Path(self.config["repo_root"]), Path(self.config["data_dir"]),
                                       instance_dir=Path(self.config["instance_dir"])))


# ── Actions ──────────────────────────────────────────────────────────────

def _add(ctx: _Context, args: list[str]) -> dict:
    if len(args) < 3:
        return {"error": f"add needs a name, a time and a command. Usage: {USAGE}"}
    name, when_text, rest = args[0], args[1], args[2:]
    if not _NAME.match(name):
        return {"error": f"'{name}' is no name for an entry: lowercase letters, digits, . _ -"}
    options, command = _split(rest)
    try:
        when = parse_when(when_text, ctx.tz, ctx.now)
        grace = parse_duration(options.get("--grace", "")) if "--grace" in options else GRACE
        timeout = parse_duration(options["--timeout"]) if "--timeout" in options else TIMEOUT
    except ValueError as e:
        return {"error": str(e)}
    if when.once and when.moment and when.moment <= ctx.now:
        return {"error": f"{when.moment:%Y-%m-%d %H:%M} has passed already."}
    if not command:
        return {"error": f"add needs the command to run. Usage: {USAGE}"}
    if why := refusal(command, ctx.commands()):
        return {"error": why}
    entry = Entry(name=name, when=as_text(when), run=shlex.join(command),
                  added=ctx.now.isoformat(), grace=grace, timeout=timeout,
                  title=options.get("--title", ""), by=options.get("--by", "admin"),
                  person=options.get("--for", ""))
    entries = ctx.schedule.entries()
    replaced = name in entries
    ctx.schedule.save({**entries, name: entry})
    _forget(ctx, name)
    due = when.next_after(ctx.now, ctx.tz)
    print(f"{'Replaced' if replaced else 'Added'} {name}: {entry.what}, {entry.when}"
          + (f", next {due:%a %Y-%m-%d %H:%M}" if due else ""))
    return {"ok": True, "name": name, "when": entry.when, "run": entry.run,
            "next": due.isoformat() if due else None, "replaced": replaced}


def _list(ctx: _Context, args: list[str]) -> dict:
    options, _ = _split(args, ("--by", "--for"))
    entries, last_due = ctx.schedule.entries(), ctx.schedule.last_due()
    listed = []
    for name, entry in sorted(entries.items()):
        if not entry.matches(by=options.get("--by", ""), for_=options.get("--for", "")):
            continue
        anchor = max(filter(None, [last_due.get(name), ctx.now]))
        try:
            due = entry.parsed(ctx.tz).next_after(anchor, ctx.tz)
        except ValueError:
            due = None
        last = (ctx.schedule.runs(name, limit=1) or [{}])[-1]
        listed.append({"name": name, "title": entry.what, "by": entry.by, "for": entry.person,
                       "when": entry.when, "run": entry.run,
                       "next": due.isoformat() if due else None,
                       "last": last.get("status"), "last_due": last.get("due")})
    beat = ctx.schedule.heartbeat()
    if beat:
        print(f"The scheduler last checked {int((ctx.now - beat).total_seconds())} s ago.")
    else:
        print("The scheduler has not checked yet. It runs inside core: stack up core")
    if not listed:
        print("Nothing scheduled. Add an entry with: stack core schedule add ...")
    for e in listed:
        nxt = datetime.fromisoformat(e["next"]).strftime("%a %d %b %H:%M") if e["next"] else "-"
        who = e["by"] + (f" for {e['for']}" if e["for"] else "")
        print(f"{e['name']:<18} {e['title'][:34]:<34} {who:<20} {e['when']:<18} "
              f"next {nxt:<16} last {e['last'] or '-'}")
    return {"entries": listed, "heartbeat": beat.isoformat() if beat else None}


def _run(ctx: _Context, args: list[str]) -> dict:
    entry = ctx.schedule.entries().get(args[0]) if args else None
    if entry is None:
        return {"error": f"No entry '{args[0] if args else ''}'. See: stack core schedule list"}
    record = _scheduler(ctx).run_now(entry, ctx.now)
    print(f"{entry.name}: {record['status']}" + (f"\n{record['output']}" if record["output"] else ""))
    if record["status"] != "ok":
        return {"error": f"{entry.name} {record['status']}: {record['output']}"}
    return {"ok": True, **record}


def _remove(ctx: _Context, args: list[str]) -> dict:
    entries = ctx.schedule.entries()
    if not args or args[0] not in entries:
        return {"error": f"No entry '{args[0] if args else ''}'. See: stack core schedule list"}
    del entries[args[0]]
    ctx.schedule.save(entries)
    _forget(ctx, args[0])
    print(f"Removed {args[0]}")
    return {"ok": True, "removed": args[0]}


def _log(ctx: _Context, args: list[str]) -> dict:
    limit = 20
    if "--limit" in args:
        i = args.index("--limit")
        limit = int(args[i + 1]) if i + 1 < len(args) and args[i + 1].isdigit() else limit
        args = args[:i] + args[i + 2:]
    runs = ctx.schedule.runs(args[0] if args else "", limit=limit)
    for r in runs:
        first = (r.get("output") or "").splitlines()[:1]
        print(f"{r['due'][:16]}  {r['name']:<20} {r['status']:<8} {first[0][:60] if first else ''}")
    if not runs:
        print("No runs yet.")
    return {"runs": runs}


# ── Helpers ──────────────────────────────────────────────────────────────

def _split(words: list[str], known=_OPTIONS) -> tuple[dict[str, str], list[str]]:
    """The options before the command, and the command: after `--`, or from
    the first word that is not one of the options."""
    options: dict[str, str] = {}
    i = 0
    while i < len(words):
        if words[i] == "--":
            return options, words[i + 1:]
        if words[i] in known and i + 1 < len(words):
            options[words[i]] = words[i + 1]
            i += 2
            continue
        break
    return options, words[i:]


def _forget(ctx: _Context, name: str) -> None:
    """A new or removed entry starts from scratch: no due time carried over."""
    last = ctx.schedule.last_due()
    if last.pop(name, None) is not None:
        ctx.schedule.save_last_due(last)


def _scheduler(ctx: _Context):
    """The service's own runner, so a run by hand is checked and logged the same way."""
    from ..scheduler import Scheduler
    return Scheduler(Path(ctx.config["instance_dir"]), Path(ctx.config["data_dir"]))
