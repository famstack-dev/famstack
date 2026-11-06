import fs from "fs"
import path from "path"
import type { QuartzTransformerPlugin } from "../types"

// sourceSenders.ts: who sent each source of a card, and when, on its page.
//
// NEW TRANSFORMER (not an upstream override).
//
// A card made from things people sent ends with `## Sources`, one quote per
// thing, and its sidecar `<stem>.json` beside it holds one entry per quote in
// the same order: quote n belongs to `sources[n]` (ADR-018,
// `lib/stack/cards/sources.py`). Each entry names the person who sent it by
// id and the time it was sent. This transformer puts that above each quote:
//
//     Marge · Nov 4, 2026, 6:20 PM
//     > Packing list for the lake: tent, sleeping bags, marshmallows
//
// The name is the first word of the title of the person's page, linked to it,
// as the wiki names people elsewhere (`personChip` in familyModel.ts). A
// person without a page keeps their id, capitalised, without a link. The
// time is in the wiki's language; a source sent at an unknown time shows
// only the name.
//
// The sidecar itself stays off the site (`ignorePatterns` in
// quartz.config.ts), because it also names rooms, full Matrix ids and
// original file names. Only the first name and the time reach the page, and
// both are what the family already sees in the chat. A card without a
// sidecar, or whose quotes and sources do not pair up, keeps its quotes as
// they are: a line on the wrong quote would name the wrong sender. The file
// in the vault is not touched.
//
// `sourceSenders` is pure: no Quartz imports at run time, so the tests call
// it through Node (`tests/memory/unit/test_wiki_source_senders.py`).

export interface Sender {
  // The first name the wiki uses for the person: "Marge".
  name: string
  // The person's page, as a root path the wiki's link crawler resolves.
  href?: string
  // When the source was sent, in the wiki's language; absent when unknown.
  when?: string
}

// The quotes follow this heading; the memory writes it in English whatever
// the household's language (`SOURCES_HEADING` in sources.py).
const SOURCES_HEADING = "Sources"

export const SourceSenders: QuartzTransformerPlugin = () => ({
  name: "SourceSenders",
  markdownPlugins(ctx) {
    const vault = ctx.argv.directory
    const locale = ctx.cfg.configuration.locale
    return [
      () => (tree: any, file) => {
        const card = file.data.relativePath ?? ""
        const sidecar = readText(path.join(vault, card.replace(/\.md$/, ".json")))
        if (!sidecar) return
        const senders = sourceSenders(String(file.value ?? ""), sidecar,
                                      (id) => pageTitle(vault, id), locale)
        if (senders) signQuotes(tree, senders)
      },
    ]
  },
})

/**
 * The sender of each quote under `## Sources` in the card `markdown`, in
 * order, read from the card's `sidecar` text. `pageTitle(id)` gives the title
 * of the person's page in the wiki, or undefined when they have none.
 *
 * Returns undefined when there is nothing to show safely: no sidecar, a
 * sidecar that is not JSON or has no `sources` list, or a count of sources
 * that differs from the count of quotes. Only the name, the page and the
 * time are returned; nothing else of the sidecar.
 *
 *     sourceSenders(card, '{"sources": [{"person": "marge", "time": "2026-11-04T18:20:00+00:00"}]}',
 *                   (id) => ({ marge: "Marge Simpson" })[id], "en-US")
 *     // → [{ name: "Marge", href: "/marge/about.md", when: "Nov 4, 2026, 6:20 PM" }]
 *     //   for a card with one quote, in the UTC time zone
 */
export function sourceSenders(
  markdown: string,
  sidecar: string | undefined | null,
  pageTitle: (id: string) => string | undefined,
  locale: string,
): Sender[] | undefined {
  const sources = sourcesOf(sidecar)
  if (!sources || sources.length !== quoteCount(markdown)) return undefined
  const timeFormat = new Intl.DateTimeFormat(locale, TIME_STYLE)
  return sources.map((s) => senderOf(s, pageTitle, timeFormat))
}

