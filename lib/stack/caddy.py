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
file, so a change of the domain needs no new file.

A snippet may also name the stack's template variables, the ones
`[env.defaults]` uses, for a route that depends on a setting. The ai
stacklet's routes go wherever `[ai]` points:

    openai.{$STACK_DOMAIN} {
        reverse_proxy {ai_openai_upstream} {
            header_up Host {upstream_hostport}
        }
    }

`fill` replaces `{ai_openai_upstream}` with `http://host.docker.internal:8888`
for `[ai] openai_url = "http://localhost:8888/v1"`. Only a `{name}` that
is a template variable is replaced, outside comments; Caddy's own
placeholders (`{host}`, `{upstream_hostport}`, `{env.X}`) stay for Caddy.
So the file is a function of which stacklets are up and of the stack's
settings.

A snippet that cannot be filled (a route to an address that is no usable
upstream, or to the proxy's own site) is refused with `UnfillableSnippet`
and left out by the caller with a warning. Caddy refuses a whole file for
one bad site, and one stacklet must not take the other routes down. A
snippet that needs a variable that is empty is refused with `NotSet`, a
kind of `UnfillableSnippet`: the setting it routes to is not made, so the
caller leaves it out without a warning.
"""
from __future__ import annotations

import re
import urllib.parse

from .host_address import for_containers, usable

# The stacklet that runs the proxy, and where it expects its config.
STACKLET = "infra"
CONTAINER = "stack-infra-caddy"
CONFIG_IN_CONTAINER = "/etc/caddy/Caddyfile"

RELOAD_COMMAND = ("caddy", "reload", "--config", CONFIG_IN_CONTAINER)

# `{name}` of a template variable. `{$STACK_DOMAIN}` and `{env.X}` do not
# match, so Caddy's environment placeholders never need an exception.
_VARIABLE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# The admin's own sites, next to the assembled Caddyfile, appended after
# the stacklets' snippets.
LOCAL_FILE = "Caddyfile.local"

_HEADER = """\
# Assembled by the stack CLI from the caddy.snippet of every stacklet that
# is up, and Caddyfile.local next to this file. It is rewritten whenever a
# stacklet starts or stops and when the AI server changes, so an edit here
# does not survive: put your own
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


class UnfillableSnippet(ValueError):
    """A snippet that would make a broken site; its message says why."""


class NotSet(UnfillableSnippet):
    """A snippet that needs a template variable that is empty: there is
    nothing to route to yet, which is no fault.

        try:
            fill(snippet, {"ai_openai_upstream": ""})
        except NotSet:
            pass    # left out, without a warning
    """


def upstream(url: str) -> str:
    """The address the proxy forwards to for a client's `url`, or "".

    Scheme, host and port only: Caddy refuses an upstream with a path, and
    the client's own path (`/v1/...`) passes through as it was sent. A
    server on the Mac's loopback is reached through Docker's host gateway
    (`stack.host_address`). An empty `url` gives "", the address is not
    set. An unusable one comes back as it was, so that `fill` refuses it
    by name.

        >>> upstream("http://localhost:8888/v1")
        'http://host.docker.internal:8888'
        >>> upstream("https://ai.example.lan/v1")
        'https://ai.example.lan'
        >>> upstream("http://ai.lan:abc/v1")
        'http://ai.lan:abc/v1'
    """
    if not usable(url):
        return url.strip()
    address = for_containers(url)
    parts = urllib.parse.urlsplit(address)
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}"


def fill(snippet: str, variables: dict[str, str]) -> str:
    """The snippet with every `{name}` of a template variable filled in.

    Comment lines are copied as they are, and so is a `{name}` that is no
    template variable: those are Caddy's placeholders. A snippet without
    template variables comes back unchanged.

    Raises NotSet when a variable it uses is empty. Raises
    UnfillableSnippet when a `reverse_proxy` would forward to an address
    that is no usable http or https address (`stack.host_address.usable`),
    or to one of the snippet's own sites, which loops
    (`variables["domain"]` names the domain).

    With `{"language": "de"}`, the line

        respond "{host} {language}" 200

    becomes `respond "{host} de" 200`: `{host}` is Caddy's.
    """
    filled = "".join(_fill_line(line, variables)
                     for line in snippet.splitlines(keepends=True))
    _refuse_a_loop(filled, variables.get("domain", ""))
    return filled


def assemble(snippets: list[tuple[str, str]], dns_provider: str = "") -> str:
    """The Caddyfile for these `(source, snippet)` pairs, in order.

    A source is the stacklet a snippet came from, or `LOCAL_FILE`.

    Raises ValueError for a DNS provider the infra image has no plugin for.
    """
    parts = [_HEADER, _global_options(dns_provider), _UNKNOWN_HOSTS]
    for source, snippet in snippets:
        parts.append(f"# ── {source} ──\n\n{snippet.strip()}\n")
    return "\n".join(parts)


# ── Filling a snippet ─────────────────────────────────────────────────────


def _fill_line(line: str, variables: dict[str, str]) -> str:
    """One line of a snippet, filled; a comment line stays as it is."""
    if line.lstrip().startswith("#"):
        return line

    def value(match: re.Match) -> str:
        name = match.group(1)
        if name not in variables:
            return match.group(0)
        filled = str(variables[name])
        if not filled:
            raise NotSet(f"{{{name}}} is empty")
        # An upstream that is no address would reach Caddy's file, where a
        # brace starts a placeholder and a space ends the token.
        if line.split()[:1] == ["reverse_proxy"] and not usable(filled):
            raise UnfillableSnippet(f"{{{name}}} is no usable address: {filled}")
        return filled

    return _VARIABLE.sub(value, line)


def _refuse_a_loop(snippet: str, domain: str) -> None:
    """Refuse a snippet whose `reverse_proxy` names one of its own sites,
    in any case and with or without a trailing dot."""
    # Host names depend neither on case nor on a trailing dot (a fully
    # qualified name); urlsplit lowercases the target's.
    sites = {address.replace("{$STACK_DOMAIN}", domain).lower().rstrip(".")
             for address in _site_addresses(snippet)}
    for target in _upstreams(snippet):
        host = urllib.parse.urlsplit(target if "://" in target else f"//{target}").hostname
        if (host or "").rstrip(".") in sites:
            raise UnfillableSnippet(
                f"it would forward to {target}, the proxy's own site")


def _site_addresses(snippet: str) -> set[str]:
    """The addresses of the snippet's top-level sites: unindented lines
    that open a block."""
    addresses = set()
    for line in snippet.splitlines():
        if line[:1] in ("", " ", "\t", "#") or not line.rstrip().endswith("{"):
            continue
        addresses.update(a.strip() for a in line.rstrip()[:-1].split(",") if a.strip())
    return addresses


def _upstreams(snippet: str) -> list[str]:
    """The upstreams of every `reverse_proxy`; matchers and braces skipped."""
    targets = []
    for line in snippet.splitlines():
        tokens = line.split("#", 1)[0].split()
        if tokens[:1] != ["reverse_proxy"]:
            continue
        targets += [t for t in tokens[1:] if not t.startswith(("/", "@", "{"))]
    return targets
