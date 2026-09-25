import fs from "fs"
import path from "path"
import { QuartzPluginData } from "../plugins/vfile"
import { BuildCtx } from "../util/ctx"

// NEW MODULE (not an upstream override): the family navigation, computed
// at build time from the vault and read by FamilyNav and FamilyLists.
//
// The vault's folders are laid out for the programs that write them:
// documents by date, captures under the topic room they came from, one
// folder per bucket. The family asks different questions (what is going
// on, what do we have about the car, where is the diary), so the sidebar
// is a second structure computed over the same files. Nothing moves on
// disk: a path is a page's identity, and links and re-filing depend on it.
//
// Two dimensions. Topics say what something is about, Documents say what
// kind of paper it is. A document is listed once under Documents and
// counted under every topic it is tagged with.

// ── Language ─────────────────────────────────────────────────────────────

export type Lang = "en" | "de"

export const lang: Lang = (process.env.WIKI_LANGUAGE ?? "en").toLowerCase().startsWith("de")
  ? "de"
  : "en"

const LABELS = {
  en: {
    nav: "Family wiki",
    menu: "Menu",
    start: "Start",
    recent: "Recent",
    diary: "Diary",
    people: "People",
    topics: "Topics",
    otherTopics: "Other topics",
    documents: "Documents",
    needsAttention: "Needs attention",
    byType: "By type",
    byYear: "By year",
    senders: "Senders",
    emails: "Emails",
    notesLinks: "Notes & links",
    notes: "Notes",
    bookmarks: "Bookmarks",
    tasks: "Open tasks",
    nothing: "Nothing here yet.",
    needsAttentionIntro:
      "Payment reminders, and documents from the last eight weeks that still have an open action item.",
    typeIntro: "Every document of this kind, newest first.",
    topicIntro: "Everything filed about this topic, newest first.",
    notesIntro: "Every note the family has saved, newest first.",
    bookmarksIntro: "Every link the family has saved, newest first.",
  },
  de: {
    nav: "Familienwiki",
    menu: "Menü",
    start: "Start",
    recent: "Aktuell",
    diary: "Tagebuch",
    people: "Personen",
    topics: "Themen",
    otherTopics: "Weitere Themen",
    documents: "Dokumente",
    needsAttention: "Zu erledigen",
    byType: "Nach Art",
    byYear: "Nach Jahr",
    senders: "Absender",
    emails: "E-Mails",
    notesLinks: "Notizen & Links",
    notes: "Notizen",
    bookmarks: "Lesezeichen",
    tasks: "Offene Aufgaben",
    nothing: "Hier ist noch nichts.",
    needsAttentionIntro:
      "Mahnungen und Dokumente der letzten acht Wochen mit einer offenen Aufgabe.",
    typeIntro: "Alle Dokumente dieser Art, die neuesten zuerst.",
    topicIntro: "Alles, was zu diesem Thema abgelegt ist, das Neueste zuerst.",
    notesIntro: "Alle Notizen der Familie, die neuesten zuerst.",
    bookmarksIntro: "Alle Links der Familie, die neuesten zuerst.",
  },
} as const

export const L = LABELS[lang]

// ── Tuning ───────────────────────────────────────────────────────────────

// A topic counts as recent when something was filed in it within this
// window. The list is capped, so a bulk import does not turn every topic
// into news.
const RECENT_DAYS = 28
const RECENT_MAX = 5
const ATTENTION_DAYS = 56
const SENDERS_MAX = 5

// Folders under the shared bucket that hold a kind of record rather than
// a topic.
const KIND_FOLDERS = new Set(["documents", "diary", "correspondents", "emails", "notes", "bookmarks", "media"])

// Accounts that own a bucket but are not people.
const isAccount = (slug: string) => slug === "admin" || slug.endsWith("-bot")

// ── Ontology ─────────────────────────────────────────────────────────────
//
// `ontology.toml` sits at the vault root. Quartz does not read TOML, and
// the file uses a small, regular subset (section headers, inline tables,
// string arrays), so it is parsed here by pattern rather than pulling in a
// TOML dependency the image does not have.

type Names = Record<string, string>
interface Entry {
  id: string
  names: Names
  synonyms: string[]
  area?: string
}
export interface Ontology {
  topics: Entry[]
  doctypes: Entry[]
  areas: Entry[]
}

