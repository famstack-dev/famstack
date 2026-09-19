"""Pre-start hook for the id stacklet.

Creates the data directory before the container starts. Pocket ID runs
as a non-root user inside the image and refuses to start if it cannot
write its SQLite file. Idempotent; 'stack up' runs it every time.
"""

import os
from pathlib import Path


def run(ctx):
    data_dir = Path(ctx.env["ID_DATA_DIR"])
    ctx.step("Creating data directory")
    data_dir.mkdir(parents=True, exist_ok=True)
    # Only the owner and the container's group need it; passkey public
    # keys are not secret, the OIDC signing keys inside are.
    os.chmod(data_dir, 0o770)
    onboarding = Path(ctx.env["ID_ONBOARDING_DIR"])
    onboarding.mkdir(parents=True, exist_ok=True)
    os.chmod(onboarding, 0o700)
    ctx.step(f"data: {data_dir}")
