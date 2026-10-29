"""Runs after the health check on every 'stack up photos'.

Keeps Immich's login through the stack's OIDC provider in line with the
credentials the provider stored. Without a provider it does nothing.
See oauth.py.

Then, while `[photos] stack_account` is on, runs `stack photos setup
--stack-account`: the stack's own account, its key, its picture and the
members' shares, and nothing of the member accounts (those are made once,
by the install hook). Once the switch is off, the one run after it
removes the account; the account's password in the secrets is what says
the stack made one. With neither, nothing is asked of Immich. See
stack_account.py.

Core reads the account's key only when it starts. Restarting core from
inside `stack up photos` would nest one `up` in another, so a new key
ends in a warning that says to run `./stack restart core`; it is printed
on every output path, a terminal or not.
"""


from ..stack_account import is_wanted
from ..oauth import sync


def run(ctx):
    from stack.users import TECH_ADMIN_EMAIL

    sync("http://localhost:42010", TECH_ADMIN_EMAIL,
         ctx.secret("ADMIN_PASSWORD") or "", ctx.env, ctx.step)

    if is_wanted(ctx.stack.config, ctx.stack.secrets.all()):
        _stack_account(ctx)


def _stack_account(ctx):
    """Run the account part of photos setup and pass on what the admin
    must read: the stage warning, an error, the next step, a hint."""
    result = ctx.stack.run_cli_command("photos", "setup", ["--stack-account"]) or {}
    for key in ("warning", "error", "next", "hint"):
        if result.get(key):
            ctx.warn(result[key])
