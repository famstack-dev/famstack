import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import { FullSlug, resolveRelative } from "../util/path"
import { classNames } from "../util/lang"
import { familyModel } from "./familyModel"

// NEW COMPONENT (not an upstream override): breadcrumbs that follow the
// family navigation, in place of upstream's, which follow the folders
// ("family › diary › 2026"). A page the menu does not list takes the
// path of what it belongs to: a document its type, a note its topic.
const FamilyCrumbs: QuartzComponent = ({ ctx, fileData, allFiles, displayClass }: QuartzComponentProps) => {
  const here = fileData.slug as FullSlug
  const trail = familyModel(ctx, allFiles).crumbs(here)
  return (
    <nav class={classNames(displayClass, "family-crumbs")} aria-label="Breadcrumbs">
      {trail.map((n, i) => (
        <>
          {i > 0 && <span class="fc-sep" aria-hidden="true">/</span>}
          {n.slug ? <a href={resolveRelative(here, n.slug as FullSlug)}>{n.label}</a> : <span>{n.label}</span>}
        </>
      ))}
    </nav>
  )
}

FamilyCrumbs.css = `
.family-crumbs {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.2rem 0.5rem;
  margin: 0 0 1.1rem;
  font-family: var(--codeFont);
  font-size: 0.75rem;
  letter-spacing: 0.02em;
  color: var(--gray);
}

.family-crumbs a {
  color: var(--secondary);
  font-weight: 400;
  text-decoration: none;
}

.family-crumbs a:hover {
  color: var(--tertiary);
}

.family-crumbs .fc-sep {
  color: var(--gray);
  opacity: 0.5;
}
`

export default (() => FamilyCrumbs) satisfies QuartzComponentConstructor
