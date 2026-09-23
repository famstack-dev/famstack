"""Runs after the health check on every 'stack up photos'.

Keeps Immich's login through the stack's OIDC provider in line with the
credentials the provider stored. Without a provider it does nothing.
See oauth.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from oauth import sync


def run(ctx):
    from stack.users import TECH_ADMIN_EMAIL

    sync("http://localhost:42010", TECH_ADMIN_EMAIL,
         ctx.secret("ADMIN_PASSWORD") or "", ctx.env, ctx.step)
