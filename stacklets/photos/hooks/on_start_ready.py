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
inside `stack up photos` would nest one `up` in another, so a changed or
removed key ends in a warning that says to run `./stack restart core`.
Every line is printed as a warning, so it shows on every output path, a
terminal or not.
"""


from ..stack_account import for_the_admin, is_wanted
from ..oauth import sync


def run(ctx):
    from stack.users import TECH_ADMIN_EMAIL

    sync("http://localhost:42010", TECH_ADMIN_EMAIL,
         ctx.secret("ADMIN_PASSWORD") or "", ctx.env, ctx.step)

    if is_wanted(ctx.stack.config, ctx.stack.secrets.all()):
        _stack_account(ctx)


def _stack_account(ctx):
    """Run the account part of photos setup and pass on every line of its
    answer a person must act on or should know (`for_the_admin`)."""
    result = ctx.stack.run_cli_command("photos", "setup", ["--stack-account"]) or {}
    for line in for_the_admin(result):
        ctx.warn(line)
