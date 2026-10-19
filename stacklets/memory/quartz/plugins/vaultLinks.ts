import { QuartzTransformerPlugin } from "../types"
import { Root, Link } from "mdast"
import { visit } from "unist-util-visit"
import path from "path"

// NEW TRANSFORMER (not an upstream override).
//
// The vault writes links between its own files relative to the file,
// `[Springfield Power](../../../correspondents/springfield-power.md)`,
// because that is the form Forgejo and Obsidian resolve. CrawlLinks runs
// with markdownLinkResolution "absolute" (see quartz.config.ts) and reads
// every link as a path from the vault root, `..` included, so such a link
// lands one or more levels too high and 404s. This resolves each relative
// link against its file's directory first and hands CrawlLinks the
// root path it expects. A link that would climb out of the vault is left
// as it is.
//
// A record links a card's own file (ADR-017 §3). Two kinds of card are
// not pages on the site but are read through the view compiled from
// them, and this is the one place that maps the card to its view:
//
//   family/people/homer.md                 -> homer/about.md
//   family/itchy-scratchy-land/topic.md    -> family/itchy-scratchy-land/about.md
//
// A correspondent's view shares its card's path, so it needs no mapping.

const RELATIVE = /^\.\.?\//
const PERSON_CARD = /^[^/]+\/people\/([^/]+)\.md$/
const TOPIC_CARD = /^([^/]+\/[^/]+)\/topic\.md$/

export function viewOf(card: string): string {
  const person = PERSON_CARD.exec(card)
  if (person) return `${person[1]}/about.md`
  const topic = TOPIC_CARD.exec(card)
  if (topic) return `${topic[1]}/about.md`
  return card
}

export const VaultLinks: QuartzTransformerPlugin = () => ({
  name: "VaultLinks",
  markdownPlugins() {
    return [
      () => (tree: Root, file) => {
        const dir = path.posix.dirname(file.data.relativePath ?? "")
        visit(tree, "link", (node: Link) => {
          if (!RELATIVE.test(node.url)) return
          const resolved = path.posix.normalize(path.posix.join(dir, node.url))
          if (!resolved.startsWith("..")) node.url = "/" + viewOf(resolved)
        })
      },
    ]
  },
})
