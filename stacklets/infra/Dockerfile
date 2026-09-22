# Caddy with the DNS provider plugins `[core] dns_provider` can name.
#
# A certificate through a DNS-01 challenge, and so the wildcard, needs the
# provider's plugin compiled into the binary; the stock image has none.
# One image carries every supported provider, so switching providers is a
# config change, not a rebuild. Supported names: lib/stack/caddy.py.
#
# The hetzner module is v2, which talks to the Hetzner Cloud API. v1 used
# the DNS Console API, which Hetzner has deprecated.
#
# Pinned to the Caddy 2.10 line. Every subdomain reusing the wildcard
# certificate needs 2.10 or later.

FROM caddy:2.10-builder AS builder
RUN xcaddy build \
    --with github.com/caddy-dns/hetzner/v2 \
    --with github.com/caddy-dns/cloudflare

FROM caddy:2.10
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
