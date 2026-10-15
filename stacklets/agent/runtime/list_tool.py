"""Agent runtime tool for item-level list edits.

Free-form page editing loses state on restructures (measured
2026-09-15: a category rewrite unticked a done item). For item
operations the model only names the item; the store matches it and
preserves everything else by construction. One route per intent:
`list_edit` for items, `write_file` for restructures. The old
structured CLI verbs failed because two routes competed for one
intent (docs/design/agent/todo-strike-flaky.md); this tool is the
only documented item route.
"""

from __future__ import annotations

import asyncio

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    StringSchema,
    tool_parameters_schema,
)
from turn import Turn, current as current_turn


class AwaitingYes:
    """Lists whose delete the store refused because of open items, and who was told.

    The person has to say yes, the model cannot: on the rig (2026-10-12)
    the store refused a delete and the model sent `confirmed` itself in
    the same turn. So a yes counts only from the person who asked, in the
    same conversation, in a later turn than the refusal: their message
    after they were told what is open.

        awaiting = AwaitingYes()
        awaiting.refused("partyliste", Turn("homer", "!kitchen", "t1"))
        awaiting.allows("partyliste", Turn("homer", "!kitchen", "t1"))  # False: same turn
        awaiting.allows("partyliste", Turn("bart", "!kitchen", "t2"))   # False: not asked
        awaiting.allows("partyliste", Turn("homer", "!kitchen", "t2"))  # True
    """

    def __init__(self) -> None:
        self._refused_in: dict[tuple[str, str, str], str] = {}

    def refused(self, slug: str, turn: Turn) -> None:
        if turn.id:
            self._refused_in[(turn.conversation, turn.person, slug)] = turn.id

    def allows(self, slug: str, turn: Turn) -> bool:
        refused_in = self._refused_in.get((turn.conversation, turn.person, slug), "")
        return bool(refused_in and turn.id and turn.id != refused_in)

    def done(self, slug: str) -> None:
        """The list is gone, for everyone who was waiting on it."""
        for key in [k for k in self._refused_in if k[2] == slug]:
            del self._refused_in[key]


# What the store says when a list to delete still has open items.
_NEEDS_YES = "add --yes"

_awaiting_yes = AwaitingYes()


