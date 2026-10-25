"""stack memory card edit <card id> --set f=v --add f=v --remove f[=v] --as <person>

Changes a card's fields through the card write endpoint, the one way any
surface changes a card (`memory/card_edit.py`). This command is a client
(`memory_api.MemoryAPI`): it reads the card and its version through core's
door on this Mac, builds the merge patch, sends it, and prints what the
endpoint answered. It holds no schema and makes no commit; the endpoint
refuses what does not fit and says which field and what it may hold.

    stack memory card edit person-bart --set "description=Rides a skateboard." --as marge
    stack memory card edit note-7f3a9c21e4 --add tags=watercolour --remove tags=paint --as marge
    stack memory card edit note-7f3a9c21e4 --remove description --as homer

`--set f=v` gives a field one value (a list field becomes that one item),
`--add f=v` and `--remove f=v` add an item to a list or take it out, and
`--remove f` removes the field. A list is sent whole, as the card holds it
with the change applied, so a concurrent edit is refused (412), not merged.

Exit codes: 0 changed or nothing to change, 1 refused, 2 usage.
"""

from __future__ import annotations

import sys

from ..memory_api import CuratorDown, MemoryAPI, Refused

_USAGE = ("usage: stack memory card edit <card id> --set f=v | --add f=v | --remove f[=v] ... "
          "--as <person>\n  e.g. stack memory card edit person-bart --add aliases=Bartman --as marge")


def run(args: list[str], config: dict) -> dict:
    parsed = _parse(args)
    if isinstance(parsed, str):
        print(parsed, file=sys.stderr)
        sys.exit(2)
    card_id, actor, edits = parsed
    memory = MemoryAPI.from_host(config)
    try:
        card, version = memory.card(card_id)
        patch = patch_for(card["fields"], edits)
        if not patch:
            print(f"Nothing to change on {card_id}.")
            return {"card": card, "changed": {}}
        answer = memory.edit(card_id, patch, version=version, by=actor)
    except Refused as e:
        return _refused(e)
    except CuratorDown as e:
        return {"error": str(e)}
    title = answer["fields"].get("title") or card_id
    print(f"Changed {title}: " + "; ".join(f"{k}: {_shown(v)}" for k, v in patch.items()))
    return {"card": answer, "changed": patch}


def patch_for(fields: dict, edits: list[tuple[str, str, str | None]]) -> dict:
    """The merge patch (RFC 7396) that applies `edits` to `fields`, lists whole.

    >>> patch_for({"tags": ["a"], "kind": "todo"},
    ...           [("add", "tags", "b"), ("remove", "kind", None), ("set", "title", "x")])
    {'tags': ['a', 'b'], 'kind': None, 'title': 'x'}
    """
    after = dict(fields)
    for op, name, value in edits:
        current = after.get(name)
        if op == "set":
            after[name] = [value] if isinstance(current, list) else value
        elif op == "add":
            items = current if isinstance(current, list) else ([current] if current else [])
            after[name] = items + [value] if value not in items else items
        elif value is None:
            after.pop(name, None)
        elif isinstance(current, list):
            after[name] = [item for item in current if item != value]
    patch = {k: v for k, v in after.items() if fields.get(k) != v}
    patch.update({k: None for k in fields if k not in after})
    return patch


def _parse(args: list[str]) -> tuple[str, str, list] | str:
    """(card id, person, edits) from the arguments, or the usage text."""
    card_id, actor, edits = "", "", []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--set", "--add", "--remove", "--as") and i + 1 < len(args):
            value = args[i + 1]
            i += 2
            if arg == "--as":
                actor = value
                continue
            name, eq, item = value.partition("=")
            if not name or (not eq and arg != "--remove"):
                return f"{arg} takes field=value\n{_USAGE}"
            edits.append((arg[2:], name, item if eq else None))
            continue
        if arg.startswith("-") or card_id:
            return _USAGE
        card_id = arg
        i += 1
    if not (card_id and actor and edits):
        return _USAGE
    return card_id, actor, edits


def _refused(refusal: Refused) -> dict:
    """The endpoint's refusal as the framework's error, each field on a line."""
    lines = [str(refusal)]
    for entry in refusal.errors:
        allowed = entry.get("allowed") or []
        lines.append(f"  {entry['field']}: {entry.get('message', '')}"
                     + (f" (allowed: {', '.join(map(str, allowed))})" if allowed else ""))
    return {"error": "\n".join(lines), "status": refusal.status, "errors": list(refusal.errors)}


def _shown(value: object) -> str:
    if value is None:
        return "(removed)"
    if isinstance(value, list):
        return ", ".join(map(str, value)) or "(none)"
    return str(value)
