"""First run: ask for the DNS provider's API token when HTTPS is configured.

Only in a terminal. Anywhere else on_start stops the start and names the
command that stores the token, which is also how it is replaced later.
"""

import sys


def run(ctx):
    if not ctx.stack._cfg("core", "dns_provider") or ctx.secret("DNS_API_TOKEN"):
        return
    if not sys.stdin.isatty():
        return

    result = ctx.stack.run_cli_command("infra", "dns-token") or {}
    if "error" in result:
        raise RuntimeError(result["error"])
    ctx.step(result["stored"])
