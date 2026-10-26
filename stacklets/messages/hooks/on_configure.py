"""The Matrix server name, asked on the first `stack up messages`.

The server name is permanent: it becomes part of every user ID
(`@homer:familyname`) and cannot be changed after Synapse starts. Its form
explains that, takes a host name only, and in a terminal asks once more
before keeping it (`x-confirm`). `apply` writes `[messages] server_name`.

The installer writes the name itself, so on an installed stack there is
nothing to ask. Without a terminal, `--answer server_name=<name>` answers
it (ADR-021).
"""

from stack.forms import Form

WHY = (
    "Every Matrix user ID includes the server name:\n"
    "  @homer:familyname  ←  this part is permanent\n\n"
    "Pick something short and meaningful: your family name works well. "
    "This cannot be changed later without starting over with a fresh database."
)

SERVER_NAME = Form(
    id="messages.server-name",
    title="Messages",
    text=WHY,
    schema={
        "type": "object",
        "properties": {
            "server_name": {
                "type": "string",
                "title": "Server name",
                "description": (
                    "If you plan to use your own family domain later (e.g. yourfamily.com), "
                    "use that instead: it keeps the option open for connecting with other "
                    "servers later if you ever want to. Examples: griswolds, smiths, "
                    "home.internal"),
                "minLength": 1,
                "pattern": r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$",
                "x-pattern-error": (
                    "Use lowercase letters, digits, dots and hyphens, like smiths or "
                    "home.internal."),
                "x-confirm": "Your user IDs will look like: @you:{value}\nUse '{value}'?",
            },
        },
        "required": ["server_name"],
    },
)


def questions(ctx, answers):
    if ctx.cfg("server_name", default=""):
        return None
    return SERVER_NAME.pending(answers)


def apply(ctx, answers):
    name = answers["server_name"]
    ctx.cfg("server_name", name)
    ctx.step(f"Server name '{name}' saved to stack.toml")
