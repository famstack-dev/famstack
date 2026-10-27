"""on_configure: a stacklet's first-run questions, walked as forms.

`stack up <id>` runs a stacklet's `on_configure` on its first run, and
`stack configure <id>` runs it on its own. A hook written as forms
(ADR-021) defines two functions instead of `run(ctx)`:

    def questions(ctx, answers) -> Form | None   # the next form, None when nothing is left
    def apply(ctx, answers) -> None              # write what the answers decide

`questions` reads and checks and never writes, so the terminal, a screen
and a test call it alike: the answers are the whole state. This module
walks it. It starts with no answers; `questions(ctx, {})` returning None
means the stacklet is configured, and `apply` is not called. Otherwise
each answer given up front (`--answer`, `--answers`) is offered once, to
the form that asks for it, read by its field's type. A form the given
answers do not settle goes to the asker: the terminal asks it field by
field (`stack.form_prompt`), and without a terminal `nobody` raises
`NeedsAnswers` with the form, which the caller turns into a result a
screen can render. When `questions` has nothing left, `apply` gets the
answers to the forms that were reached, and nothing else.

A hook with only `run(ctx)`, every extension written before forms, and a
shell hook run exactly as they did.

Secrets. A field marked `writeOnly` is a secret. The terminal reads it
with `getpass`; no form carries its value as a default (`Form.to_dict`);
the validator never quotes an answer; and an error raised by `questions`
or `apply` has every secret answer of the run blanked out before it is
printed or returned. Progress lines are the hook's own words and never
carry an answer. The value lives in this run's memory until `apply` has
stored it where it belongs (the Keychain, the secret store), and nowhere
else.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from .forms import Form, read_typed
from .hooks import Cancelled, HookResolver, run_python_file

# Asks a form and returns the answers given for its fields.
Ask = Callable[[Form, dict], dict]

NOTHING_TO_ASK = {"ready": True, "applied": False}
APPLIED = {"ready": True, "applied": True}


class NeedsAnswers(Exception):
    """A form is open and nobody can be asked: no terminal, or `--json`.

    The message names the form and its open fields, and how to answer
    them. It quotes no answer, so it never carries a secret.
    """

    def __init__(self, form: Form, answers: dict):
        self.form = form
        names = [name for name in form.asked()
                 if name in form.errors or name in form.missing(answers)]
        said = [f"{name}: {form.errors[name]}" if name in form.errors else name
                for name in names]
        first = names[0] if names else "<field>"
        super().__init__(
            f"Setup needs answers to the form '{form.title}' ({form.id}): "
            f"{'; '.join(said)}. Give them with --answer {first}=<value> "
            f"or --answers <file>, or run this in a terminal.")


def nobody(form: Form, answers: dict) -> dict:
    """The asker when nobody can answer: the form goes back to the caller."""
    raise NeedsAnswers(form, answers)


def asker() -> Ask:
    """Ask in the terminal when there is one, otherwise nobody."""
    if sys.stdin.isatty():
        from .form_prompt import ask_form
        return ask_form
    return nobody


# ── Running the hook ─────────────────────────────────────────────────────

def run_on_configure(stacklet_dir: Path, ctx, answers: dict | None = None,
                     ask: Ask | None = None, forms_only: bool = False) -> dict:
    """Run the stacklet's `on_configure`, whichever kind it is.

    Returns `{"ready": True, "applied": ...}` when it is done (`applied`
    False when there was nothing to ask), `{"error": ..., "form": ...}`
    when a form is open and nobody can answer it, or `{"error":
    "Configuration failed"}` when the hook failed, which is printed as
    before. `forms_only` refuses a hook that asks for itself, for callers
    that must not wait on a terminal. Raises `Cancelled`.
    """
    resolver = HookResolver(stacklet_dir)
    path = resolver.resolve("on_configure")
    if path is None:
        return dict(NOTHING_TO_ASK)
    if path.suffix != ".py":
        return _asks_itself(ctx, forms_only) or (
            dict(APPLIED) if resolver.run("on_configure", ctx) else _failed())

    walk = _Walk(answers or {}, ask or asker())
    try:
        return cast(dict, run_python_file(
            stacklet_dir, path, ctx, call=lambda module: walk.through(module, ctx, forms_only)))
    except Cancelled:
        raise
    except NeedsAnswers as e:
        return {"error": str(e), "form": e.form.to_dict()}
    except Exception as e:
        ctx.stack.output.error(f"Hook {path.name} error: {walk.blanked(str(e))}")
        return _failed()


def asks(stacklet_dir: Path, ctx) -> bool:
    """Whether a first `stack up` would ask anything, with no answers given.

    For screens, which offer a button only for a stacklet that asks
    nothing (`stack list`'s `asks_on_first_up`). False without an
    `on_configure`, or when its forms have nothing left
    (`questions(ctx, {})` is None). True for a form still open, for a hook
    that asks for itself (a shell hook, `run(ctx)`), and for a `questions`
    that fails: the terminal then shows why. Calls only `questions`, which
    writes nothing, and prints nothing.
    """
    path = HookResolver(stacklet_dir).resolve("on_configure")
    if path is None:
        return False
    if path.suffix != ".py":
        return True

    def still_asks(module) -> bool:
        return not hasattr(module, "questions") or module.questions(ctx, {}) is not None

    try:
        return bool(run_python_file(stacklet_dir, path, ctx, call=still_asks))
    except Exception:
        return True


def _asks_itself(ctx, forms_only: bool) -> dict | None:
    """The answer for a hook that does its own asking, when only forms may run."""
    if not forms_only:
        return None
    return {"error": f"{ctx.stacklet_id} asks its setup questions itself, in a terminal: "
                     f"run 'stack up {ctx.stacklet_id}' there."}


def _failed() -> dict:
    return {"error": "Configuration failed"}


# ── The walk ─────────────────────────────────────────────────────────────

class _Walk:
    """One walk through a hook's forms: the answers given, the answers
    settled so far, and which of them are secrets.

        walk = _Walk({"server_name": "simpson"}, nobody)
        walk.through(module, ctx)     # {"ready": True, "applied": True}
    """

    def __init__(self, given: dict, ask: Ask):
        self.offered = dict(given)
        self.ask = ask
        self.known: dict = {}
        self.secret_fields: set[str] = set()

    def through(self, module, ctx, forms_only: bool = False) -> dict:
        """Walk `module`'s forms and apply them, or call its `run(ctx)`."""
        if not hasattr(module, "questions"):
            refused = _asks_itself(ctx, forms_only)
            if refused:
                return refused
            if hasattr(module, "run"):
                module.run(ctx)
            return dict(APPLIED)
        if module.questions(ctx, {}) is None:
            return dict(NOTHING_TO_ASK)
        while (form := module.questions(ctx, self.known)) is not None:
            self._settle(form)
        module.apply(ctx, dict(self.known))
        return dict(APPLIED)

    def _settle(self, form: Form) -> None:
        """Take what was given for `form`'s fields, once; if nothing was,
        ask it."""
        self.secret_fields |= form.secrets()
        given = {name: self.offered.pop(name) for name in form.asked() if name in self.offered}
        if given:
            self.known.update(read_typed(form, given))
            return
        self.known.update(self.ask(form, dict(self.known)))

    def blanked(self, text: str) -> str:
        """`text` with every secret answer of this walk replaced."""
        for name in self.secret_fields:
            value = self.known.get(name, self.offered.get(name))
            if isinstance(value, str) and value:
                text = text.replace(value, "(hidden)")
        return text
