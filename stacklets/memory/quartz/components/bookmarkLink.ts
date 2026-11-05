// bookmarkLink.ts: the page a bookmark card points to, as the wiki shows it.
//
//     bookmarkLink({ type: "bookmark", resource: "https://www.simpsons.example/wiki/The_Simpsons" })
//     // → { site: "simpsons.example",
//     //     address: "simpsons.example/wiki/The_Simpsons",
//     //     href: "https://www.simpsons.example/wiki/The_Simpsons" }
//
// NEW MODULE (not an upstream override). A bookmark card keeps the page it
// bookmarked in its frontmatter field `resource` (written by the archivist,
// `stacklets/docs/bot/vault_entry.py`). The bookmarked page is what a
// bookmark is for, so its wiki page shows that page first (`BookmarkTarget`),
// by its site and a short form of its address. The wiki's lists name the
// site the same way (`familyModel.ts`, `describe`).
//
// The card holds no title of the bookmarked page itself: its `title` is
// the classifier's name for the card, which the page head already shows.
// So the site is the name of the page here.
//
// Pure: no Quartz imports at run time, so the tests call it through Node
// (`tests/memory/unit/test_wiki_bookmark_link.py`).

export interface BookmarkTarget {
  // The host without `www.`: "en.wikipedia.org".
  site: string
  // Host and path as a person reads them, decoded and cut to ADDRESS_CHARS.
  address: string
  // The address as filed, for the link.
  href: string
}

// About one line of the block on a laptop, where most addresses of an
// article fit whole; a phone wraps it. The full address is in the link.
const ADDRESS_CHARS = 72

/**
 * The page a card points to, or undefined for any card that is not a
 * bookmark and for a bookmark without a web address. Only http and https
 * are shown, because the value becomes an `href` and a `javascript:`
 * address must never be clickable.
 */
export function bookmarkLink(frontmatter: Record<string, unknown> | undefined): BookmarkTarget | undefined {
  if (frontmatter?.type !== "bookmark") return undefined
  const href = String(frontmatter.resource ?? "")
  const url = webAddress(href)
  if (!url) return undefined
  const site = url.hostname.replace(/^www\./, "")
  return { site, address: shortAddress(site, url), href }
}

// ── Reading the address ──────────────────────────────────────────────────

/** The parsed address when it is an http(s) address with a host, else undefined. */
function webAddress(address: string): URL | undefined {
  try {
    const url = new URL(address)
    const web = url.protocol === "http:" || url.protocol === "https:"
    return web && url.hostname ? url : undefined
  } catch {
    return undefined
  }
}

/**
 * `en.wikipedia.org/wiki/List_of_The_Simpsons_episodes_(seasons_1–20)`:
 * the site, the path and the query, without the scheme, a trailing slash
 * or the fragment. Percent-escapes are decoded, so an address reads as the
 * browser's address bar shows it. Longer than ADDRESS_CHARS, it is cut and
 * ends in "…".
 */
function shortAddress(site: string, url: URL): string {
  const rest = url.pathname.replace(/\/$/, "") + url.search
  const full = site + readable(rest)
  return full.length > ADDRESS_CHARS ? `${full.slice(0, ADDRESS_CHARS - 1)}…` : full
}

function readable(text: string): string {
  try {
    return decodeURIComponent(text)
  } catch {
    return text
  }
}
