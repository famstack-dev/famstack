"""A stacklet's setup forms, answered in the dashboard: relayed, or walked.

A stacklet whose first `up` asks questions in forms (ADR-021) is added from
the page: its Add opens the forms, and the page posts every answer so far
to `POST /dashboard/api/configure/<id>`. The host API's `configure` runs
`stack configure <id> --json` with them and answers with the next form,
the same form with the reason under a field, or ready once it applied them.

A secret (a `writeOnly` field) is answered only from a page opened on this
Mac: on the network the dashboard is plain HTTP, and a passphrase typed on
a phone would cross the Wi-Fi in the clear. From this Mac the answers are
relayed as they are. From any other device `walk` asks for one form at a
time and forwards only answers to fields of the forms it has been shown
without a secret, so an answer it was never asked for, a secret included,
never reaches the host API; at the first form that asks for a secret it
stops and answers with the terminal command instead.

Pure: `call(answers)` is the one way out, the host API's `configure` for
this stacklet; no web framework.
"""

from __future__ import annotations

from collections.abc import Callable

Call = Callable[[dict], dict]


def relay(stacklet: str, answers: dict, call: Call, *, here: bool) -> dict:
    """The answer to `answers` for `stacklet`: the host API's own from this
    Mac (`here`), `walk`'s from anywhere else."""
    return call(answers) if here else walk(stacklet, answers, call)


def walk(stacklet: str, answers: dict, call: Call) -> dict:
    """Ask form after form, forwarding only answers to the fields of forms
    without a secret, until the answers settle nothing more; at a form with
    a secret, `{"terminal": "stack up <id>"}` instead."""
    given: dict = {}
    while True:
        result = call(given)
        form = result.get("form")
        if form is None:
            return result
        fields = form.get("schema", {}).get("properties", {})
        if any(spec.get("writeOnly") for spec in fields.values()):
            return {"terminal": f"stack up {stacklet}"}
        taking = {name: answers[name] for name in fields if name in answers and name not in given}
        if form.get("errors") or not taking:
            return result
        given.update(taking)
