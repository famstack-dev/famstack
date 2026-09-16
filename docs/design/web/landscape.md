# Reading the web in 2026 — landscape

Survey behind `plan.md`, done 2026-09-15. Everything here was checked
against a primary source: a repo's actual LICENSE file, a published
benchmark, a container manifest, or a running instance. Where a claim
comes from a vendor's own marketing it is labelled as such.

## What actually constrains us

In priority order. The first one is the one that kills otherwise-good
options, and it is not the one most write-ups optimise for.

1. **Resident memory.** A family Mac Mini is already running Immich,
   Paperless, Postgres, Synapse and a local model. A fetcher that holds
   a browser resident is competing with photo thumbnailing and OCR for
   RAM, and losing that fight is visible to the family as everything
   getting slow.
2. **Apple Silicon, but arm64 is a preference, not a wall.** This
   survey was carried out treating `linux/arm64` as mandatory and x86 as
   disqualifying. That was too strict: everything here runs in a
   container, and Docker runs `linux/amd64` images on this hardware.
   Emulation costs latency, so measure it rather than assume it. The
   cost lands hardest exactly where it hurts most, on a browser (our
   SeleniumBase measurement of 40s *was* the emulation), and barely at
   all on a fast CPU-bound library that only ships x86 wheels.
   **Anything rejected below purely for lacking arm64 wheels deserves
   re-judging on measured latency.** `rs-trafilatura` was the obvious
   candidate and has been re-judged; see its row. The answer was still
   no, because arm64 was only one of three objections and the weakest.
3. **Nothing hosted.** No reader APIs, no scraper SaaS, no "free tier
   after you sign in with GitHub".
4. **Licence compatible with AGPLv3, distributed freely.** Non-commercial
   riders and BSL are disqualifying.
5. **A local ~30B model.** Anything assuming frontier-model tool-calling
   reliability has to be flagged as such.

**Image size is not a constraint.** A larger image for a genuinely
better fetcher is fine, and the right answer for anything heavy is to
make it an opt-in stacklet so a family that never needs it never pulls
it. This corrects the original plan, which carried a 250 MB image budget
as if it were load-bearing. It never was.

**Caveat on the memory numbers below: they are thin.** The survey
measured image sizes carefully and memory barely at all, because that is
what the ecosystem publishes. Treat every RAM figure here as indicative
and measure before committing. That measurement is the real Phase 4
gate.

## Stealth fetching

The tier 3 decision. We already measured Scrapling `StealthyFetcher` at
3.4s to 19.8s on macOS arm64, and it won our own spike.

| Project | What it is | arm64 | Licence | Latest | Verdict |
|---|---|---|---|---|---|
| **Scrapling** | Parser + 3 fetchers, Turnstile solver built in | wheels + CI + multi-arch image | BSD-3-Clause | v0.4.15, 2026-08-23 | **adopt** |
| **patchright** | Playwright fork patching CDP leaks | aarch64 + macOS arm64 | Apache-2.0 | v1.62.3, 2026-08-17 | **adopt** (transitive) |
| **curl_cffi** | TLS/JA3 impersonating HTTP client | aarch64, musl, macOS arm64 | MIT | v0.16.3, 2026-09-02 | **adopt** |
| Camoufox | Firefox fork, C++-level fingerprint patching | `lin.arm64` yes | MPL-2.0 | v152.0.4-beta.30, 2026-09-01 | **reject**, see below |
| SeleniumBase UC | UC mode wants real Chrome | needs x86 emulation | MIT | v4.54.6, 2026-09-15 | **reject** (we measured 40s) |
| nodriver | Direct CDP, no Playwright layer | - | AGPL-3.0 | no releases, last commit 2026-05-13 | watch |
| zendriver | Maintained nodriver fork | claims Docker | AGPL-3.0 | v0.16.0, 2026-08-16 | watch |
| primp | Rust impersonating client | aarch64 + macOS arm64 | MIT | v2.0.1, 2026-09-12 | watch |
| FlareSolverr | Proxy that solves Cloudflare | - | MIT | v3.5.2, 2026-09-12 | watch (alive again) |
| DrissionPage | CDP automation | - | **non-commercial** | - | **reject** |
| rebrowser-patches | Playwright leak patches | - | **no licence file** | last push 2025-05-09 | **reject** |
| undetected-chromedriver | The original | - | GPL-3.0 | last push 2025-07-05 | **reject** (dead) |

**Scrapling dropped Camoufox entirely at v0.3.13.** `StealthyFetcher`
is now patchright over Playwright Chromium with a built-in Turnstile
solver (`__CF_MAX_SOLVE_ATTEMPTS__ = 3`). The maintainer's stated
reasons were speed, memory and image size. So the plan's original
"consider Camoufox directly if the image is too big" is doubly wrong: it
is no longer the fallback, and image size was never the reason to reach
for it. Camoufox also wants `headless="virtual"` (Xvfb) on Linux, which
adds a process and more RAM, and it went dark for roughly ten months
(2025-03 to 2026-01) before recovering.

