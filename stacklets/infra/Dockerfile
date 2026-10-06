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
# Pinned to the Caddy 2.11 line. Every subdomain reusing the wildcard
# certificate needs 2.10 or later, and hetzner v2 requires 2.11.2.
#
# The plugins are pinned too. Unpinned, xcaddy takes each plugin's newest
# release, and one that requires a newer Caddy breaks the build for every
# install: hetzner v2.0.0 did that to the 2.10 image.

FROM caddy:2.11-builder AS builder
RUN xcaddy build \
    --with github.com/caddy-dns/hetzner/v2@v2.0.1 \
    --with github.com/caddy-dns/cloudflare@v0.2.4

FROM caddy:2.11
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
