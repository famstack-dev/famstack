"""
stack infra dns-token — store the API token of the DNS provider.

With `[core] dns_provider` set, Caddy obtains its certificates by writing
a TXT record through the provider's API, and needs a token for it. The
token is kept in the secret store (`.stack/secrets.toml`), never in
stack.toml, and reaches the Caddy container as `DNS_API_TOKEN` on the
next `stack up infra`.

In a terminal it asks for the token without echoing it. Piped, it reads
the first line of stdin. Run it again to replace the token, then
`stack up infra`.

    stack infra dns-token
"""

HELP = "Store or replace the DNS provider API token used for HTTPS"

import getpass
import sys
from pathlib import Path

from stack.secrets import TomlSecretStore


# What each provider's Caddy plugin authenticates with. Hetzner's plugin
# talks to the Hetzner Cloud API, so a token from the older DNS Console
# does not work.
_TOKEN_KIND = {
    "hetzner": "a Hetzner Cloud API token with Read & Write access, created in "
               "the Console project that holds the zone (Security > API tokens)",
    "cloudflare": "a Cloudflare API token with Zone.Zone:Read and Zone.DNS:Edit "
                  "on the zone",
}


def _read_token(provider: str) -> str:
    if sys.stdin.isatty():
        if provider in _TOKEN_KIND:
            print(f"  {provider} needs {_TOKEN_KIND[provider]}.", file=sys.stderr)
        return getpass.getpass("  ▸ DNS API token (input hidden): ")
    return sys.stdin.readline()


def run(args, stacklet, config):
    provider = config["stack"].get("core", {}).get("dns_provider", "")
    token = _read_token(provider).strip()
    if not token:
        return {"error": "No token given. The stored token, if any, is unchanged."}

    store = TomlSecretStore(Path(config["instance_dir"]) / ".stack" / "secrets.toml")
    store.set(stacklet["id"], "DNS_API_TOKEN", token)
    # The token itself is never printed. Its length tells a complete paste
    # from a truncated one.
    return {
        "stored": f"DNS API token ({len(token)} characters)",
        "next": "stack up infra",
    }