Two ceilings worth writing into the Phase 4 gate rather than
discovering in it:

- **`real_chrome` is unavailable in an arm64 image.** Patchright's own
  guidance is to run real Chrome via `channel="chrome"`; Chrome for
  Testing publishes no `linux-arm64`, so Playwright falls back to a
  Chromium `headless_shell`. An `amd64` image under emulation *could*
  run real Chrome, so this is a cost rather than an impossibility — but
  it is the worst possible place to spend that cost, since a browser is
  the one workload where emulation was measured at 40s. Treat the
  weaker Chromium configuration as the working assumption, and if tier 3
  ever proves insufficient, measure the emulated-Chrome path before
  concluding it is closed.
- **Every public stealth benchmark runs headed, on macOS, from a
  residential IP.** We ship headless in a container. Our own numbers are
  worth more than all of them, and the Phase 4 gate should assert
  headless specifically.

**Scrapling's bus factor is 1** (1,544 commits to the next
contributor's 15). Camoufox already demonstrated that failure mode.
Keeping tier 3 behind the injected `Transport` seam is what makes this
survivable: if Scrapling goes quiet for a quarter, reimplementing the
solve loop over patchright is roughly a hundred lines, not a rewrite.

**Memory, such as we know it.** Lightpanda reports 123 MB peak across
100 pages against Chrome's ~2 GB. Firecrawl's shipped compose sets
`mem_limit: 4G` on its Playwright worker, which is the clearest
published signal of what a real browser costs at rest. `headless_shell`
is lighter than full Chromium. None of this is a substitute for
measuring our own container.

## Browser agents

All rejected, for different reasons, and the capability bar is the
reason to be relaxed about it.

| Project | Stars | Licence | Latest | Verdict |
|---|---|---|---|---|
| browser-use | 114.7k | MIT | v0.13.10, 2026-09-04 | reject — steers to its own hosted model |
| Stagehand | 24.3k | MIT | 3.7.3, 2026-08-28 | reject — Browserbase-owned, TypeScript |
| Skyvern | 23.0k | AGPL-3.0 | v1.0.53, 2026-09-09 | reject — anti-bot held back for cloud |
| Notte | 2.0k | **SSPL** | v1.9.0, 2026-09-11 | **reject on licence** |
| LaVague | 6.4k | Apache-2.0 | last push 2025-01-21 | dead |
| Steel | 7.6k | Apache-2.0 | v0.5.4-beta, 2026-08-25 | watch — genuinely multi-arch |
| Playwright MCP | 37.1k | Apache-2.0 | v0.0.81, 2026-09-14 | watch — accessibility tree, no vision |
| Chrome DevTools MCP | 52.1k | Apache-2.0 | v1.9.0, 2026-09-08 | **reject on arm64** (Chrome only) |
| Puppeteer MCP | - | - | - | **archived** |
| Lightpanda | 35.4k | AGPL-3.0 | 0.4.1, 2026-09-15 | watch — tiny RAM, but we measured: no body |

**The capability bar.** ClawBench (arXiv 2604.08523, 153 everyday tasks
across 144 live sites) records a best-ever score of **33.3%**, with the
same models scoring 65 to 75% on traditional web benchmarks. Princeton's
cost-instrumented HAL puts real-web multi-step success at 40 to 42%, at
hundreds of dollars per benchmark run. Steel's own leaderboard shows 97%
— using a custom judge, and their FAQ concedes scores are "not always"
independently verified.

Take **40 to 70%** as the honest band and treat anything above 90% as
harness-and-judge dependent. This is the evidence behind `stack web ask`
being one search plus one model call: nobody has a reliable web agent,
least of all on a local model.

**Local models are not the blocker people assume, if purpose-trained.**
Microsoft's Fara 1.5 (MIT, built on Qwen3.5) scores 72.3% on
Online-Mind2Web at 27B, beating OpenAI Operator's 58.3% and Gemini 2.5
Computer Use's 57.3%, and runs in ~15 GB at INT4 with MLX conversions
already published for the 4B and 9B. But a *general-purpose* 30B MoE is
a different thing: Qwen3.6-35B-A3B publishes strong agentic scores and
**no** WebArena/WebVoyager/OSWorld numbers at all. The gpt-5 to
gpt-5-mini drop of 15.4 points on browser tasks is the cleanest measure
of how sharply this degrades with model class. If browser control ever
matters here, the answer is a purpose-trained model, not a bigger
general one.

## Extraction

**Nothing beats trafilatura inside our constraints.** Everything that
measurably wins is a 0.6B transformer needing 1.5 GB of weights, or
x86-only, or non-commercially licensed.

