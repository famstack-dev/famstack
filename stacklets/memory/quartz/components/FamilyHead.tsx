import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import { FullSlug, resolveRelative } from "../util/path"
import { classNames } from "../util/lang"
import { familyModel, fmtDate } from "./familyModel"

// NEW COMPONENT (not an upstream override): the page header, in place of
// upstream's ArticleTitle, ContentMeta and TagList.
//
//   DOCUMENT · INVOICE                       a mono label: what this is
//   Springfield Power & Light Bill           one serif title
//   31 Mar 2026 · Springfield P&L   [Utility] [Marge]
//
// The chips link a record to its topics and people, which is how a
// document reached from Documents leads on to the topic it belongs to.
//
// Many generated pages open with their own `# Heading`. That heading is
// the better title (the home page's is the family's name, not "Family
// Memory"), so it becomes the title here, and custom.scss hides the
// copy at the top of the article. One title per page either way.

type Hast = { type: string; tagName?: string; value?: string; children?: Hast[] }
const textOf = (n: Hast): string => n.value ?? (n.children ?? []).map(textOf).join("")
const leadingH1 = (tree: Hast | undefined) => {
  const first = (tree?.children ?? []).find((n) => n.type === "element")
  return first?.tagName === "h1" ? textOf(first).trim() : undefined
}

const FamilyHead: QuartzComponent = ({ ctx, fileData, allFiles, tree, displayClass }: QuartzComponentProps) => {
  const here = fileData.slug as FullSlug
  const head = familyModel(ctx, allFiles).head(here)
  const title = leadingH1(tree as Hast) ?? fileData.frontmatter?.title
  const facts = [head.date ? fmtDate(head.date) : "", ...head.meta].filter(Boolean)

  return (
    // A div, not <header>: Quartz styles every header element as a flex row.
    <div class={classNames(displayClass, "family-head")}>
      {head.kicker.length > 0 && <p class="fh-kicker">{head.kicker.join(" · ")}</p>}
      <h1 class="article-title">{title}</h1>
      {(facts.length > 0 || head.chips.length > 0) && (
        <div class="fh-meta">
          {facts.length > 0 && <span class="fh-facts">{facts.join(" · ")}</span>}
          {head.chips.map((c) =>
            c.slug ? (
              <a class="fh-chip" href={resolveRelative(here, c.slug as FullSlug)}>
                {c.label}
              </a>
            ) : (
              <span class="fh-chip">{c.label}</span>
            ),
          )}
        </div>
      )}
    </div>
  )
}

FamilyHead.css = `
.family-head {
  margin: 0 0 1.4rem;
}

.family-head .fh-kicker {
  margin: 0 0 0.35rem;
  font-family: var(--codeFont);
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--secondary);
}

.family-head .article-title {
  margin: 0;
  font-size: clamp(1.9rem, 4vw, 2.5rem);
  line-height: 1.12;
}

.family-head .fh-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem 0.5rem;
  margin-top: 0.7rem;
}

.family-head .fh-facts {
  font-size: 0.88rem;
  color: var(--gray);
  margin-right: 0.3rem;
}

.family-head .fh-chip {
  display: inline-flex;
  align-items: center;
  min-height: 1.7rem;
  padding: 0 0.6rem;
  border: 1px solid rgba(61, 143, 160, 0.3);
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--darkgray);
  background: transparent;
  text-decoration: none;
}

.family-head a.fh-chip:hover {
  border-color: var(--tertiary);
  color: var(--dark);
}
`

export default (() => FamilyHead) satisfies QuartzComponentConstructor