export function parseOntology(text: string): Ontology {
  const out: Ontology = { topics: [], doctypes: [], areas: [] }
  const header = /^\[(topic|doctype|area)\.([A-Za-z0-9_]+)\]\s*$/gm
  const heads = [...text.matchAll(header)]
  heads.forEach((m, i) => {
    const body = text.slice(m.index! + m[0].length, heads[i + 1]?.index ?? text.length)
    const names: Names = {}
    const nm = body.match(/^names\s*=\s*\{([^}]*)\}/m)
    if (nm) for (const [, k, v] of nm[1].matchAll(/(\w+)\s*=\s*"([^"]*)"/g)) names[k] = v
    const synonyms: string[] = []
    const sm = body.match(/^synonyms\s*=\s*\{([\s\S]*?)\}\s*$/m)
    if (sm) for (const [, v] of sm[1].matchAll(/"([^"]*)"/g)) synonyms.push(v)
    const area = body.match(/^area\s*=\s*"([^"]+)"/m)?.[1]
    const entry: Entry = { id: m[2], names, synonyms, area }
    ;({ topic: out.topics, doctype: out.doctypes, area: out.areas })[m[1] as "topic"].push(entry)
  })
  return out
}

const readFile = (p: string) => {
  try {
    return fs.readFileSync(p, "utf8")
  } catch {
    return ""
  }
}

// The vault's ontology is the family's copy: seeded once at install and
// theirs from then on. The seed that ships with this image supplies the
// areas a vault does not name yet, so an instance installed before areas
// existed groups its topics after a restart, with no edit to its vault.
// Whatever the vault says wins: its own areas, and a topic's own `area`.
function readOntology(ctx: BuildCtx): Ontology {
  const vault = parseOntology(readFile(path.join(ctx.argv.directory, "ontology.toml")))
  const seed = parseOntology(readFile(path.join(process.cwd(), "ontology.seed.toml")))
  const seedArea = new Map(seed.topics.map((t) => [t.id, t.area]))
  return {
    topics: vault.topics.map((t) => ({ ...t, area: t.area ?? seedArea.get(t.id) })),
    doctypes: vault.doctypes,
    areas: vault.areas.length ? vault.areas : seed.areas,
  }
}

const nameOf = (e: Entry) => e.names[lang] ?? e.names.en ?? e.id

// Tags, categories and folder slugs name a topic in whatever language the
// instance files in, with or without umlauts ("behorde" for Behörde).
// Folding them all to one key lets any of those spellings find the entry.
export function fold(s: string): string {
  return s
    .toLowerCase()
    .replace(/ä/g, "a")
    .replace(/ö/g, "o")
    .replace(/ü/g, "u")
    .replace(/ß/g, "ss")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
}

function indexEntries(entries: Entry[]): Map<string, Entry> {
  const idx = new Map<string, Entry>()
  for (const e of entries) {
    for (const key of [e.id, e.id.replace(/_/g, " "), ...Object.values(e.names), ...e.synonyms]) {
      const k = fold(key)
      if (k && !idx.has(k)) idx.set(k, e)
    }
  }
  return idx
}

// ── The model ────────────────────────────────────────────────────────────

export interface NavNode {
  key?: string
  label: string
  slug?: string
  count?: number
  open?: boolean
  children?: NavNode[]
}

export interface ListSection {
  heading?: string
  slugs?: string[]
  tasks?: { text: string; slug: string }[]
}

export interface ListPage {
  slug: string
  title: string
  intro: string
  sections: ListSection[]
}

export interface Model {
  nav: NavNode[]
  lists: Map<string, ListPage>
}

type Kind = "document" | "note" | "bookmark" | "email"
const RECORD_KINDS = new Set<string>(["document", "note", "bookmark", "email"])

interface Rec {
  file: QuartzPluginData
  slug: string
  kind: Kind
  filed?: Date
}

interface Topic {
  key: string
  label: string
  slug?: string
  area: string
  room: boolean
  records: Rec[]
  tasks: { text: string; slug: string }[]
  last?: Date
}

const fm = (f: QuartzPluginData) => (f.frontmatter ?? {}) as Record<string, any>
const asList = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : v ? [String(v)] : [])

function parseDate(v: unknown): Date | undefined {
  if (!v) return undefined
  const d = new Date(String(v))
  return isNaN(d.getTime()) ? undefined : d
}

function readText(ctx: BuildCtx, f: QuartzPluginData): string {
  try {
    return fs.readFileSync(path.join(ctx.argv.directory, String(f.relativePath)), "utf8")
  } catch {
    return ""
  }
}

// `- [ ] Pay the bill by Friday` → "Pay the bill by Friday"
const openTasks = (text: string) =>
  [...text.matchAll(/^\s*[-*] \[ \] (.+)$/gm)].map((m) => m[1].trim())

