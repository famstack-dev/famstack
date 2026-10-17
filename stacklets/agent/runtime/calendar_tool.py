"""Agent runtime tool for the family calendar.

Appointments went through `list_edit` on the calendar list, and the
model filled the gaps the list tool leaves open. It put a time into the
text wherever it liked, and asked what was planned next week it read
the list file and named a Friday "Dienstag": a model counts weekdays
instead of computing them. This tool asks for the parts of an
appointment one by one and lets the store do the rest. The time goes in
front of the entry, and what comes back names each entry's weekday and
how far away it is, so the model only reads it out.

The calendar is the list `stack memory list` knows as `calendar`, so
the archivist's dates from letters and the agent's appointments stay in
one place.
"""

from __future__ import annotations

import asyncio
import re

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from turn import current as current_turn

# What `show` covers when the model names no span: the week ahead.
_WEEK = 7

_TIME = re.compile(r"^([01]?\d|2[0-3])[:.]([0-5]\d)$")


@tool_parameters(
    tool_parameters_schema(
        op=StringSchema(
            "add an appointment, show what is planned for the coming days, or "
            "remove an appointment.",
            enum=("add", "show", "remove"),
        ),
        what=StringSchema(
            "For add or remove: the appointment in the family's words, with the "
            "place, such as Treff am Bahnhof.",
            nullable=True,
        ),
        day=StringSchema(
            "For add: the day, YYYY-MM-DD, worked out from when the message was "
            "sent. Ask when nobody said a day.",
            nullable=True,
        ),
        time=StringSchema("For add: the time of day as HH:MM, when one was said.",
                          nullable=True),
        days=IntegerSchema(
            description="For show: how many days from today, 1 for today, 2 for "
                        "today and tomorrow, 7 for the week.",
            nullable=True,
        ),
    )
)
class CalendarTool(Tool):
    """Add, show or remove appointments on the family calendar."""

    _scopes = {"core"}

    @property
    def name(self) -> str:
        return "calendar"

    @property
    def description(self) -> str:
        return (
            "The family calendar: add an appointment, show what is planned for "
            "the coming days, or remove one. Every entry comes back with its "
            "weekday and how far away it is; read those out, never work them out."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self,
        op: str,
        what: str | None = None,
        day: str | None = None,
        time: str | None = None,
        days: int | None = None,
    ) -> str:
        try:
            args = calendar_args(op, what, day, time, days, actor=current_turn().person or "someone")
        except ValueError as e:
            return str(e)
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        # Exit 1 carries an instructive answer (no date, no such entry), as
        # for the list tool. The model reads it and retries once.
        if proc.returncode not in (0, 1):
            return f"Error: calendar failed with exit {proc.returncode}: {err or out}"
        return out or err or "(no answer from the calendar)"


def calendar_args(op: str, what: str | None, day: str | None, time: str | None,
                  days: int | None, *, actor: str) -> "list[str]":
    """The `stack memory list` command for a calendar operation.

    Raises ValueError with what the model has to fix, before anything runs.
    """
    base = ["stack", "memory", "list"]
    if op == "show":
        return [*base, "show", "calendar", "--days", str(days or _WEEK)]
    entry = (what or "").strip()
    if not entry:
        raise ValueError(f"Nothing changed: {op} needs what, the appointment.")
    if op == "remove":
        return [*base, "remove", "calendar", entry, "--as", actor]
    if op != "add":
        raise ValueError(f"Nothing changed: unknown op {op!r}; use add, show or remove.")
    if not (day or "").strip():
        raise ValueError("Nothing added: the appointment needs its day. Ask for it.")
    if time and time.strip():
        m = _TIME.match(time.strip())
        if not m:
            raise ValueError(f"Nothing added: time {time!r} is not HH:MM, such as 10:30.")
        entry = f"{int(m[1]):02}:{m[2]} {entry}"
    return [*base, "add", "calendar", entry, "--due", (day or "").strip(), "--as", actor]


def install() -> None:
    """Append CalendarTool to nanobot discovery, same pattern as list_tool."""
    from nanobot.agent.tools.loader import ToolLoader

    original = ToolLoader.discover

    def discover_with_calendar(self: ToolLoader) -> list[type[Tool]]:
        tools = list(original(self))
        if CalendarTool not in tools:
            tools.append(CalendarTool)
        return tools

    ToolLoader.discover = discover_with_calendar
