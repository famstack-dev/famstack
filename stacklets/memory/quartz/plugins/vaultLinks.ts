import { QuartzTransformerPlugin } from "../types"
import { Root, Link } from "mdast"
import { visit, SKIP } from "unist-util-visit"
import fs from "fs"
import path from "path"
import { shown, vaultPathOf, viewOf } from "../../components/vaultPaths"

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
// as it is. A link to a person, topic or correspondent card goes to the
// view compiled from it (`viewOf` in components/vaultPaths.ts).
//
// The wiki holds only the family's shared part, so a link into a person's
// bucket would open a missing page: the home page's line for each member
// (`[Homer](homer/about)`), a person card's link, a link to someone's own
// note. Such a link becomes its text. The wiki's listener serves no
// `/go` links, so there is no memory page to send it to from here.
//
// Core's `/go/card/<card_id>` lands on the card's own page (`/card/<card_id>`),
// which links this view.

const RELATIVE = /^\.\.?\//

export const VaultLinks: QuartzTransformerPlugin = () => ({
  name: "VaultLinks",
  markdownPlugins(ctx) {
    // Asked per link, not once per build: `--serve` keeps this plugin while
    // the curator adds a top folder (the media, on its first photo).
    const holds = (top: string) => fs.existsSync(path.join(ctx.argv.directory, top))
    return [
      () => (tree: Root, file) => {
        const dir = path.posix.dirname(file.data.relativePath ?? "")
        visit(tree, "link", (node: Link, index, parent) => {
          const target = vaultPathOf(node.url, dir)
          if (target === undefined) return
          if (!shown(viewOf(target), holds) && parent && index !== undefined) {
            parent.children.splice(index, 1, ...node.children)
            return [SKIP, index]
          }
          if (!RELATIVE.test(node.url)) return
          const resolved = path.posix.normalize(path.posix.join(dir, node.url))
          if (!resolved.startsWith("..")) node.url = "/" + viewOf(resolved)
        })
      },
    ]
  },
})
