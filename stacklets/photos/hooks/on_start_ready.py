"""Runs after the health check on every 'stack up photos'.

Keeps Immich's login through the stack's OIDC provider in line with the
credentials the provider stored. Without a provider it does nothing.
See oauth.py.

Then, while `[photos] stack_account` is on, runs photos setup, which keeps
the stack's own account, its key and the members' shares in place and
shares the library of a member added since. Once the switch is off, the
one run after it removes the account; the account's password in the
secrets is what says there is one. With neither, nothing is asked of
Immich. See stack_account.py.
"""


from ..stack_account import PASSWORD_SECRET, is_switched_on
from ..oauth import sync


def run(ctx):
    from stack.users import TECH_ADMIN_EMAIL

    sync("http://localhost:42010", TECH_ADMIN_EMAIL,
         ctx.secret("ADMIN_PASSWORD") or "", ctx.env, ctx.step)

    if is_switched_on(ctx.stack.config) or ctx.secret(PASSWORD_SECRET):
        _stack_account(ctx)


def _stack_account(ctx):
    """Run photos setup and pass on what it says about the stack's account."""
    result = ctx.stack.run_cli_command("photos", "setup") or {}
    account = result.get("stack_account") or {}
    error = result.get("error") or account.get("error")
    if error:
        ctx.warn(f"The stack's own Photos account: {error}")
    if account.get("warning"):
        ctx.warn(account["warning"])
