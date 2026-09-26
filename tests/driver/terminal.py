"""The admin's terminal: an interactive command, answered prompt by prompt.

The wizard and the setup hooks ask with `› <prompt> [default]` or
`? <question> [Y/n]`. A prompt is output that stops, so the last line is read
once nothing has arrived for a moment. An answer given with `--answer` is
matched on how the prompt starts. A prompt without an answer stops the
command and is named, because Enter on an unexpected "Switch to the local
engine? [Y/n]" changes the instance; `take_defaults` presses Enter instead.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from instance import Instance

PROMPT = re.compile(r"^\s*[›?]\s+(?P<text>.+?)\s*(?:\[[^\]]*\])?\s*$")
ESCAPES = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-9A-Za-z]|[@-Z\\-_])")


def plain(text: str) -> str:
    """Terminal output as text: escape sequences gone, redraws on their own lines."""
    return ESCAPES.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def prompt_in(output: str) -> str | None:
    """The question the output stops at, if it stops at one."""
    last = re.split(r"[\r\n]", ESCAPES.sub("", output))[-1]
    match = PROMPT.match(last)
    return match.group("text") if match else None


class Answers:
    """The answers the admin would type, looked up by the question asked.

    Built from `--answer "Prompt start=value"` pairs. A prompt is matched on
    how it starts, and a prompt that repeats (the wizard's "Name (leave empty
    to continue)") takes its answers in the order given. No answer means
    None, which is where `interact` stops.

        answers = Answers(["Family name=Simpson", "Name (leave empty=Marge", "Name (leave empty="])
        answers.for_prompt("Family name")                      # "Simpson"
        answers.for_prompt("Name (leave empty to continue)")   # "Marge", then ""
    """

    def __init__(self, pairs: list[str]):
        self._queues: dict[str, list[str]] = {}
        for pair in pairs:
            prompt, _, value = pair.partition("=")
            self._queues.setdefault(prompt, []).append(value)

    def for_prompt(self, question: str) -> str | None:
        queue = next((q for start, q in self._queues.items()
                      if question.startswith(start) and q), None)
        return queue.pop(0) if queue else None


@dataclass
class Session:
    """What happened while a command ran: output, the prompts asked, a recording.

    `interact` fills it as output arrives; `report()` is what a person or a
    script gets back, and `cast` becomes an asciinema recording.

        session = interact(rig, "./stack", answers, take_defaults=False, limits=Limits())
        session.report()["prompts"]   # [{"prompt": "Family name", "answer": "Simpson", ...}]
    """

    started: float = field(default_factory=time.time)
    output: str = ""
    prompts: list[dict] = field(default_factory=list)
    cast: list[str] = field(default_factory=list)
    exit: int | None = None

    def heard(self, chunk: str) -> None:
        self.output += chunk
        self.cast.append(json.dumps([round(time.time() - self.started, 3), "o", chunk]))

    def report(self) -> dict:
        return {"exit": self.exit, "seconds": round(time.time() - self.started),
                "prompts": self.prompts, "transcript": plain(self.output)}


@dataclass
class Limits:
    """How long `interact` waits, and how big the terminal it draws in is.

        Limits(timeout=120)   # give up on a command after two minutes
    """

    quiet: float = 1.5       # seconds of silence that end a prompt
    timeout: int = 2400      # seconds before giving up; the installer pulls images
    cols: int = 110
    rows: int = 32


def interact(instance: Instance, command: str, answers: Answers, *,
             take_defaults: bool, limits: Limits) -> Session:
    import pexpect

    argv = instance.argv(command, tty=True)
    child = pexpect.spawn(argv[0], argv[1:], encoding="utf-8", timeout=None,
                          dimensions=(limits.rows, limits.cols))
    session, pending = Session(), ""
    while _running(child, session, limits):
        try:
            chunk = child.read_nonblocking(65536, timeout=limits.quiet)
        except pexpect.TIMEOUT:
            pending = _respond(child, session, pending, answers, take_defaults)
            continue
        except pexpect.EOF:
            break
        session.heard(chunk)
        pending += chunk
    child.close()
    session.exit = child.exitstatus
    return session


def _running(child, session: Session, limits: Limits) -> bool:
    if not child.isalive() and child.eof():
        return False
    if time.time() - session.started <= limits.timeout:
        return True
    session.prompts.append({"prompt": None, "answer": None, "note": f"stopped after {limits.timeout}s"})
    child.terminate(force=True)
    return False


def _respond(child, session: Session, pending: str, answers: Answers, take_defaults: bool) -> str:
    """Answer the prompt the output stopped at; what is still unanswered."""
    question = prompt_in(pending)
    if question is None:
        return pending
    answer = answers.for_prompt(question)
    if answer is None and not take_defaults:
        session.prompts.append({"prompt": question, "answer": None, "note": "not in --answer: stopped here"})
        child.terminate(force=True)
        return ""
    session.prompts.append({"prompt": question, "answer": answer or "",
                            "note": "" if answer is not None else "took the default"})
    child.send((answer or "") + "\r")
    return ""


def write_recording(session: Session, path: Path, limits: Limits) -> None:
    header = json.dumps({"version": 2, "width": limits.cols, "height": limits.rows,
                         "timestamp": int(session.started)})
    path.write_text("\n".join([header, *session.cast]) + "\n")
