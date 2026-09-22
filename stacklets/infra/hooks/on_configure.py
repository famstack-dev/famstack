"""First run: say what infra takes on, then ask for the DNS token if needed.

Infra is DNS and the entry point for the whole home network, which no
other stacklet is, so its first `stack up` says so once, through the
same warning channel as a stacklet's stage.

The DNS provider's API token is asked for only in a terminal. Anywhere
else on_start stops the start and names the command that stores the
token, which is also how it is replaced later.
"""

import sys

NETWORK_WARNING = (
    "Infrastructure runs DNS and the entry point for your whole network. "
    "Once your router hands out this Mac as its DNS server, every device "
    "depends on it: when the Mac or AdGuard is down, nothing on the network "
    "resolves names. Setting it up takes some networking knowledge (your "
    "router's DHCP and DNS settings, DNS records, a domain at a DNS provider "
    "for HTTPS). Read stacklets/infra/README.md first, and note your router's "
    "current DNS setting so you can go back to it."
)


def run(ctx):
    ctx.warn(NETWORK_WARNING)

    if not ctx.stack._cfg("core", "dns_provider") or ctx.secret("DNS_API_TOKEN"):
        return
    if not sys.stdin.isatty():
        return

    result = ctx.stack.run_cli_command("infra", "dns-token") or {}
    if "error" in result:
        raise RuntimeError(result["error"])
    ctx.step(result["stored"])
