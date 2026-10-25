"""Runs after the health check on every 'stack up code'.

Keeps Forgejo's login source for the stack's OIDC provider in line with
the credentials the provider stored. Without a provider it does nothing.
See oauth.py.
"""


from ..oauth import auth_cli, sync


def run(ctx):
    sync(ctx.env, auth_cli(ctx), ctx.secret, ctx.step)