trafilatura 2.2.0 (2026-07-31) is **Apache-2.0** — relicensed from
GPLv3+ in March 2024, which is worth knowing since older write-ups still
call it GPL.

Read every benchmark here with suspicion: all four major ones are
published by someone shipping a competitor, and trafilatura scores 0.924
on its own eval and 0.6402 on OpenDataLab's. Those are different
questions, not a contradiction. The useful finding across all of them is
that **every heuristic extractor collapses off news articles** — forum
and product pages sit at 0.41 to 0.81 for everyone.

| Candidate | Licence | Verdict |
|---|---|---|
| **trafilatura** | Apache-2.0 | **stay** |
| magic-html | Apache-2.0 | watch — 0.05 MB, second on trafilatura's own bench, worth an A/B |
| resiliparse | Apache-2.0 | watch — ~10x faster, worse output |
| readability-lxml | Apache-2.0 | watch — cheap fallback when trafilatura returns nothing |
| MinerU-HTML / Dripper-0.6B | Apache-2.0 **incl. weights** | watch hard — see below |
| rs-trafilatura | MIT/Apache-2.0 | **reject**, re-judged 2026-09-16; see below |
| ReaderLM-v2 | **CC-BY-NC-4.0** | **reject on licence** |
| Crawl4AI | Apache-2.0 | reject — pulls both playwright and patchright |
| Firecrawl self-host | AGPL-3.0, verified clean | reject — six services, and self-host is explicitly crippled |
| markitdown | MIT | reject — no main-content extraction at all |
| goose3 / newspaper4k | Apache-2.0 / MIT | reject — dominated on quality and speed |
| boilerpy3 | ambiguous | reject — unmaintained since 2023 |

**rs-trafilatura, re-judged after the arm64 constraint was relaxed.**
Still no. Only one of its three disqualifiers was about arm64, and it
was the weakest. Verified 2026-09-16: **v0.1.1 published 2026-03-24,
last push 2026-04-03**, five and a half months dormant, **24 commits
from a single contributor**, 57 stars, and PyPI still ships only
`manylinux_2_34_x86_64`. The benchmark it tops is written by its own
author, who also sells a commercial product built on it.

The deeper reason is that it would optimise the part that already
works. Measured on our own URLs, trafilatura's output is byte-identical
to what we ship for wikipedia, docs and long-form blogs. The one real
extraction failure we found was a recipe whose quantities trafilatura
silently dropped ("g g Tomaten"), and JSON-LD fixed that; a better
heuristic extractor would not have. Everything else that fails is an
access problem, not an extraction problem. Its own claimed gains
concentrate in forum and product pages, where both libraries are
mediocre, and our biggest forum case is reddit, which we cannot fetch
as HTML at all and read through feeds instead.

If extraction quality ever becomes a *measured* problem, start with
**magic-html**: Apache-2.0, 0.05 MB, pure Python over lxml so there is
no wheel question at all, second on trafilatura's own benchmark and
ahead of it on OpenDataLab's. That is a two-hour A/B. Revisit
rs-trafilatura if it gains a second contributor and aarch64 wheels.

**MinerU-HTML** is the one that could displace trafilatura. It reframes
extraction as constrained sequence labeling, so a small model is
genuinely *sufficient* rather than merely cheap — no generative
hallucination. Apache-2.0 code and weights, which is rare. It is 1,503
MB of safetensors with no MLX or GGUF port and the repo has been quiet
since 2026-03-27. Revisit if someone ships a 4-bit MLX conversion.

**One concrete item outside this plan.** `html2text` — used by
`stack.email_message`, not by the web module — is our weakest
dependency: GPL-3.0-or-later (our only copyleft dep), last release
2025-04-15, and the worst F1 in every table. `html-to-markdown` replaces
it at MIT, **zero Python dependencies**, native arm64 wheels, ~7 MB,
released 2026-09-14. Note the org moved to `xberg-io/html-to-markdown`.

## Search

`searxng/searxng` remains the only practical answer. Nobody has solved
an independent index on a 16 GB box: Marginalia wants ~32 GB and
terabytes of crawl storage, Stract is archived, Whoogle is archived, and
YaCy returns results too slow and too strange for question answering.

What matters is operational, and it is covered in
`stacklets/web/config/settings.yml` and the e2e lane. In short: Google
and Bing ship `disabled: true`, Startpage is captcha-walled, only two
engines actually answer, `search.formats` excludes JSON by default,
`number_of_results` lies, `site:` does not pass through, and tags move
several times a day. SearXNG migrated its network layer to `curl_cffi`
on 2026-09-04 for TLS impersonation, which is the biggest structural
anti-bot change of the year and is still unproven.