@tool_parameters(
    tool_parameters_schema(
        list=StringSchema(
            "The list's name, such as einkaufsliste. `stack memory list "
            "show` names every list with its description. Leave it out "
            "when nobody said which list: add puts a known item where it "
            "was last bought, and tick, untick or remove find the item on "
            "whichever list it is.",
            nullable=True,
        ),
        topic=StringSchema(
            "The room's topic as the brief names it, never a list's name. "
            "For add without a list, only that topic's lists are "
            "considered; for create or set, the list belongs to it.",
            nullable=True,
        ),
        op=StringSchema(
            "Item operation: add, tick, untick, remove, or move to another "
            "list. reset reopens every ticked item. create starts a new "
            "list when none fits, such as a store the family has no list "
            "for yet. set changes a list's title, description, topic, kind or "
            "sections, such as linking it to this room's topic. delete "
            "removes a whole list nobody needs any more.",
            enum=("add", "tick", "untick", "remove", "move", "reset", "create", "set",
                  "delete"),
        ),
        items=ArraySchema(
            StringSchema("One item's text, as it appears on the list."),
            description="The item(s) for this op, one or more, in the "
                        "family's words. Omit for reset.",
            nullable=True,
        ),
        section=StringSchema(
            "For add: one of the list's own sections, such as "
            "Milchprodukte & Eier.",
            nullable=True,
        ),
        people=ArraySchema(
            StringSchema("A family member's name."),
            description="For add: who the item is for, only when someone "
                        "says so.",
            nullable=True,
        ),
        due=StringSchema("For add: the day it is due, YYYY-MM-DD.", nullable=True),
        to=StringSchema("For move: the list the items go to.", nullable=True),
        title=StringSchema("For create or set: the list's name as the family says it, "
                           "such as Baumarkt.", nullable=True),
        # Not `description`: the schema builder takes that keyword for the
        # tool's own description, and a parameter by that name breaks every
        # request the agent makes.
        about=StringSchema("For create or set: what goes on the list, so the next "
                           "item finds it.", nullable=True),
        sections=ArraySchema(
            StringSchema("A section, such as Obst & Gemüse."),
            description="For create or set: a store's sections in the order the family "
                        "walks through it. Leave out for a list without sections.",
            nullable=True,
        ),
        kind=StringSchema(
            "For create or set: checklist for a list used again and again "
            "(packing): ticks stay until reset. calendar for appointments: "
            "every add needs due, the entry leaves after its day. Leave out "
            "for a to-do list.",
            enum=("todo", "checklist", "calendar"),
            nullable=True,
        ),
        confirmed=BooleanSchema(
            description="For delete: true only after the person said yes to "
                        "deleting a list that still has open items.",
            nullable=True,
        ),
    )
)
class ListEditTool(Tool):
    """Change items on a family list."""

    _scopes = {"core"}

    @property
    def name(self) -> str:
        return "list_edit"

    @property
    def description(self) -> str:
        return (
            "Add, tick, untick, remove or move items on a family list, by "
            "name, or start or delete a list. The store matches each item and answers with what "
            "actually changed. Use this for every item change; use "
            "write_file only to restructure a whole list."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self,
        op: str,
        list: str | None = None,
        topic: str | None = None,
        items: "list[str] | None" = None,
        section: str | None = None,
        people: "list[str] | None" = None,
        due: str | None = None,
        to: str | None = None,
        title: str | None = None,
        about: str | None = None,
        sections: "list[str] | None" = None,
        kind: str | None = None,
        confirmed: bool | None = None,
    ) -> str:
        now = current_turn()
        actor = now.person or "someone"

        if op in ("create", "set"):
            args = _details_args(op, list, title, about, topic, sections, kind)
        elif op == "delete":
            slug = (list or "").strip()
            args = ["stack", "memory", "list", "delete", slug]
            # Only a real yes, in a later message than the refusal: nanobot
            # casts "true"/"false" but passes any other string through.
            if confirmed is True and _awaiting_yes.allows(slug, now):
                args.append("--yes")
        else:
            args = _item_args(op, list, items, topic, section, people, due, to)
        args += ["--as", actor]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        # Exit 1 carries an instructive answer (ambiguous item, a section or
        # list that does not exist, with the choices). The model reads it
        # and retries once.
        if proc.returncode not in (0, 1):
            return f"Error: list edit failed with exit {proc.returncode}: {err or out}"
        if op == "delete":
            if _NEEDS_YES in out + err:
                _awaiting_yes.refused(slug, now)
                return _ask_first(out or err)
            if proc.returncode == 0:
                _awaiting_yes.done(slug)
        return out or err or "(no answer from the list store)"


def _ask_first(refusal: str) -> str:
    """The store's refusal, as what the model has to do next.

    The store answers for a person at a terminal ("add --yes"). The model
    read that as a step it could not take and went looking for another
    way to delete the file; what it has to do is ask.
    """
    what = refusal.strip().lstrip("✗ ").split(" Delete it with them")[0]
    return (f"Not deleted: {what} Ask the person whether these go too, naming them. "
            "If they say yes in their next message, call delete again with confirmed=true.")


def _item_args(op, slug, items, topic, section, people, due, to) -> "list[str]":
    """`stack memory list <op> [<list>] <items>... [flags]` for an item operation."""
    args = ["stack", "memory", "list", op]
    if slug and slug.strip():
        args.append(slug.strip())
    if op != "reset":
        args += [i for i in (items or []) if i and i.strip()]
    if op == "add":
        if topic and not slug:
            args += ["--topic", topic]
        if section:
            args += ["--section", section]
        for person in people or []:
            args += ["--for", person]
        if due:
            args += ["--due", due]
    if op == "move" and to:
        args += ["--to", to]
    return args


def _details_args(op, slug, title, about, topic, sections, kind=None) -> "list[str]":
    """`stack memory list create|set [<list>] --title ...`: a list's own details.

    `create` makes a missing slug from the title; `set` changes only what is
    given, so linking a list to this room is `set` with just the topic.
    """
    args = ["stack", "memory", "list", op]
    if slug and slug.strip():
        args.append(slug.strip())
    if title or op == "create":
        args += ["--title", (title or slug or "").strip()]
    if about:
        args += ["--description", about]
    if topic:
        args += ["--topic", topic]
    for section in sections or []:
        args += ["--section", section]
    if kind:
        args += ["--kind", kind]
    return args


def install() -> None:
    """Append ListEditTool to nanobot discovery, same pattern as memory_tool."""
    from nanobot.agent.tools.loader import ToolLoader

    original = ToolLoader.discover

    def discover_with_list_edit(self: ToolLoader) -> list[type[Tool]]:
        tools = list(original(self))
        if ListEditTool not in tools:
            tools.append(ListEditTool)
        return tools

    ToolLoader.discover = discover_with_list_edit