const newest = (a?: Date, b?: Date) => (!a ? b : !b ? a : a > b ? a : b)
const byFiledDesc = (a: Rec, b: Rec) => (b.filed?.getTime() ?? 0) - (a.filed?.getTime() ?? 0)

// Every page of a build renders the sidebar, and the emitters each pass
// their own copy of the file list, so the model is kept per list.
const cache = new WeakMap<QuartzPluginData[], Model>()

export function familyModel(ctx: BuildCtx, allFiles: QuartzPluginData[]): Model {
  const hit = cache.get(allFiles)
  if (hit) return hit
  const model = build(ctx, allFiles)
  cache.set(allFiles, model)
  return model
}

function build(ctx: BuildCtx, allFiles: QuartzPluginData[]): Model {
  const onto = readOntology(ctx)
  const topicIdx = indexEntries(onto.topics)
  const typeIdx = indexEntries(onto.doctypes)
  const now = Date.now()
  const daysAgo = (d?: Date) => (d ? (now - d.getTime()) / 86_400_000 : Infinity)

  // The shared bucket is wherever the diary or the documents live;
  // `family` unless the instance renamed it.
  const bucket =
    allFiles
      .map((f) => String(f.slug ?? "").match(/^([^/]+)\/(diary\/about|documents\/)/)?.[1])
      .find(Boolean) ?? "family"

  // ── topics ──
  const topics = new Map<string, Topic>()
  const topicFor = (entry: Entry): Topic => {
    let t = topics.get(entry.id)
    if (!t) {
      t = { key: entry.id, label: nameOf(entry), area: entry.area ?? "", room: false, records: [], tasks: [] }
      topics.set(entry.id, t)
    }
    return t
  }
  // A folder under the shared bucket is a topic. When its name is an
  // ontology topic in any language, it is that topic, so a "Reise" room
  // and the travel topic stay one entry.
  const folderTopic = (seg: string): Topic => {
    const entry = topicIdx.get(fold(seg))
    if (entry) return topicFor(entry)
    const key = "room:" + seg
    let t = topics.get(key)
    if (!t) {
      t = { key, label: seg, area: "", room: true, records: [], tasks: [] }
      topics.set(key, t)
    }
    return t
  }

  const records: Rec[] = []
  const people: NavNode[] = []
  const diaryYears = new Map<string, { slug: string; label: string; months: NavNode[] }>()
  let diaryRoot: string | undefined

  for (const f of allFiles) {
    const slug = String(f.slug ?? "")
    const meta = fm(f)
    const segs = slug.split("/")

    // people: a member's hub page at the vault root
    if (segs.length === 2 && segs[1] === "about" && meta.type === "person" && !isAccount(segs[0])) {
      people.push({ label: String(meta.title ?? segs[0]), slug })
      continue
    }

    // diary: <bucket>/diary/about, <bucket>/diary/YYYY/about, <bucket>/diary/YYYY/MM
    if (segs[0] === bucket && segs[1] === "diary" && segs[2] !== "entries") {
      if (segs.length === 3 && segs[2] === "about") diaryRoot = slug
      else if (/^\d{4}$/.test(segs[2] ?? "")) {
        const y = diaryYears.get(segs[2]) ?? { slug: "", label: segs[2], months: [] }
        if (segs[3] === "about") y.slug = slug
        else if (/^\d{2}$/.test(segs[3] ?? ""))
          y.months.push({ label: String(meta.title ?? segs[3]).replace(/\s*\d{4}$/, ""), slug })
        diaryYears.set(segs[2], y)
      }
      continue
    }

    // topic folders: about page and task list
    if (segs[0] === bucket && segs.length === 3 && !KIND_FOLDERS.has(segs[1])) {
      const t = folderTopic(segs[1])
      if (segs[2] === "about") {
        t.slug = slug
        if (t.room) t.label = String(meta.title ?? t.label)
        if (meta.area) t.area = String(meta.area)
      } else if (segs[2] === "todos") {
        const open = openTasks(readText(ctx, f)).map((text) => ({ text, slug }))
        t.tasks.push(...open)
        if (open.length) t.last = newest(t.last, f.dates?.modified)
      }
      continue
    }

    if (!RECORD_KINDS.has(meta.type)) continue
    const rec: Rec = {
      file: f,
      slug,
      kind: meta.type as Kind,
      filed: parseDate(meta.timestamp) ?? parseDate(meta.date) ?? f.dates?.modified,
    }
    records.push(rec)

    // A record belongs to every topic its category or tags name, and to
    // the topic folder it was filed in.
    const hits = new Set<Topic>()
    for (const tag of [meta.category, ...asList(meta.tags)]) {
      if (!tag || /^person:/i.test(String(tag))) continue
      const entry = topicIdx.get(fold(String(tag)))
      if (entry) hits.add(topicFor(entry))
    }
    if (segs[0] === bucket && segs.length > 2 && !KIND_FOLDERS.has(segs[1])) hits.add(folderTopic(segs[1]))
    for (const t of hits) {
      t.records.push(rec)
      t.last = newest(t.last, rec.filed)
    }
  }

  const lists = new Map<string, ListPage>()
  const addList = (page: ListPage) => lists.set(page.slug, page)
  const slugs = (rs: Rec[]) => [...rs].sort(byFiledDesc).map((r) => r.slug)

  // Every visible topic gets a page: its own about page when the curator
  // wrote one, otherwise a list page with its tasks and records.
  const visible = [...topics.values()].filter(
    (t) => t.records.length > 0 || t.tasks.length > 0,
  )
  for (const t of visible) {
    if (t.slug) continue
    t.slug = `lists/topic/${t.key.replace(/^room:/, "")}`
    const by = (k: Kind) => slugs(t.records.filter((r) => r.kind === k))
    addList({
      slug: t.slug,
      title: t.label,
      intro: L.topicIntro,
      sections: [
        { heading: L.tasks, tasks: t.tasks },
        { heading: L.documents, slugs: by("document") },
        { heading: L.notes, slugs: by("note") },
        { heading: L.bookmarks, slugs: by("bookmark") },
        { heading: L.emails, slugs: by("email") },
      ],
    })
  }
  const topicNode = (t: Topic): NavNode => ({
    label: t.label,
    slug: t.slug,
    count: t.records.length || undefined,
  })

  // ── recent ──
  const recent = visible
    .filter((t) => daysAgo(t.last) <= RECENT_DAYS)
    .sort((a, b) => (b.last?.getTime() ?? 0) - (a.last?.getTime() ?? 0))
    .slice(0, RECENT_MAX)

  // ── topics by area ──
  // An area shows when one of its topics does. With a single topic it
  // collapses into it; a topic named like its area (Gesundheit under
  // Gesundheit) becomes the area's own link instead of repeating.
  // Chat-room topics come first: the family made them.
  const areaNodes: NavNode[] = []
  const areaIds = [...onto.areas.map((a) => a.id), ""]
  for (const id of areaIds) {
    const area = onto.areas.find((a) => a.id === id)
    const label = area ? nameOf(area) : L.otherTopics
    const members = visible
      .filter((t) => (area ? t.area === id : !onto.areas.some((a) => a.id === t.area)))
      .filter((t) => t.key !== "credential")
      .sort((a, b) => Number(b.room) - Number(a.room) || a.label.localeCompare(b.label, lang))
    if (members.length === 0) continue
    // "Other topics" is a shelf, not an area: it never takes a topic's place.
    if (members.length === 1 && area) {
      areaNodes.push({ ...topicNode(members[0]), label })
      continue
    }
    const same = members.find((t) => fold(t.label) === fold(label))
    const rest = members.filter((t) => t !== same)
    areaNodes.push({
      label,
      slug: same?.slug,
      count: members.reduce((n, t) => n + t.records.length, 0) || undefined,
      children: rest.map(topicNode),
    })
  }

  // ── documents ──
  const docs = records.filter((r) => r.kind === "document")
  const reminder = onto.doctypes.find((d) => d.id === "payment_reminder")
  const attention = docs.filter((r) => {
    const m = fm(r.file)
    if (daysAgo(parseDate(m.date) ?? r.filed) > ATTENTION_DAYS) return false
    if (reminder && typeIdx.get(fold(String(m.document_type ?? ""))) === reminder) return true
    return openTasks(readText(ctx, r.file)).length > 0
  })
  if (attention.length)
    addList({
      slug: "lists/needs-attention",
      title: L.needsAttention,
      intro: L.needsAttentionIntro,
      sections: [{ slugs: slugs(attention) }],
    })

  const types = new Map<string, { label: string; recs: Rec[] }>()
  for (const r of docs) {
    const raw = String(fm(r.file).document_type ?? "")
    if (!raw) continue
    const entry = typeIdx.get(fold(raw))
    const key = entry?.id ?? fold(raw).replace(/ /g, "-")
    const g = types.get(key) ?? { label: entry ? nameOf(entry) : raw, recs: [] }
    g.recs.push(r)
    types.set(key, g)
  }
  const typeNodes = [...types.entries()]
    .sort((a, b) => b[1].recs.length - a[1].recs.length || a[1].label.localeCompare(b[1].label, lang))
    .map(([key, g]) => {
      const slug = `lists/type/${key}`
      addList({ slug, title: g.label, intro: L.typeIntro, sections: [{ slugs: slugs(g.recs) }] })
      return { label: g.label, slug, count: g.recs.length }
    })

  const years = new Map<string, number>()
  for (const r of docs) {
    const y = r.slug.split("/")[2]
    if (/^\d{4}$/.test(y)) years.set(y, (years.get(y) ?? 0) + 1)
  }
  const yearNodes = [...years.entries()]
    .sort((a, b) => Number(b[0]) - Number(a[0]))
    .map(([y, n]) => ({ label: y, slug: `${bucket}/documents/${y}/index`, count: n }))

  // Senders: correspondent pages, the ones that wrote most recently first.
  const corrPages = new Map<string, string>()
  for (const f of allFiles) {
    const m = fm(f)
    if (m.type === "correspondent") corrPages.set(fold(String(m.canonical ?? m.title ?? "")), String(f.slug))
  }
  const lastFrom = new Map<string, Date | undefined>()
  for (const r of docs) {
    const c = fold(String(fm(r.file).correspondent ?? ""))
    if (corrPages.has(c)) lastFrom.set(c, newest(lastFrom.get(c), parseDate(fm(r.file).date) ?? r.filed))
  }
  const senderNodes = [...lastFrom.entries()]
    .sort((a, b) => (b[1]?.getTime() ?? 0) - (a[1]?.getTime() ?? 0))
    .slice(0, SENDERS_MAX)
    .map(([c]) => {
      const slug = corrPages.get(c)!
      const page = allFiles.find((f) => f.slug === slug)
      return { label: String(fm(page!).title ?? c), slug }
    })

  const emails = records.filter((r) => r.kind === "email")
  const docChildren: NavNode[] = []
  if (attention.length)
    docChildren.push({ label: L.needsAttention, slug: "lists/needs-attention", count: attention.length })
  if (typeNodes.length) docChildren.push({ label: L.byType, children: typeNodes })
  if (yearNodes.length) docChildren.push({ label: L.byYear, children: yearNodes })
  if (corrPages.size)
    docChildren.push({ label: L.senders, slug: `${bucket}/correspondents/index`, count: corrPages.size, children: senderNodes })
  if (emails.length) docChildren.push({ label: L.emails, slug: `${bucket}/emails/index`, count: emails.length })

  // ── notes & links ──
  const notes = records.filter((r) => r.kind === "note")
  const bookmarks = records.filter((r) => r.kind === "bookmark")
  const captureNodes: NavNode[] = []
  if (notes.length) {
    addList({ slug: "lists/notes", title: L.notes, intro: L.notesIntro, sections: [{ slugs: slugs(notes) }] })
    captureNodes.push({ label: L.notes, slug: "lists/notes", count: notes.length })
  }
  if (bookmarks.length) {
    addList({ slug: "lists/bookmarks", title: L.bookmarks, intro: L.bookmarksIntro, sections: [{ slugs: slugs(bookmarks) }] })
    captureNodes.push({ label: L.bookmarks, slug: "lists/bookmarks", count: bookmarks.length })
  }

  // ── assemble, in the agreed order ──
  const nav: NavNode[] = [{ key: "start", label: L.start, slug: "index" }]
  if (recent.length) nav.push({ key: "recent", label: L.recent, open: true, children: recent.map(topicNode) })
  if (diaryRoot || diaryYears.size) {
    const yearsDesc = [...diaryYears.values()].sort((a, b) => Number(b.label) - Number(a.label))
    nav.push({
      key: "diary",
      label: L.diary,
      slug: diaryRoot,
      children: yearsDesc.map((y) => ({
        label: y.label,
        slug: y.slug || undefined,
        children: y.months.sort((a, b) => b.slug!.localeCompare(a.slug!)),
      })),
    })
  }
  if (people.length)
    nav.push({ key: "people", label: L.people, children: people.sort((a, b) => a.label.localeCompare(b.label, lang)) })
  if (areaNodes.length) nav.push({ key: "topics", label: L.topics, children: areaNodes })
  if (docs.length || emails.length)
    nav.push({ key: "documents", label: L.documents, slug: `${bucket}/documents/index`, count: docs.length, children: docChildren })
  if (captureNodes.length) nav.push({ key: "notes", label: L.notesLinks, children: captureNodes })

  return { nav, lists }
}
