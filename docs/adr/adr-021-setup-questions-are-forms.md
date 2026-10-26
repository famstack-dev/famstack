# ADR-021: Setup Questions Are Forms

## Status
Proposed (2026-10-26).

## Context
A stacklet asks its first-run questions in its `on_configure` hook: the
Matrix server name, the backup disk and its passphrase, whether to set up
oMLX, the DNS provider's token. Each hook prints its own explanation, reads
the answers with `input` and `getpass` (`stack.prompt`), checks them, and
writes `stack.toml`. Without a terminal, every one of them refuses with "run
this command interactively", and the installer works around one of them with
`STACK_SETUP_CONFIRMED=1`.

That leaves three callers out. The dashboard's setup widget (RFC-001,
ADR-020) has to ask the same questions on a screen. An agent or a script has
to answer them without a terminal. A test has to drive them without faking a
keyboard. The questions are code that talks to a terminal, so none of them
can see what is asked, and none can answer.

Arthur, 2026-10-26: "create a json schema for the setup questions and based
on that ask in the CLI or have UI rendered for it ... we can also link
documentation on famstack.dev later".

## Decision

**1. A form is a step's questions, as a JSON Schema object.** A subset of
JSON Schema 2020-12, so a screen can use any JSON Schema form renderer, and
the host checks it with a validator of its own (the host CLI is
stdlib-only). Around the schema, the form's own fields:

```json
{
  "id": "messages.server-name",
  "title": "Messages server name",
  "text": "Every Matrix user ID includes the server name ...",
  "schema": {
    "type": "object",
    "properties": {
      "server_name": {
        "type": "string",
        "title": "Server name",
        "description": "Examples: griswolds, smiths, home.internal",
        "minLength": 1,
        "pattern": "^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$",
        "x-pattern-error": "Use lowercase letters, digits, dots and hyphens, like smiths or home.internal."
      }
    },
    "required": ["server_name"]
  },
  "errors": {},
  "x-docs": "https://famstack.dev/..."
}
```

- `id` names the form across runs and in messages; `title` and `text` say
  what it is for (`text` is plain prose, paragraphs split by blank lines, so
  a screen wraps it and the terminal wraps it at its width).
- `errors` maps a field to what is wrong with its answer, in plain words.
- `x-docs` is a famstack.dev page about the form, added later.

The keywords a field may use, and nothing else:

| Keyword | Meaning here |
|---|---|
| `type` | `string`, `boolean` or `integer` |
| `title`, `description` | the question, and help shown with it |
| `default` | what the terminal and a screen propose. Never an answer: a missing field is missing |
| `enum` | the allowed values |
| `oneOf` of `{const, title}` | labelled choices |
| `examples` | suggested values that do not restrict the answer (the disks mounted now) |
| `pattern`, `minLength` | the text's form and length |
| `format` | `time` (a time of day as `HH:MM`, 24 hours, what an HTML time input gives; RFC 3339's seconds and offset are not taken), `uri`, `hostname` |
| `writeOnly: true` | a secret (§5) |
| `readOnly: true` | information shown, never asked |

Anything else is prefixed `x-`: `x-pattern-error` is the sentence shown when
`pattern` does not match, and `x-confirm` is a yes/no question the terminal
asks after the answer, with `{value}` in it, where a no asks again (the
permanent server name). A screen may show it as a confirmation, or not.

**2. Steps are stateless.** A stacklet's `on_configure.py` defines

```python
def questions(ctx, answers) -> Form | None: ...   # the next form, None when nothing is left
def apply(ctx, answers) -> None: ...              # write what the answers decide
```

- `questions` returns the next form to ask given every answer so far. It
  reads and checks, and never writes. A check that looks at the machine (is
  the disk mounted, is it APFS) is made here, and a failed check returns the
  same form with an error on its field. `Form.pending(answers)` is the usual
  last line: `None` once the form is answered and valid, else the form, with
  an error on each answer that breaks its schema.
- The answers are the whole state. The terminal, a screen and a test call the
  same function, and nothing needs a channel back into a running process.
- `apply` runs once, after `questions` returned `None`, and writes
  `stack.toml`, the secret store, the Keychain. `Cancelled` keeps its meaning:
  an answer of no that stops the command without it being a failure.
- `questions(ctx, {})` returning `None` means there is nothing to ask: the
  stacklet is configured, and `apply` is not called. A configured stacklet is
  never changed by answers given to it.

**3. The framework walks the forms.** It calls `questions` with the answers
it holds so far, starting with none. Each answer given up front is offered
once, to the form that asks for it, read by its field's type (`yes`, `no`,
`true`, `false` for a boolean, digits for an integer). When a form comes back
that the given answers do not settle, the framework asks it: in a terminal,
field by field with the form's words, looping on the validator's errors;
without one, the walk stops there and the command fails with that form. So
the framework sees every form on the way, and `apply` receives the answers to
the forms that were reached and nothing else.

