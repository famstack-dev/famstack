"""The driver's command line: one small handler per command.

Each handler reads its arguments, calls one module (chat, terminal, browser,
or the instance itself), and prints the result for a person or, with --json,
for a script.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

import chat
import terminal
from browser import Visit, browse
from instance import Instance
from narration import Narrator, first_line

log = Narrator()


# ── Handlers ─────────────────────────────────────────────────────────────

def as_member(args, instance: Instance) -> None:
    log.when(_telling(args))
    action = {
        "say": lambda: chat.say(instance, args.who, args.room, args.text,
                                thread=args.thread, reply_to=args.reply_to),
        "voice": lambda: chat.speak(instance, args.who, args.room, args.text, args.file),
        "send": lambda: chat.send(instance, args.who, args.room, Path(args.file)),
        "react": lambda: chat.react(instance, args.who, args.room, args.event, args.emoji),
    }[args.action]
    answer = action()
    log.detail(f"sent {answer['event_id']}")
    print(json.dumps(answer) if args.json else answer["event_id"])


def _telling(args) -> str:
    """What a member does, the way the protocol says it."""
    what = {"say": f"says in {args.room}", "voice": f"speaks in {args.room}",
            "send": f"sends {getattr(args, 'file', '')} to {args.room}",
            "react": f"reacts {getattr(args, 'emoji', '')} in {args.room}"}[args.action]
    words = getattr(args, "text", "") if args.action in ("say", "voice") else ""
    return f"{args.who} {what}" + (f": {words!r}" if words else "")


def read(args, instance: Instance) -> None:
    _print_messages(chat.recent(instance, args.room, args.limit), args.json)


def answer(args, instance: Instance) -> None:
    log.then(f"{args.frm} answers in {args.room} to {args.after[:12]}")
    started = time.time()
    messages = chat.answers(instance, args.room, args.frm, args.after, args.timeout)
    if not messages:
        log.detail(f"nothing after {time.time() - started:.1f}s")
    else:
        log.ok(f"after {time.time() - started:.1f}s: {first_line(messages[0]['body'])}")
    _print_messages(messages[:1] if args.first else messages, args.json, whole=True)


def _print_messages(messages: list[dict], as_json: bool, whole: bool = False) -> None:
    print(json.dumps(messages, indent=2) if as_json else chat.listing(messages, whole=whole))


def stack(args, instance: Instance) -> None:
    line = "./stack " + " ".join(shlex.quote(a) for a in args.args)
    log.when(f"the admin runs {line} on {instance.name}")
    result = instance.run(line)
    print(result.stdout + result.stderr, end="")
    (log.ok if result.returncode == 0 else log.detail)(f"exit {result.returncode}")
    sys.exit(result.returncode)


# Work the stack otherwise does on a timer, each with the stack command that
# runs it now and waits until it is done.
CYCLES = {
    "curator": ("the curator regenerates the pages the last filing touched "
                "(on its own after a quiet window)", ("memory", "sync", "--pages")),
    "nightly": ("the curator's nightly sweep: diary, source reconcile, every page "
                "(on its own at 03:30)", ("memory", "nightly")),
}


def cycle(args, instance: Instance) -> None:
    what, command = CYCLES[args.which]
    log.cycle(what)
    started = time.time()
    answer = instance.stack(*command)
    log.ok(f"done after {time.time() - started:.1f}s")
    print(json.dumps(answer) if args.json else f"{args.which} cycle done")


def logs(args, instance: Instance) -> None:
    """A bot's lines: in core's log (the bot-runner), tagged `[name]`; the agent's own."""
    name = args.name.lower().removesuffix("-bot")
    agent = name in ("agent", "stacky")
    command = f"./stack logs {'agent' if agent else 'core'} --tail {args.tail}"
    if not agent:
        command += f" --grep {shlex.quote('[' + name)}"
    result = instance.run(command)
    lines = (result.stdout + result.stderr).splitlines()
    wanted = args.grep.lower() if args.grep else ""
    print("\n".join(line for line in lines if wanted in line.lower()))


def tty(args, instance: Instance) -> None:
    log.when(f"the admin runs {args.line} on {instance.name}, answering its prompts")
    limits = terminal.Limits(quiet=args.quiet, timeout=args.timeout, cols=args.cols, rows=args.rows)
    session = terminal.interact(instance, args.line, terminal.Answers(args.answer),
                                take_defaults=args.take_defaults, limits=limits)
    if args.record:
        terminal.write_recording(session, Path(args.record), limits)
    report = session.report()
    for p in report["prompts"]:
        log.detail(f"asked {p['prompt']!r} -> {p['answer']!r}" + (f" ({p['note']})" if p.get("note") else ""))
    log.ok(f"exit {report['exit']} after {report['seconds']}s")
    if args.json:
        return print(json.dumps(report, indent=2))
    print(report["transcript"])
    for p in report["prompts"]:
        note = f"   ({p['note']})" if p.get("note") else ""
        print(f"  asked: {p['prompt']!r} -> {p['answer']!r}{note}")
    print(f"  exit {report['exit']} after {report['seconds']}s")


def browse_ui(args, instance: Instance) -> None:
    visit = Visit(target=args.target, member=args.as_, password=args.password, room=args.room,
                  shot=args.shot, video=args.video, settle=args.wait, headed=args.headed)
    log.when(f"{args.as_ or 'someone'} opens {args.target}" + (f", room {args.room}" if args.room else ""))
    report = browse(instance, visit)
    log.ok(" ".join(f"{k} {report[k]}" for k in ("url", "shot", "video") if report.get(k)))
    if args.json:
        return print(json.dumps(report, indent=2))
    saved = [f"{kind} {path}" for kind, path in (("screenshot", report["shot"]),
                                                 ("video", report["video"])) if path]
    print("\n".join([report["url"], *saved, report["text"][:1500]]))


