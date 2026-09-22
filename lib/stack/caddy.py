"""The Caddyfile for domain mode, assembled from stacklet snippets.

In domain mode every service is reached by name (`photos.<domain>`)
through one reverse proxy, the Caddy container of the proxy stacklet.
A stacklet that serves something ships its routes as a `caddy.snippet`;
this module joins the snippets of the stacklets that are up into the one
Caddyfile that container mounts.

Snippets describe routes only. Everything that applies to the proxy as a
whole, the catch-all for hosts no stacklet claims and how certificates
are obtained, is emitted here, so a snippet never has to know about any
other.

With `[core] dns_provider` set, every certificate comes from Let's
Encrypt through a DNS-01 challenge at that provider: Caddy writes a TXT
record through the provider's API, so nothing on the LAN has to be
reachable from the internet. That is also the only challenge that can
issue the wildcard certificate. The `*.<domain>` site makes Caddy manage
one, and every subdomain site uses it rather than getting its own. The
bare domain is not covered by the wildcard, and gets a second
certificate the same way.

The domain is left as the `{$STACK_DOMAIN}` placeholder, in snippets and
here alike. Caddy fills it in from its own environment when it loads the
file, so the file is a function of which stacklets are up and nothing
else.
"""
from __future__ import annotations

# The stacklet that runs the proxy, and where it expects its config.
STACKLET = "infra"
CONTAINER = "stack-infra-caddy"
CONFIG_IN_CONTAINER = "/etc/caddy/Caddyfile"

RELOAD_COMMAND = ("caddy", "reload", "--config", CONFIG_IN_CONTAINER)

# The admin's own sites, next to the assembled Caddyfile, appended after
# the stacklets' snippets.
LOCAL_FILE = "Caddyfile.local"

_HEADER = """\
# Assembled by the stack CLI from the caddy.snippet of every stacklet that
# is up, and Caddyfile.local next to this file. It is rewritten whenever a
# stacklet starts or stops, so an edit here does not survive: put your own
# sites in Caddyfile.local, or change the stacklet's snippet.
"""

# Sites without a scheme listen on the HTTPS port, TLS or not. With no
# certificates to serve, that port is moved to 80, so every snippet is
# served as plain HTTP without having to spell out `http://` itself.
_PLAIN_HTTP = """\
{
\tauto_https off
\thttps_port 80
}
"""

# The DNS providers whose Caddy plugin is compiled into the infra image,
# and the settings each one's plugin asks for beyond the token. Hetzner's
# README asks for a delay before Caddy starts checking for the record.
DNS_PROVIDERS = {
    "hetzner": ("propagation_delay 30s", "propagation_timeout 5m"),
    "cloudflare": (),
}

# The token is read from Caddy's environment when a certificate is
# requested, so it never appears in this file or in the config Caddy
# serves on its admin endpoint.
_TOKEN = "{env.DNS_API_TOKEN}"

# A host that no running stacklet claims, including one whose stacklet is
# stopped, gets an answer instead of a connection error.
_UNKNOWN_HOSTS = """\
*.{$STACK_DOMAIN} {
\trespond "No service at {host}" 404
}
"""


def _global_options(dns_provider: str) -> str:
    """Global options: certificates through the DNS provider, or none."""
    if not dns_provider:
        return _PLAIN_HTTP
    if dns_provider not in DNS_PROVIDERS:
        raise ValueError(
            f"dns_provider '{dns_provider}' is not supported; "
            f"use {' or '.join(DNS_PROVIDERS)}, or leave it empty for plain HTTP")
    lines = [f"dns {dns_provider} {_TOKEN}", *DNS_PROVIDERS[dns_provider]]
    body = "".join(f"\t\t{line}\n" for line in lines)
    return f"{{\n\tcert_issuer acme {{\n{body}\t}}\n}}\n"


def assemble(snippets: list[tuple[str, str]], dns_provider: str = "") -> str:
    """The Caddyfile for these `(source, snippet)` pairs, in order.

    A source is the stacklet a snippet came from, or `LOCAL_FILE`.

    Raises ValueError for a DNS provider the infra image has no plugin for.
    """
    parts = [_HEADER, _global_options(dns_provider), _UNKNOWN_HOSTS]
    for source, snippet in snippets:
        parts.append(f"# ── {source} ──\n\n{snippet.strip()}\n")
    return "\n".join(parts)
