"""Every `stack up messages`: the family's admins are admins in every family room.

Rooms appear after install (a stacklet's bot room, `stack messages room
create`), and an install from before this rule has rooms where only the
tech admin holds power. Reconciling on every start covers both. A failure
is reported and does not stop the stack.
"""


def run(ctx):
    # `setup --room-admins` prints one line per admin itself.
    result = ctx.stack.run_cli_command("messages", "setup", ["--room-admins"]) or {}
    if result.get("error"):
        ctx.warn(f"Could not check the family admins' room rights: {result['error']}")
