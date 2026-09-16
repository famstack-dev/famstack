# Web capture fixtures

Real pages, captured from the live sites the web plan measured.

They are here because a fixture written next to the detector that reads
it proves only that the two were written together. Every claim the gate
makes about how a site blocks is checkable against what the site
actually served.

## Provenance

Captured 2026-09-15 with a Chrome user agent, anonymous (no cookies, no
session), from a German residential IP.

| Fixture | Source | Served |
|---|---|---|
| `cloudflare-challenge.html` | `https://www.decathlon.de/` | HTTP 403, Cloudflare managed challenge, `<title>Just a moment...</title>` |
| `reddit-login-wall.html` | `https://old.reddit.com/r/selfhosted/` | HTTP 302 to `/login/?reason=lor2`, then HTTP 200, `<title>Welcome to Reddit</title>` |
| `google-maps-shell.html` | `https://www.google.com/maps/place/Brandenburger+Tor/` | HTTP 200, application shell, no prose but the site-wide meta description |
| `recipe-jsonld.html` | `https://www.essen-und-trinken.de/rezepte/48816-rzpt-griechischer-salat` | HTTP 200, complete schema.org `Recipe` |
| `shop-listing-ok.html` | `https://geizhals.de/` | HTTP 200, real listing, cookie banner in the same document |

The last one is the negative control: a page carrying a consent banner
that must still pass the gate, because the content is right there.

## What was stripped

Each file had inline `<script>` and `<style>` *bodies* removed, base64
data URIs replaced, and attribute walls over 200 characters (`srcset`,
inline SVG `d=`) truncated. Everything the gate and the structured-data
reader look at is byte-for-byte what the site sent: every tag, every
attribute, every `<meta>`, and every `ld+json` block.

## Known drift

`reddit-login-wall.html` documents a change since the plan was
measured. `old.reddit.com` used to serve 702 KB of readable post to an
anonymous Chrome-UA fetch. It now 302s to a sign-in page — and answers
HTTP 200 with 320 KB and a friendly title, which is precisely why the
gate reads the landing URL rather than the body.

## Adding a site

One fixture plus one test:

```python
def test_newsite_is_a_challenge(self, web_fixture):
    verdict = assess(Page(url="https://newsite.example/", status=403,
                          html=web_fixture("newsite-challenge")))
    assert verdict.name == "challenge"
```

Capture with the shrink step, never hand-write, and add a row above.
