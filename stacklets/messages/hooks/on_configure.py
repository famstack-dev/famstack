"""Prompt for the Matrix server name on first setup.

The server name is permanent — it becomes part of every user ID
(e.g. @arthur:familyname) and cannot be changed after Synapse starts.
This hook explains the consequences and writes the choice to stack.toml.

Runs before on_install. Only fires on first 'stack up messages'.
"""

import sys

from stack.prompt import section, out, nl, dim, ask, confirm


def run(ctx):
    # Check if already configured
    server_name = ctx.cfg("server_name", default="")
    if server_name:
        ctx.step(f"Server name: {server_name}")
        return

    # Non-interactive mode — can't prompt
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Messages requires a server name. "
            "Add it to stack.toml under [messages], or run this command interactively."
        )

    section("Messages", "Family messaging and notification backbone")
    out("Every Matrix user ID includes the server name:")
    out("  @arthur:familyname  ←  this part is permanent")
    nl()
    out("Pick something short and meaningful — your family name")
    out("works well. This cannot be changed later without starting")
    out("over with a fresh database.")
    nl()
    dim("If you plan to use your own family domain later (e.g. yourfamily.com)")
    dim("use that instead — it keeps the")
    dim("option open for connecting with other servers later if you ever want to.")
    nl()
    dim("Examples: griswolds, smiths, home.internal")
    nl()

    name = ask("Server name")
    if not name:
        raise RuntimeError("No server name entered")

    out(f"Your user IDs will look like: @you:{name}")
    if not confirm(f"Use '{name}'?", default=False):
        raise RuntimeError("Aborted")

    ctx.cfg("server_name", name)
    ctx.step(f"Server name '{name}' saved to stack.toml")
