"""How a snapshot command prints its answer and asks its question.

`stack snapshot` and `stack rollback` answer the same way. With `--json`
they print JSON. An error goes to stderr with exit 1. Else they print
lines for a person. A question goes to stderr when stdout carries JSON,
because a script must still read one JSON document.

    from stack.snapshot.answer import ask, print_answer

    if ask("Remove it?", quiet=args.json):
        print_answer({"ok": True, "removed": "2026-11-24T2114Z"}, args.json,
                     lambda result: [f"  Removed {result['removed']}."])
    print_answer({"error": "There is no snapshot 2026-01-01T0000Z."}, False, None)
    # stderr: "  ✗  There is no snapshot 2026-01-01T0000Z.", exit 1

`stack snapshot remove` and `stack rollback` ask while they hold the run
locks of the stacklets they change (`runs.reserve`). They hold them
because nothing may start one of those stacklets between the plan and
the answer.

Debt: a question left open keeps those stacklets locked. `stack up` and
`stack restart` of them refuse until the terminal answers or closes. For
a rollback, every `stack snapshot remove` refuses too. Trigger: a report
of a held lock, or a question on a page or in a bot. Then `ask` gets a
timeout, or the lock moves after the answer with the second plan as the
only guard.
"""

from __future__ import annotations

import contextlib
import json
import sys

from ..prompt import RED, RESET, confirm

__all__ = ["ask", "print_answer"]


def print_answer(result: dict, as_json: bool, lines) -> None:
    """Print `result` as JSON, its error on stderr, or `lines(result)`.
    Exits 1 after an error, as every `stack` command does.

        print_answer({"snapshots": []}, True, None)   # {"snapshots": []}
    """
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif "error" in result:
        print(f"\n  {RED}✗{RESET}  {result['error']}\n", file=sys.stderr)
    else:
        print("\n".join(lines(result)))
    if "error" in result:
        sys.exit(1)


def ask(question: str, quiet: bool) -> bool:
    """Ask `question`, with no as the default. The question goes to stderr
    when stdout carries JSON (`quiet`). False at the end of input.

        ask("Roll back?", quiet=False)   #   ? Roll back? [y/N]
    """
    if not quiet:
        return confirm(question, default=False)
    with contextlib.redirect_stdout(sys.stderr):
        return confirm(question, default=False)
