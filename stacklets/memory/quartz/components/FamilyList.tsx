import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import { FullSlug, resolveRelative } from "../util/path"
import { familyModel, fmtDate, L } from "./familyModel"

// NEW COMPONENT (not an upstream override): the body of a page the
// FamilyLists emitter writes, such as a topic without an about page, a
// document type, a year, or all notes. Sections without entries are left
// out, so a topic with only tasks shows only tasks.
//
// Rows are our own rather than upstream's PageList: a date, the title,
// and one line saying what it is (type, sender, people). PageList shows
// raw tags instead, which in this vault includes "Person: Marge".

const FamilyList: QuartzComponent = ({ ctx, fileData, allFiles }: QuartzComponentProps) => {
  const here = fileData.slug as FullSlug
  const model = familyModel(ctx, allFiles)
  const page = model.lists.get(here)
  if (!page) return null

  const bySlug = new Map(allFiles.map((f) => [String(f.slug), f]))
  const sections = page.sections.filter((s) => s.tasks?.length || s.slugs?.length)
  const multi = sections.length > 1

  return (
    <div class="family-list">
      <p class="fl-intro">{page.intro}</p>
      {sections.length === 0 && <p>{L.nothing}</p>}
      {sections.map((s) => (
        <section class="fl-section">
          {multi && s.heading && (
            <h2>
              {s.heading}
              <span class="fl-count">{s.tasks?.length ?? s.slugs?.length}</span>
            </h2>
          )}
          {s.tasks && (
            <ul class="fl-tasks">
              {s.tasks.map((t) => (
                <li>
                  <span class="fl-box" aria-hidden="true"></span>
                  <a href={resolveRelative(here, t.slug as FullSlug)}>{t.text}</a>
                </li>
              ))}
            </ul>
          )}
          {s.slugs && (
            <ul class="fl-rows">
              {s.slugs.map((slug) => {
                const f = bySlug.get(slug)
                if (!f) return null
                const row = model.describe(f)
                return (
                  <li>
                    <span class="fl-date">{row.date ? fmtDate(row.date) : ""}</span>
                    <div class="fl-main">
                      <a class="internal" href={resolveRelative(here, slug as FullSlug)}>
                        {f.frontmatter?.title ?? slug}
                      </a>
                      {row.meta.length > 0 && <span class="fl-meta">{row.meta.join(" · ")}</span>}
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </section>
      ))}
    </div>
  )
}

FamilyList.css = `
.family-list .fl-intro {
  color: var(--gray);
  margin: 0 0 1.2rem;
}

.family-list .fl-section h2 {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  margin: 2rem 0 0.4rem;
  font-size: 1.35rem;
}

.family-list .fl-section:first-of-type h2 {
  margin-top: 1.4rem;
}

.family-list .fl-count {
  font-family: var(--codeFont);
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--gray);
}

.family-list ul {
  list-style: none;
  padding: 0;
  margin: 0;
}

.family-list .fl-rows li,
.family-list .fl-tasks li {
  display: grid;
  grid-template-columns: 7.5rem 1fr;
  gap: 1rem;
  align-items: baseline;
  padding: 0.7rem 0;
  border-bottom: 1px solid rgba(61, 143, 160, 0.14);
}

.family-list .fl-rows li:last-child,
.family-list .fl-tasks li:last-child {
  border-bottom: 0;
}

.family-list .fl-date {
  font-family: var(--codeFont);
  font-size: 0.75rem;
  color: var(--gray);
  white-space: nowrap;
}

.family-list .fl-main {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  min-width: 0;
}

.family-list .fl-main > a {
  font-weight: 600;
  color: var(--dark);
}

.family-list .fl-meta {
  font-size: 0.85rem;
  color: var(--gray);
}

.family-list .fl-tasks li {
  grid-template-columns: 1.2rem 1fr;
  gap: 0.6rem;
}

.family-list .fl-tasks a {
  color: var(--darkgray);
  font-weight: 400;
}

.family-list .fl-box {
  width: 0.9rem;
  height: 0.9rem;
  border: 1.5px solid var(--secondary);
  border-radius: 3px;
  transform: translateY(0.15rem);
}

@media all and (max-width: 600px) {
  .family-list .fl-rows li {
    grid-template-columns: 1fr;
    gap: 0.1rem;
  }
}
`

export default (() => FamilyList) satisfies QuartzComponentConstructor
