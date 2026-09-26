import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import { classNames } from "../util/lang"
import { L } from "./familyModel"

// NEW COMPONENT (not an upstream override) — the greeting on the home
// page, and only there. The layout gates it on the index slug.
//
// It lives in the layout rather than in the vault's index.md on
// purpose. index.md is a generated page: the curator rewrites it on
// every sweep from what the vault actually contains, so a welcome
// written into it would survive exactly until the next rebuild. Chrome
// belongs in the chrome.
//
// The copy follows the instance language, from the label table in
// familyModel.ts.
const Welcome: QuartzComponent = ({ displayClass }: QuartzComponentProps) => {
  return (
    <div class={classNames(displayClass, "fs-welcome")}>
      <p>{L.welcome}</p>
      <p class="fs-welcome-hint">{L.welcomeHint}</p>
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
