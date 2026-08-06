import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import { classNames } from "../util/lang"

// NEW COMPONENT (not an upstream override) — the greeting on the home
// page, and only there. The layout gates it on the index slug.
//
// It lives in the layout rather than in the vault's index.md on
// purpose. index.md is a generated page: the curator rewrites it on
// every sweep from what the vault actually contains, so a welcome
// written into it would survive exactly until the next rebuild. Chrome
// belongs in the chrome.
//
// The copy is English because quartz.config.ts pins locale to en-US.
// When the wiki is localised, this string moves with the locale rather
// than staying here.
const Welcome: QuartzComponent = ({ displayClass }: QuartzComponentProps) => {
  return (
    <div class={classNames(displayClass, "fs-welcome")}>
      <p>
        Everything the household has kept: documents, the notes about them, and the people they
        belong to.
      </p>
      <p class="fs-welcome-hint">Search from the sidebar, or start with a name.</p>
    </div>
  )
}

Welcome.css = `
.fs-welcome {
  margin: 0.75rem 0 0.5rem;
  padding: 0.9rem 1.1rem;
  border-left: 3px solid var(--tertiary);
  background: rgba(240, 125, 69, 0.06);
  border-radius: 0 8px 8px 0;
}

.fs-welcome > p {
  margin: 0;
  font-size: 0.95rem;
  line-height: 1.5rem;
}

.fs-welcome > p.fs-welcome-hint {
  margin-top: 0.2rem;
  font-size: 0.85rem;
  color: var(--gray);
}
`

export default (() => Welcome) satisfies QuartzComponentConstructor