**4. Three ways to answer the same forms.**

- **In a terminal**, `stack up <id>` on a first run and `stack configure <id>`
  ask form by form, as the hooks did, in the same words and with the same
  defaults.
- **Without a terminal**, `--answer key=value` (repeatable) and
  `--answers <file.json>` (or `-` for stdin) supply answers to either command.
  Anything still open fails with the form that is open, naming the form and
  its fields: no "run this interactively", and no `STACK_SETUP_CONFIRMED`.
- **As data**, `stack configure <id> --json [--answers ...]` never asks. It
  prints the next form, `{"form": {...}}`, or, once nothing is open, applies
  the answers and prints `{"ready": true}`. Both exit 0: a form is an answer
  here, not a failure. A configured stacklet prints `{"ready": true}` at once.

`stack configure <id>` in a terminal on a configured stacklet says what is
set (its section of `stack.toml`, credentials hidden as `stack config --json`
hides them) and changes nothing.

**5. A secret lives only as long as the run that applies it.** A `writeOnly`
field is asked with `getpass`. Its value is never in a form's `default`, in a
progress line or `--events` line, in a result, in an error message or in a
log. The validator never quotes an answer in an error, and an error raised by
`questions` or `apply` has every secret answer of the run blanked out before
anyone sees it. The value reaches `apply` and is gone when the command ends.
On the command line a secret goes in `--answers <file>` or `--answers -`:
`--answer key=value` puts it in the process's arguments, which every process
on the Mac can read with `ps`.

**6. A hook with only `run(ctx)` runs as before.** Every extension today is
written that way, and a shell hook too. `stack configure <id> --json` on such
a stacklet says it asks in a terminal on its first `stack up`.

## A run that needs answers, for screens (shape only)

This ADR does not build it; the live-events branch (ADR-020) adds `form` to
`result_for_screens` when the two meet.

A `stack up <id> --events` run never asks, even with a terminal attached:
stdout carries events. When a form is open it ends with the one `result`
event, carrying:

```json
{"id": 7, "type": "result", "time": "...",
 "data": {"ok": false, "stacklet": "backup", "name": "Backup",
          "error": "Backup needs answers to the form 'Backup disk' (backup.disk): disk",
          "form": {"id": "backup.disk", "title": "Backup disk", "text": "...",
                   "schema": {...}, "errors": {}}}}
```

The screen renders `form`, shows `errors` on their fields, and posts every
answer it holds so far, the earlier forms' included, since the run keeps no
state. The host starts `stack up <id> --events --answers -` again with them on
stdin. The next run walks past the answered forms and either ends with the
next form or goes on to install. A `result` with `form` is not a failure to
report: nothing broke, and the server room is not told a stacklet failed. The
form carries no secret by §5, so it passes to screens as it is.

## Options weighed

- **The `jsonschema` package.** Complete, but the host CLI imports nothing
  from pip (`docs/agent/dev.md`). The subset above is what setup questions
  need, and its validator is about a hundred lines.
- **Forms declared in `stacklet.toml`.** Static forms cannot depend on an
  earlier answer (the passphrase only when the disk is encrypted) or carry a
  check's error. A later step if hand-written forms turn out to repeat.
- **A process that waits for a screen's answer.** It needs a channel back
  into a running `stack up`, and loses its questions with a dropped
  connection. ADR-020 §3 keeps state out of connections; stateless steps keep
  it out of processes too.
- **Hooks keep their prompts and read an answers file.** Answering without a
  terminal works, but a screen still cannot know what is asked, so every
  question would be written twice.

## Consequences

- `stack up <id>` and `stack configure <id>` take `--answer` and `--answers`.
  Both branches add flags to `up` in `lib/stack/cli.py`.
- The messages, backup, ai and infra hooks are forms. `STACK_SETUP_CONFIRMED`
  is gone, and nothing in them checks for a terminal.
- `stack up infra` asks for a yes to the network warning, which it used to
  print and go on. Without a terminal it needs `--answer understood=yes`.
- The backup disk is checked against what `mount` reports, one source for
  "is it mounted" and "is it APFS", and its form lists the mounted volumes.
- `on_configure` is documented in `docs/stack-reference.md` with both
  contracts.