HANDLERS = {"as": as_member, "read": read, "answer": answer, "stack": stack,
            "logs": logs, "cycle": cycle, "tty": tty, "browse": browse_ui}


# ── Arguments ────────────────────────────────────────────────────────────

def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="driver", description="Operate a famstack instance from its own surfaces.")
    top.add_argument("--host", default=os.environ.get("DRIVER_HOST"),
                     help="ssh host of the instance's Mac (default: this checkout)")
    top.add_argument("--root", default=os.environ.get("DRIVER_ROOT"),
                     help="checkout on that Mac (default: ~/famstack over ssh)")
    sub = top.add_subparsers(dest="command", required=True)
    json_commands = [*_member_actions(sub), _read(sub), _answer(sub), _cycle(sub),
                     _tty(sub), _browse(sub)]
    _stack(sub)
    _logs(sub)
    for command in json_commands:
        command.add_argument("--json", action="store_true", help="machine-readable output")
    return top


def _member_actions(sub) -> list:
    member = sub.add_parser("as", help="a family member acts in a room")
    member.add_argument("who")
    acts = member.add_subparsers(dest="action", required=True)
    say = acts.add_parser("say", help="send a text message")
    say.add_argument("room")
    say.add_argument("text")
    say.add_argument("--reply-to", help="event id this answers")
    say.add_argument("--thread", help="root event id of the thread to post in")
    voice = acts.add_parser("voice", help="send a voice message")
    voice.add_argument("room")
    voice.add_argument("text", nargs="?", default="", help="spoken through DRIVER_TTS_URL")
    voice.add_argument("--file", help="an existing recording instead")
    send = acts.add_parser("send", help="send a file: photo, PDF, audio")
    send.add_argument("room")
    send.add_argument("file")
    react = acts.add_parser("react", help="react to a message")
    react.add_argument("room")
    react.add_argument("event")
    react.add_argument("emoji")
    return [say, voice, send, react]


def _read(sub):
    read_ = sub.add_parser("read", help="a room's recent messages, with event ids")
    read_.add_argument("room")
    read_.add_argument("--limit", type=int, default=20)
    return read_


def _answer(sub):
    answer_ = sub.add_parser("answer", help="wait for a bot's message in a room")
    answer_.add_argument("room")
    answer_.add_argument("--from", dest="frm", required=True, help="archivist, stacker, stacky, ...")
    answer_.add_argument("--after", required=True, help="the message it answers (its event id)")
    answer_.add_argument("--timeout", type=int, default=180)
    answer_.add_argument("--first", action="store_true", help="only the first answer")
    return answer_


def _cycle(sub):
    cycle_ = sub.add_parser("cycle", help="run timed work now: the curator's pages, the nightly sweep")
    cycle_.add_argument("which", choices=sorted(CYCLES))
    return cycle_


def _tty(sub):
    tty_ = sub.add_parser("tty", help="run an interactive command, answering its prompts")
    tty_.add_argument("line", help='the command, e.g. "./stack" (the installer)')
    tty_.add_argument("--answer", action="append", default=[],
                      help='"Prompt start=value", repeatable; a repeated prompt takes them in order')
    tty_.add_argument("--take-defaults", action="store_true",
                      help="press Enter on a prompt not in --answer, instead of stopping")
    tty_.add_argument("--record", help="write an asciinema recording (.cast) here")
    tty_.add_argument("--timeout", type=int, default=terminal.Limits.timeout)
    tty_.add_argument("--quiet", type=float, default=terminal.Limits.quiet)
    tty_.add_argument("--cols", type=int, default=terminal.Limits.cols)
    tty_.add_argument("--rows", type=int, default=terminal.Limits.rows)
    return tty_


def _browse(sub):
    browse_ = sub.add_parser("browse", help="open a stacklet's web UI in a browser")
    browse_.add_argument("target", help="a stacklet (messages, docs, memory, ...) or a URL")
    browse_.add_argument("--as", dest="as_", help="sign in to Element as this family member")
    browse_.add_argument("--password", help="default: the user name, the installer's default")
    browse_.add_argument("--room", help="open this room (by name) after signing in")
    browse_.add_argument("--shot", help="save a screenshot here")
    browse_.add_argument("--video", help="record a video into this folder")
    browse_.add_argument("--wait", type=float, default=2, help="seconds to let the page settle")
    browse_.add_argument("--headed", action="store_true", help="show the browser window")
    return browse_


def _stack(sub) -> None:
    stack_ = sub.add_parser("stack", help="run ./stack on the instance, as the admin")
    stack_.add_argument("args", nargs=argparse.REMAINDER)


def _logs(sub) -> None:
    logs_ = sub.add_parser("logs", help="a bot's lines from its stacklet's log")
    logs_.add_argument("name", help="archivist, stacker, mail, scribe, or agent")
    logs_.add_argument("--grep")
    logs_.add_argument("--tail", type=int, default=400)


def main() -> None:
    args = parser().parse_args()
    try:
        HANDLERS[args.command](args, Instance(args.host, args.root))
    except SystemExit as stop:
        # A failure is part of the story: into the protocol, then out as before.
        if isinstance(stop.code, str):
            log.failed(stop.code.removeprefix("driver: "))
        raise