Answer engines were all rejected as backends: **Perplexica renamed to
Vane** and has had no code commit since 2026-04-11 despite 37k stars,
with a 3.4 GB arm64 image. Morphic is alive but is a chat UI. Scira
mandates a hosted Exa key. Khoj's cloud was sunset and the repo went
quiet with it. Onyx is enterprise RAG at ten times the footprint.
**SurfSense is a licence trap**: Apache-2.0 except the directory
containing its SearXNG connector, which is BSL 1.1.

Of the deep-research agents, `LearningCircuit/local-deep-research` is
the only genuinely local, genuinely alive one, and it is a
single-maintainer project with 682 open issues. `langchain-ai/open_deep_research`
was **archived 2026-08-21**. `HKUDS/Auto-Deep-Research` has no licence
file at all. Its rerank-before-the-model pattern is worth stealing
regardless of the repo's fate.

## Standards

**`llms.txt` is a dud. Stop tracking it.** Across 137,000 domains
surveyed, 97% of `llms.txt` files received zero requests in a month,
only 28% of domains publish one, and 77% of the bots that did fetch it
were not AI tools. No major provider reads it in production.

**JSON-LD is the bet that is working.** Present on about 41% of mobile
pages, up from 34% in 2022, with 958 distinct schema.org types observed
in live markup. This is why tier 1 reads structured data
deterministically and never asks a model, and it is the single most
durable decision in the plan.

**NLWeb** (sites answering natural-language queries over their own
schema.org data) is the one to watch, because it makes a JSON-LD reader
more valuable rather than obsolete. W3C Community Group since October
2025, spec not final.

**WebMCP** is a Chrome origin trial (149 through 156) and is mid-API-
migration already. It is a browser-side JavaScript API, so it does
nothing for a server-side fetcher. Ignore for now.

## The strategic risk

**Cloudflare turned on default AI-crawler blocking on 2026-09-15**, for
all new customers and all existing free customers. The sanctioned
alternative is **Web Bot Auth**: Ed25519-signed HTTP message signatures,
a published JWKS, an application to Cloudflare, and compliance with
their signed-agent policy. Launch partners are OpenAI, Block,
Browserbase and Anchor Browser. Pay-per-crawl returns HTTP 402 with a
`crawler-price` header.

A self-hosted family stack cannot realistically join that programme and
would not want to. So the web is bifurcating into "identify yourself
cryptographically and maybe pay" and "be indistinguishable from a
human browser", and famstack is structurally on the second path. That
validates the stealth tier and makes it a permanent maintenance
treadmill rather than a milestone. Budget for it breaking.

This is also the strongest argument for the parts of the ladder that do
not fetch adversarially at all: structured data the site publishes on
purpose, and feeds it offers on purpose, are not subject to any of this.

## Licence traps, in the PriceBuddy category

Worth listing because an automated licence check catches none of them.

- **Notte** — SSPL. GitHub reports `NOASSERTION`.
- **DrissionPage** — permits personal, learning and non-profit use only,
  stated in Chinese. GitHub reports `NOASSERTION`.
- **rebrowser-patches** — no LICENSE file at all.
- **SurfSense** — Apache-2.0 except `app/proprietary/**`, which is BSL
  1.1, and which is exactly where its SearXNG connector lives.
- **ReaderLM-v2** — CC-BY-NC-4.0, commercial use needs a separate licence.
- **HKUDS/Auto-Deep-Research** — no licence, therefore not redistributable.

## Frontier context, briefly

Useful only as a capability reference. The category consolidated hard:
OpenAI's **Atlas** shipped 2025-10-21 and stopped working 2026-08-09, a
nine-month life; **Operator** was absorbed in 2025; Google's **Project
Mariner** was killed 2026-05-04; **Arc** is feature-dead under Atlassian.
What survived is agentic browsing as a feature inside an existing
surface — an extension or a sidebar — not a browser you install.

On prompt injection the public consensus is that it is **mitigated, not
solved**. OpenAI wrote in December 2025 that it is "unlikely to ever be
fully 'solved'". Anthropic is the only vendor publishing before/after
numbers, and its trajectory is real (23.6% to 11.2% to ~1% to 0-0.3%),
though measured against its own attacker on its own environments. WASP
finds mainstream agents at 16% to 86% attack success. Cloud Security
Alliance reported indirect prompt injection as operational rather than
theoretical by April 2026, with Google seeing a 32% relative increase in
malicious injected content across crawled pages in three months.

The one structural claim with support: systems that carry **provenance
metadata and constrain tool invocation by data origin** are defensible,
while systems treating all retrieved content as undifferentiated prompt
context are not. Every consumer agentic browser shipping today runs the
agent inside the user's authenticated session, which is the vulnerable
shape. Relevant to us mainly as a reason `stack web ask` keeps page
content out of the agent's context entirely.
