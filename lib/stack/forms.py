"""Setup forms: a step's questions as data, and the one validator for them.

A stacklet's first-run questions are forms (ADR-021): a JSON Schema object
in a small subset, with the form's `id`, `title`, an explanation `text`,
per-field `errors`, and later a famstack.dev link (`x-docs`). The terminal
renders a form and asks it (`stack.form_prompt`); a caller without a
terminal receives it as JSON (`Form.to_dict`) and posts the answers back.
The same `validate` judges the answers in both cases, so a screen and the
terminal refuse the same answers with the same words.

The subset, per field: `type` (string, boolean, integer), `title`,
`description`, `default`, `enum`, labelled choices as `oneOf` of
`{const, title}`, `examples`, `pattern`, `minLength`, `format` (`time`,
`uri`, `hostname`), `writeOnly` for a secret and `readOnly` for
information shown. Beyond the standard, `x-pattern-error` is the sentence
shown when `pattern` does not match, and `x-confirm` a question the
terminal asks after an answer. The host CLI is stdlib-only, so this is the
validator, not the `jsonschema` package.

Two rules hold for every message here. A default is never an answer: a
field nobody answered is missing, whatever its default. And an error never
quotes the answer, which may be a secret.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace


# ── The form ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Form:
    """One step's questions, as a hook returns it from `questions`.

        SERVER_NAME = Form(
            id="messages.server-name", title="Messages server name", text=WHY,
            schema={"type": "object",
                    "properties": {"server_name": {"type": "string", "minLength": 1}},
                    "required": ["server_name"]})

        def questions(ctx, answers):
            return SERVER_NAME.pending(answers)
    """

    id: str
    title: str
    schema: dict
    text: str = ""
    errors: dict[str, str] = field(default_factory=dict)
    docs: str = ""

    @property
    def fields(self) -> dict[str, dict]:
        """Every field, in the order it is asked and shown."""
        return self.schema.get("properties", {})

    def asked(self) -> list[str]:
        """The fields an answer is given for: all but the read-only ones."""
        return [name for name, spec in self.fields.items() if not spec.get("readOnly")]

    def secrets(self) -> set[str]:
        """The fields whose answers are secrets (`writeOnly`)."""
        return {name for name, spec in self.fields.items() if spec.get("writeOnly")}

    def missing(self, answers: dict) -> list[str]:
        """The required fields `answers` leaves out."""
        asked = self.asked()
        return [name for name in self.schema.get("required", [])
                if name in asked and name not in answers]

    def with_errors(self, **errors: str) -> Form:
        """This form, saying what is wrong with these fields' answers."""
        return replace(self, errors={**self.errors, **errors})

    def pending(self, answers: dict) -> Form | None:
        """None once `answers` settle this form; otherwise the form, with
        an error on each answer that breaks its schema."""
        errors = validate(self, answers)
        if errors or self.missing(answers):
            return self.with_errors(**errors)
        return None

    def to_dict(self) -> dict:
        """The form as a screen receives it. A secret's `default` is left
        out, so a value put there by mistake never leaves the host."""
        properties = {name: ({k: v for k, v in spec.items() if k != "default"}
                             if spec.get("writeOnly") else spec)
                      for name, spec in self.fields.items()}
        shown = {"id": self.id, "title": self.title, "text": self.text,
                 "schema": {**self.schema, "properties": properties},
                 "errors": dict(self.errors)}
        if self.docs:
            shown["x-docs"] = self.docs
        return shown


# ── Answers typed as text ────────────────────────────────────────────────

_YES = {"yes", "y", "true", "1"}
_NO = {"no", "n", "false", "0"}


def read_typed(form: Form, answers: dict) -> dict:
    """`answers` with the text given for this form's fields read by their
    types: `--answer keep=yes` is a yes for a boolean field, `12` a number
    for an integer one. Text that does not read stays text, for the
    validator to refuse; answers to other forms' fields stay as they are."""
    typed = dict(answers)
    for name, spec in form.fields.items():
        value = answers.get(name)
        if not isinstance(value, str):
            continue
        word = value.strip().lower()
        if spec.get("type") == "boolean" and word in _YES | _NO:
            typed[name] = word in _YES
        elif spec.get("type") == "integer" and re.fullmatch(r"-?\d+", word):
            typed[name] = int(word)
    return typed


# ── The validator ────────────────────────────────────────────────────────

# A time of day as an HTML time input gives it, 24 hours. ADR-021 narrows
# JSON Schema's `time` (RFC 3339, with seconds and an offset) to this,
# because a nightly schedule is all a setup question asks for.
_TIME = re.compile(r"([01]?\d|2[0-3]):[0-5]\d")
# RFC 1123 host names: dot-separated labels of letters, digits and inner
# hyphens, each at most 63 long.
_LABEL = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*")

_FORMATS = {
    "time": (lambda v: bool(_TIME.fullmatch(v)),
             "Answer with a time like 02:00 or 22:15."),
    "uri": (lambda v: _is_uri(v),
            "Answer with a full address, like https://example.com."),
    "hostname": (lambda v: len(v) <= 253 and all(_LABEL.fullmatch(p) for p in v.split(".")),
                 "Answer with a host name, like home.internal."),
}

_TYPES = {
    "string": (lambda v: isinstance(v, str), "Answer with text."),
    "boolean": (lambda v: isinstance(v, bool), "Answer yes or no."),
    "integer": (lambda v: isinstance(v, int) and not isinstance(v, bool),
                "Answer with a whole number."),
}


def validate(form: Form, answers: dict) -> dict[str, str]:
    """What is wrong with each answered field of `form`, in plain words.

    Only fields that have an answer are judged; a missing one is
    `Form.missing`, not an error, so a form shown for the first time
    carries none. Read-only fields take no answer and are skipped.
    """
    errors = {}
    for name in form.asked():
        if name in answers:
            problem = check(form.fields[name], answers[name])
            if problem:
                errors[name] = problem
    return errors


def check(spec: dict, value) -> str:
    """What is wrong with `value` as an answer to the field `spec`, or ""."""
    is_type, wrong_type = _TYPES.get(spec.get("type", "string"), _TYPES["string"])
    if not is_type(value):
        return wrong_type
    if "enum" in spec and value not in spec["enum"]:
        return "Choose one of: " + ", ".join(_word(v) for v in spec["enum"]) + "."
    if "oneOf" in spec and value not in [c.get("const") for c in spec["oneOf"]]:
        return "Choose one of: " + ", ".join(
            f"{_word(c.get('const'))} ({c.get('title', c.get('const'))})" for c in spec["oneOf"]) + "."
    if isinstance(value, str):
        return _check_text(spec, value)
    return ""


def _check_text(spec: dict, value: str) -> str:
    """The string keywords: length, pattern, format."""
    least = spec.get("minLength", 0)
    if len(value) < least:
        return "Must not be empty." if least == 1 else f"Must be at least {least} characters."
    if "pattern" in spec and not re.search(spec["pattern"], value):
        return spec.get("x-pattern-error", "Does not have the expected form.")
    if spec.get("format") in _FORMATS:
        is_valid, wrong = _FORMATS[spec["format"]]
        if not is_valid(value):
            return wrong
    return ""


def _is_uri(value: str) -> bool:
    """An absolute address: a scheme, then something, and no spaces."""
    scheme, _, rest = value.partition(":")
    return bool(_SCHEME.fullmatch(scheme)) and bool(rest) and not re.search(r"\s", value)


def _word(value) -> str:
    """A value the way a person types it: a boolean as yes or no."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)
