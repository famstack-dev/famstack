"""The Caddyfile for domain mode, assembled from stacklet snippets.

In domain mode every service is reached by name (`photos.<domain>`)
through one reverse proxy, the Caddy container of the proxy stacklet.
A stacklet that serves something ships its routes as a `caddy.snippet`;
this module joins the snippets of the stacklets that are up into the one
Caddyfile that container mounts.

Snippets describe routes only. Everything that applies to the proxy as a
whole, which today is the catch-all for hosts no stacklet claims, is
emitted here, so a snippet never has to know about any other.

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

_HEADER = """\
# Assembled by the stack CLI from the caddy.snippet of every stacklet that
# is up. It is rewritten whenever a stacklet starts or stops, so an edit
# here does not survive: change the stacklet's snippet instead.
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

# A host that no running stacklet claims, including one whose stacklet is
# stopped, gets an answer instead of a connection error.
_UNKNOWN_HOSTS = """\
*.{$STACK_DOMAIN} {
\trespond "No service at {host}" 404
}
"""


def assemble(snippets: list[tuple[str, str]]) -> str:
    """The Caddyfile for these `(stacklet_id, snippet)` pairs, in order."""
    parts = [_HEADER, _PLAIN_HTTP, _UNKNOWN_HOSTS]
    for stacklet_id, snippet in snippets:
        parts.append(f"# ── {stacklet_id} ──\n\n{snippet.strip()}\n")
    return "\n".join(parts)
