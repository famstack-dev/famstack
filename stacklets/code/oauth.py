"""Forgejo login through the stack's OIDC provider.

Forgejo keeps login sources in its database. The `[oauth2_client]`
settings (auto registration, account linking, the username claim) come
from the environment like the rest of its config, but the source itself
is created through the admin CLI inside the container:
`forgejo admin auth list | add-oauth | update-oauth`.

The stacklet manages one source, named SOURCE. The name is the label on
the login button ("Sign in with family account") and part of the
callback path, `/user/oauth2/<name>/callback`, which is why the
manifest's `[oidc]` callback spells it out. Other login sources an admin
added are left alone.

The CLI does not show a source's stored credentials, so there is
nothing in Forgejo to compare against. The stacklet keeps a fingerprint
of what it applied, a hash of the client id, secret and discovery URL,
in its own secret namespace, and updates the source only when the
fingerprint changes or the source is gone.

Without credentials nothing runs, so an install without a provider
never has a source created. The CLI runs through
`ctx.run_in_container`, which passes the client secret as an argument
and leaves the arguments out of any error, so the secret never appears
in the output.

Stdlib only, like every host-side hook.
"""

import hashlib

SOURCE = "family account"
FINGERPRINT = "OIDC_APPLIED"


# ── What the source should say ────────────────────────────────────────

def desired(env: dict) -> dict | None:
    """The source's flags, or None without credentials."""
    issuer = env.get("OIDC_ISSUER", "")
    client_id = env.get("OIDC_CLIENT_ID", "")
    secret = env.get("OIDC_CLIENT_SECRET", "")
    if not (issuer and client_id and secret):
        return None
    return {
        "--key": client_id,
        "--secret": secret,
        "--auto-discover-url": f"{issuer}/.well-known/openid-configuration",
    }


def fingerprint(flags: dict) -> str:
    """A hash of what was applied, so a change can be seen without
    storing the secret a second time."""
    material = "\n".join(f"{k}={flags[k]}" for k in sorted(flags))
    return hashlib.sha256(material.encode()).hexdigest()


def source_ids(listing: str) -> dict[str, int]:
    """Name to id, from the tab-separated table `auth list` prints.
    Forgejo pads a short cell with extra tabs after it, which leaves the
    first two columns in place."""
    ids = {}
    for row in listing.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) >= 2 and cols[0].strip().isdigit():
            ids[cols[1].strip()] = int(cols[0])
    return ids


# ── Talking to Forgejo ────────────────────────────────────────────────

def auth_cli(ctx):
    """A runner for `forgejo admin auth <args>` in the code container.

    Forgejo's CLI refuses to run as root, so it runs as `git`, the same
    user the on_install_success hook uses.
    """
    def run(args: list[str]) -> str:
        return ctx.run_in_container(["forgejo", "admin", "auth", *args], user="git")
    return run


def sync(env: dict, run, secret, step) -> None:
    """Keep the family account source in line with the provider.

    `run` executes one `forgejo admin auth` command and raises
    RuntimeError on failure (see `auth_cli`). `secret` reads and writes
    this stacklet's secrets. Prints one line when the source was created
    or updated and nothing otherwise, since this runs on every
    `stack up code`. Errors are reported, not raised: Forgejo and its
    password login still work, and the next run tries again.
    """
    want = desired(env)
    if want is None:
        return
    applied = fingerprint(want)
    try:
        existing = source_ids(run(["list"])).get(SOURCE)
        if existing is not None and secret(FINGERPRINT) == applied:
            return
        if existing is None:
            run(["add-oauth", "--name", SOURCE, "--provider", "openidConnect",
                 *_pairs(want)])
            step(f"Forgejo login source '{SOURCE}' created")
        else:
            run(["update-oauth", "--id", str(existing), *_pairs(want)])
            step(f"Forgejo login source '{SOURCE}' updated")
        secret(FINGERPRINT, applied)
    except RuntimeError as e:
        step(f"Forgejo login source not updated: {e}")


def _pairs(flags: dict) -> list[str]:
    return [part for k, v in flags.items() for part in (k, v)]
