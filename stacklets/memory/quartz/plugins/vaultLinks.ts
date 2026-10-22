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
// A record links a card's own file (ADR-017 §3). Three kinds of card are
// not pages on the site but are read through the view compiled from
// them, and this is the one place that maps the card to its view:
//
//   family/people/homer.md                 -> homer/about.md
//   family/itchy-scratchy-land/topic.md    -> family/itchy-scratchy-land/about.md
//   family/correspondents/kwik-e-mart.md   -> family/correspondents/kwik-e-mart/about.md
//
// Core's `/go/<card_id>` lands a link to the same cards on the same views
// (`card_target` in core's tools-server/resolver.py).

const RELATIVE = /^\.\.?\//
const PERSON_CARD = /^[^/]+\/people\/([^/]+)\.md$/
const TOPIC_CARD = /^([^/]+\/[^/]+)\/topic\.md$/
const CORRESPONDENT_CARD = /^([^/]+\/correspondents\/[^/]+)\.md$/

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