// ── Reading the sidecar ──────────────────────────────────────────────────

type SourceRecord = { person?: unknown; time?: unknown }

/** The sidecar's `sources` list, or undefined for a missing or broken sidecar. */
function sourcesOf(sidecar: string | undefined | null): SourceRecord[] | undefined {
  if (!sidecar) return undefined
  try {
    const sources = JSON.parse(sidecar)?.sources
    return Array.isArray(sources) ? sources : undefined
  } catch {
    return undefined
  }
}

/** How many quotes the card has under `## Sources`, as `quotes_of` in sources.py counts them. */
function quoteCount(markdown: string): number {
  const lines = markdown.split("\n")
  const start = lines.indexOf(`## ${SOURCES_HEADING}`)
  if (start < 0) return 0
  let count = 0
  let inside = false
  for (const line of lines.slice(start + 1)) {
    if (/^##\s+\S/.test(line)) break
    const quoted = line.startsWith(">")
    if (quoted && !inside) count++
    inside = quoted
  }
  return count
}

// ── Naming the sender ────────────────────────────────────────────────────

// A day and a minute, the way the wiki writes a date elsewhere (`fmtDate`).
const TIME_STYLE: Intl.DateTimeFormatOptions = {
  day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit",
}

function senderOf(
  source: SourceRecord,
  pageTitle: (id: string) => string | undefined,
  timeFormat: Intl.DateTimeFormat,
): Sender {
  const id = String(source.person ?? "")
  const title = id ? pageTitle(id) : undefined
  const sender: Sender = title
    ? { name: title.split(" ")[0], href: `/${id}/about.md` }
    : { name: id.charAt(0).toUpperCase() + id.slice(1) }
  const sent = new Date(String(source.time ?? ""))
  if (!Number.isNaN(sent.getTime())) sender.when = timeFormat.format(sent)
  return sender
}

// ── Reading the vault ────────────────────────────────────────────────────

function readText(file: string): string | undefined {
  try {
    return fs.readFileSync(file, "utf-8")
  } catch {
    return undefined
  }
}

/** The `title` of a person's page `<id>/about.md`, or undefined when there is none. */
function pageTitle(vault: string, id: string): string | undefined {
  if (!/^[a-z0-9_-]+$/i.test(id)) return undefined
  const page = readText(path.join(vault, id, "about.md"))
  const title = page?.match(/^---\n[\s\S]*?^title:\s*(.+?)\s*$/m)?.[1]
  return title?.replace(/^["']|["']$/g, "") || undefined
}

// ── Signing the quotes ───────────────────────────────────────────────────

/**
 * Puts a line above each quote under the `Sources` heading of the parsed
 * card. Changes nothing when the parsed quotes are not exactly one per
 * sender, for the same reason `sourceSenders` refuses a mismatch.
 */
function signQuotes(tree: any, senders: Sender[]): void {
  const nodes: any[] = tree.children
  const heading = nodes.findIndex(
    (n) => n.type === "heading" && n.depth === 2 && textOf(n) === SOURCES_HEADING,
  )
  if (heading < 0) return
  const quotes: number[] = []
  for (let i = heading + 1; i < nodes.length; i++) {
    if (nodes[i].type === "heading" && nodes[i].depth <= 2) break
    if (nodes[i].type === "blockquote") quotes.push(i)
  }
  if (quotes.length !== senders.length) return
  // From the last quote back, so the earlier indexes stay valid.
  for (let q = quotes.length - 1; q >= 0; q--) nodes.splice(quotes[q], 0, senderLine(senders[q]))
}

function senderLine(sender: Sender): any {
  const name = { type: "text", value: sender.name }
  const children: any[] = [sender.href ? { type: "link", url: sender.href, children: [name] } : name]
  if (sender.when) children.push({ type: "text", value: ` · ${sender.when}` })
  return { type: "paragraph", data: { hProperties: { className: ["source-sender"] } }, children }
}

function textOf(node: any): string {
  return (node.children ?? []).map((c: any) => c.value ?? textOf(c)).join("")
}
