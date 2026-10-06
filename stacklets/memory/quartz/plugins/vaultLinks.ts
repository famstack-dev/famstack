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

const RELATIVE = /^\.\.?\//

export const VaultLinks: QuartzTransformerPlugin = () => ({
  name: "VaultLinks",
  markdownPlugins() {
    return [
      () => (tree: Root, file) => {
        const dir = path.posix.dirname(file.data.relativePath ?? "")
        visit(tree, "link", (node: Link) => {
          if (!RELATIVE.test(node.url)) return
          const resolved = path.posix.normalize(path.posix.join(dir, node.url))
          if (!resolved.startsWith("..")) node.url = "/" + resolved
        })
      },
    ]
  },
})
