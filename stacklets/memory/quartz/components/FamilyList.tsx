import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import { PageList } from "./PageList"
import { FullSlug, resolveRelative } from "../util/path"
import { familyModel, L } from "./familyModel"

// NEW COMPONENT (not an upstream override): the body of a page the
// FamilyLists emitter writes: a topic without an about page, a document
// type, needs attention, all notes, all bookmarks. Sections without
// entries are left out, so a topic with only tasks shows only tasks.

const FamilyList: QuartzComponent = (props: QuartzComponentProps) => {
  const { ctx, fileData, allFiles } = props
  const here = fileData.slug as FullSlug
  const page = familyModel(ctx, allFiles).lists.get(here)
  if (!page) return null

  const bySlug = new Map(allFiles.map((f) => [f.slug, f]))
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
                  <a class="internal" href={resolveRelative(here, t.slug as FullSlug)}>
                    {t.text}
                  </a>
                </li>
              ))}
            </ul>
          )}
          {s.slugs && (
            <PageList
              {...props}
              allFiles={s.slugs.map((slug) => bySlug.get(slug as FullSlug)!).filter(Boolean)}
              // Keep the model's order: newest filed first.
              sort={() => 0}
            />
          )}
        </section>
      ))}
    </div>
  )
}

FamilyList.css = `
.family-list .fl-intro {
  color: var(--gray);
  margin-top: 0;
}

.family-list .fl-section h2 {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  margin-top: 2rem;
}

.family-list .fl-count {
  font-family: var(--codeFont);
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--gray);
}

.family-list .fl-tasks {
  list-style: none;
  padding: 0;
  margin: 0.5rem 0 1rem;
}

.family-list .fl-tasks li {
  display: flex;
  gap: 0.7rem;
  align-items: baseline;
  padding: 0.45rem 0;
  border-bottom: 1px solid var(--lightgray);
}

.family-list .fl-box {
  flex: none;
  width: 0.85rem;
  height: 0.85rem;
  border: 1.5px solid var(--secondary);
  border-radius: 3px;
  transform: translateY(0.1rem);
}
`

export default (() => FamilyList) satisfies QuartzComponentConstructor
