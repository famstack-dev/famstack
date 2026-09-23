"""Runs after the health check on every 'stack up code'.

Keeps Forgejo's login source for the stack's OIDC provider in line with
the credentials the provider stored. Without a provider it does nothing.
See oauth.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from oauth import auth_cli, sync


def run(ctx):
    sync(ctx.env, auth_cli(ctx), ctx.secret, ctx.step)
