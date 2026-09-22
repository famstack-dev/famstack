"""Refuse to start a proxy that could not get its certificates.

With `[core] dns_provider` set, Caddy needs a plugin for that provider,
compiled into the infra image, and an API token for it. Without either it
would start, fail every certificate request, and serve nothing over
HTTPS, with the reason only in its logs. Stopping here puts the fix in
front of the admin instead.
"""

from stack.caddy import DNS_PROVIDERS


def run(ctx):
    provider = ctx.stack._cfg("core", "dns_provider")
    if not provider:
        return

    if provider not in DNS_PROVIDERS:
        raise RuntimeError(
            f"[core] dns_provider = '{provider}' is not supported. "
            f"Use {' or '.join(DNS_PROVIDERS)}, or leave it empty for plain HTTP.")

    if not ctx.secret("DNS_API_TOKEN"):
        raise RuntimeError(
            f"HTTPS through {provider} needs an API token. "
            "Store it with `stack infra dns-token`, then run `stack up infra` again.")
