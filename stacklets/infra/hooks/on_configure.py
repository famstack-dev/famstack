"""First run: say what infra takes on, then ask for the DNS token if needed.

Infra is DNS and the entry point for the whole home network, which no
other stacklet is, so its first setup says so once, as a form that needs a
yes (ADR-021): in a terminal a question, without one
`--answer understood=yes`. A no installs nothing.

With `[core] dns_provider` set and no token stored, the provider's API
token is asked next, as a secret: read without echo in a terminal, from
`--answers <file>` or stdin otherwise, and kept only in the secret store.
`stack infra dns-token` replaces it later; on_start stops a start that
has none.
"""

from stack.forms import Form
from stack.hooks import Cancelled

from ..dns_token import TOKEN_KIND

NETWORK_WARNING = (
    "Infrastructure runs DNS and the entry point for your whole network. "
    "Once your router hands out this Mac as its DNS server, every device "
    "depends on it: when the Mac or AdGuard is down, nothing on the network "
    "resolves names. Setting it up takes some networking knowledge (your "
    "router's DHCP and DNS settings, DNS records, a domain at a DNS provider "
    "for HTTPS). Read stacklets/infra/README.md first, and note your router's "
    "current DNS setting so you can go back to it."
)

NETWORK = Form(
    id="infra.network",
    title="Infrastructure",
    text=NETWORK_WARNING,
    schema={
        "type": "object",
        "properties": {
            "understood": {
                "type": "boolean",
                "title": "Set up DNS and the entry point for the whole network?",
                "default": False,
            },
        },
        "required": ["understood"],
    },
)


def _token_form(provider: str) -> Form:
    kind = TOKEN_KIND.get(provider, "an API token")
    return Form(
        id="infra.dns-token",
        title="DNS provider token",
        text=(f"HTTPS through {provider} needs {kind}. Caddy uses it to prove "
              "you own the domain when it asks Let's Encrypt for certificates. "
              "It is kept in the stack's secret store, never in stack.toml."),
        schema={
            "type": "object",
            "properties": {
                "dns_token": {
                    "type": "string",
                    "title": "DNS API token",
                    "writeOnly": True,
                    "pattern": r"\S",
                    "x-pattern-error": "The token is empty.",
                },
            },
            "required": ["dns_token"],
        },
    )


def questions(ctx, answers):
    if ctx.stack._is_first_run(ctx.stacklet_id) and (open_form := NETWORK.pending(answers)):
        return open_form
    if answers.get("understood") is False:
        return None
    provider = ctx.stack._cfg("core", "dns_provider")
    if provider and not ctx.secret("DNS_API_TOKEN"):
        return _token_form(provider).pending(answers)
    return None


def apply(ctx, answers):
    if answers.get("understood") is False:
        raise Cancelled("Cancelled. Infrastructure was not set up.")
    if "dns_token" in answers:
        token = answers["dns_token"].strip()
        ctx.secret("DNS_API_TOKEN", token)
        # The token itself is never printed. Its length tells a complete
        # paste from a truncated one.
        ctx.step(f"DNS API token stored ({len(token)} characters)")
