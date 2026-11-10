import type { QuartzTransformerPlugin } from "../types"

// NEW TRANSFORMER (not an upstream override).
//
// A card's page is what a chat client previews when the archivist links a
// filed card (`/go/card/<card id>` redirects here). Quartz's description is the
// start of the page's text, and a card starts with its meta block, so the
// preview would read "Class 4b Parent Meeting About Bart Started 2026-11-05
// · Kind email 2026-11-05 · office@…". The preview reads the card's summary
// instead: the first paragraph of its
// `> [!summary]` callout, or, for a card without one (a note), its first
// paragraph of plain text. A page that has neither keeps Quartz's own.
//
// It sets `socialDescription`, which only the page's <meta> tags read
// (components/Head.tsx), so search and the page itself are unchanged. The
// file in the vault is not touched.
//
//     cardDescription("---\ntype: email\n---\n# Trip\n\n> [!summary]\n> Trip on Friday.\n")
//     // → "Trip on Friday."

// Long enough for two sentences; a preview shows about three lines.
const MAX_CHARS = 300

export const CardSummary: QuartzTransformerPlugin = () => ({
  name: "CardSummary",
  markdownPlugins() {
    return [
      () => (_tree, file) => {
        const fm = file.data.frontmatter
        if (!fm || fm.socialDescription) return
        const description = cardDescription(String(file.value ?? ""))
        if (description) fm.socialDescription = description
      },
    ]
  },
})

/** The text a link preview shows for the card `markdown`, or undefined when it has none. */
export function cardDescription(markdown: string): string | undefined {
  const lines = withoutFrontmatter(markdown).split("\n")
  const text = summaryOf(lines) ?? firstPlainParagraph(lines)
  return text ? shortened(plain(text)) : undefined
}

// ── Finding the text ─────────────────────────────────────────────────────

const SUMMARY_CALLOUT = /^>\s*\[!summary\][-+]?/i

/** The first paragraph inside the `> [!summary]` callout, if the card has one. */
function summaryOf(lines: string[]): string | undefined {
  const start = lines.findIndex((l) => SUMMARY_CALLOUT.test(l))
  if (start < 0) return undefined
  const paragraph: string[] = []
  for (const line of lines.slice(start + 1)) {
    if (!line.startsWith(">")) break
    const inner = line.replace(/^>\s?/, "").trim()
    if (!inner) {
      if (paragraph.length) break
      continue
    }
    // The callout's facts and action items are lists under a bold label.
    if (inner.startsWith("**") || /^[-*+] /.test(inner)) break
    paragraph.push(inner)
  }
  return paragraph.length ? paragraph.join(" ") : undefined
}

/** The first paragraph that is prose: no heading, quote, list, table or comment. */
function firstPlainParagraph(lines: string[]): string | undefined {
  const paragraph: string[] = []
  for (const line of lines) {
    const trimmed = line.trim()
    const prose = trimmed && !/^(#|>|<!--|[-*+] |\||!\[)/.test(trimmed)
    if (prose) paragraph.push(trimmed)
    else if (paragraph.length) break
  }
  return paragraph.length ? paragraph.join(" ") : undefined
}

function withoutFrontmatter(markdown: string): string {
  if (!markdown.startsWith("---\n")) return markdown
  const end = markdown.indexOf("\n---\n", 3)
  return end < 0 ? markdown : markdown.slice(end + 5)
}

// ── Making it plain ──────────────────────────────────────────────────────

/** Links as their labels, emphasis without its marks. */
function plain(text: string): string {
  return text
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2")
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/(\*\*|__|\*|_|`)(.+?)\1/g, "$2")
    .replace(/\s+/g, " ")
    .trim()
}

function shortened(text: string): string {
  if (text.length <= MAX_CHARS) return text
  return text.slice(0, MAX_CHARS).replace(/\s+\S*$/, "") + " …"
}
