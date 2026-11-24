// vaultPaths.ts: where a link in the vault points, and whether the wiki holds it.
//
//     vaultPathOf("../people/homer.md", "family/notes")   // → "family/people/homer.md"
//     vaultPathOf("homer/about", "")                       // → "homer/about"
//     vaultPathOf("https://example.org/a", "family")       // → undefined
//     viewOf("family/people/homer.md")                     // → "homer/about.md"
//     shown("homer/about.md", (top) => top === "family")   // → false
//
// NEW MODULE (not an upstream override). The wiki renders only the family's
// shared part of the memory (the curator's `bot/wiki_input.py`): the top
// level and the shared bucket. A page there can still link into a person's
// bucket: the home page names each member with a link to their page, and a
// person card's link maps to that member page. Such a page is not in the
// build, so the link would open a missing page. VaultLinks turns it into
// plain text with these functions.
//
// The test is the content folder itself: a link is shown when its first
// folder is in the folder Quartz builds from. No list of people is needed,
// and a person added later is handled the same way.
//
// Pure: no Quartz imports at run time, so the tests call it through Node
// (`tests/memory/unit/test_wiki_links_into_a_persons_part.py`).

import path from "path"

const RELATIVE = /^\.\.?\//
// A link with a scheme (`https:`, `mailto:`) leaves the vault.
const SCHEME = /^[a-z][a-z0-9+.-]*:/i

// A record links a card's own file (ADR-017 §3). Three kinds of card are
// not pages on the site but are read through the view compiled from them:
//
//   family/people/homer.md                 -> homer/about.md
//   family/itchy-scratchy-land/topic.md    -> family/itchy-scratchy-land/about.md
//   family/correspondents/kwik-e-mart.md   -> family/correspondents/kwik-e-mart/about.md
const PERSON_CARD = /^[^/]+\/people\/([^/]+)\.md$/
const TOPIC_CARD = /^([^/]+\/[^/]+)\/topic\.md$/
const CORRESPONDENT_CARD = /^([^/]+\/correspondents\/[^/]+)\.md$/

/** The page that shows `card` (a vault path): its view, or the card itself. */
export function viewOf(card: string): string {
  const person = PERSON_CARD.exec(card)
  if (person) return `${person[1]}/about.md`
  // Before the topic: `correspondents/` is never a topic's folder, and a
  // correspondent slugged `topic` would otherwise read as one's card.
  const correspondent = CORRESPONDENT_CARD.exec(card)
  if (correspondent) return `${correspondent[1]}/about.md`
  const topic = TOPIC_CARD.exec(card)
  if (topic) return `${topic[1]}/about.md`
  return card
}

/**
 * The vault path a link `url` on a page in folder `dir` points to, without
 * its fragment or query; undefined for a link that leaves the vault (a
 * scheme, a fragment alone, a relative path that climbs out).
 *
 * A path without `./` or `../` is read from the vault's root, as CrawlLinks
 * reads it (`markdownLinkResolution: "absolute"`).
 */
export function vaultPathOf(url: string, dir: string): string | undefined {
  const bare = url.split(/[#?]/)[0]
  if (!bare || SCHEME.test(bare)) return undefined
  const joined = RELATIVE.test(bare) ? path.posix.join(dir, bare) : bare.replace(/^\/+/, "")
  const resolved = path.posix.normalize(joined)
  if (resolved.startsWith("..") || resolved === ".") return undefined
  return resolved
}

/**
 * Whether the page at vault path `page` can be in the wiki: a top-level
 * file, or a path whose first folder `holds` says the wiki's content has.
 */
export function shown(page: string, holds: (topFolder: string) => boolean): boolean {
  const slash = page.indexOf("/")
  return slash < 0 || holds(page.slice(0, slash))
}
