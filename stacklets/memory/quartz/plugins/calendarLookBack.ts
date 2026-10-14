import fs from "fs"
import path from "path"
import { QuartzTransformerPlugin } from "../types"
import { L } from "../../components/familyModel"

// NEW TRANSFORMER (not an upstream override).
//
// A calendar list keeps only what is ahead: the night after its date an
// entry moves to the list's archive, `<bucket>/lists/archive/<list>/<yyyy-mm>.md`.
// "The dentist was on Tuesday, wasn't it?" is still a fair question, so a
// calendar's page ends with the entries of the last seven days, read from
// the archive and greyed out. The list file itself is not touched.
//
// "Today" is the household's (TZ is set on the wiki container). The page
// is rebuilt when the list changes and on the nightly sweep, which is
// also when entries move to the archive, so the window stays current.

const LOOK_BACK_DAYS = 7
const ENTRY = /^- \[[ xX]\] (.*)$/
const DATED = /📅\s*(\d{4}-\d{2}-\d{2})/
const LINK = /\[([^\]]+)\]\([^)]*\)/g
const MARKS = /\s*(📅|✅)\s*\d{4}-\d{2}-\d{2}/g

type Past = { text: string; due: string }

/** YYYY-MM-DD of `d` in the container's timezone. */
const day = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`

/** The archive's entries dated in [from, to), newest first. */
function pastEntries(archiveDir: string, months: string[], from: string, to: string): Past[] {
  const found: Past[] = []
  for (const month of months) {
    let text: string
    try {
      text = fs.readFileSync(path.join(archiveDir, `${month}.md`), "utf-8")
    } catch {
      continue
    }
    for (const line of text.split("\n")) {
      const entry = ENTRY.exec(line.trim())
      const due = entry && DATED.exec(entry[1])?.[1]
      if (!entry || !due || due < from || due >= to) continue
      found.push({ text: entry[1].replace(LINK, "$1").replace(MARKS, "").trim(), due })
    }
  }
  return found.sort((a, b) => b.due.localeCompare(a.due))
}

export const CalendarLookBack: QuartzTransformerPlugin = () => ({
  name: "CalendarLookBack",
  markdownPlugins(ctx) {
    return [
      () => (tree: any, file) => {
        const fm = file.data.frontmatter
        const rel = file.data.relativePath ?? ""
        const [bucket, dir, name] = rel.split("/")
        if (fm?.type !== "list" || fm?.kind !== "calendar" || dir !== "lists" || !name) return

        const now = new Date()
        const start = new Date(now)
        start.setDate(now.getDate() - LOOK_BACK_DAYS)
        const months = [...new Set([day(start).slice(0, 7), day(now).slice(0, 7)])]
        const archiveDir = path.join(ctx.argv.directory, bucket, "lists", "archive", name.replace(/\.md$/, ""))
        const past = pastEntries(archiveDir, months, day(start), day(now))
        if (past.length === 0) return

        tree.children.push(
          { type: "heading", depth: 2, children: [{ type: "text", value: L.lookBack }] },
          {
            type: "list",
            ordered: false,
            spread: false,
            data: { hProperties: { className: ["calendar-look-back"] } },
            children: past.map((p) => ({
              type: "listItem",
              spread: false,
              children: [{ type: "paragraph", children: [{ type: "text", value: `${p.text} 📅 ${p.due}` }] }],
            })),
          },
        )
      },
    ]
  },
})
