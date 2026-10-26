"""What each DNS provider's token is, in the words of its console.

With `[core] dns_provider` set, Caddy obtains its certificates by writing
a TXT record through the provider's API, and needs a token for it. Both
places that ask for the token say which one: the setup question
(`hooks/on_configure.py`) and `stack infra dns-token`, which replaces it.
"""

# What each provider's Caddy plugin authenticates with. Hetzner's plugin
# talks to the Hetzner Cloud API, so a token from the older DNS Console
# does not work.
TOKEN_KIND = {
    "hetzner": "a Hetzner Cloud API token with Read & Write access, created in "
               "the Console project that holds the zone (Security > API tokens)",
    "cloudflare": "a Cloudflare API token with Zone.Zone:Read and Zone.DNS:Edit "
                  "on the zone",
}
