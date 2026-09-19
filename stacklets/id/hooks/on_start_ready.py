"""Runs after the health check on every 'stack up id'.

Creates Pocket ID accounts for new users.toml entries (one-time login
link per person, written to the onboarding dir) and OIDC clients for
every stacklet that declares an [oidc] table. See provision.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from provision import provision


def run(ctx):
    provision(ctx)
