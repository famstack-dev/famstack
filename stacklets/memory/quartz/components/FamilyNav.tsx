import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import { classNames } from "../util/lang"
import { L } from "./familyModel"
// @ts-ignore
import script from "./scripts/familyNav.inline"

// NEW COMPONENT (not an upstream override): the sidebar navigation that
// replaces the Explorer.
//
// The Explorer shows the vault's folders, which are laid out for the
// programs that write them. This shows the family's structure instead:
// Start, Recent, Diary, People, Topics, Documents, Notes & links (see
// familyModel.ts). The tree itself is drawn in the browser from
// static/family-nav.json, like upstream's Explorer draws from the content
// index, so it stays current when `quartz --serve` re-renders only the
// pages that changed.
const FamilyNav: QuartzComponent = ({ displayClass }: QuartzComponentProps) => (
  <nav class={classNames(displayClass, "family-nav")} aria-label={L.nav}>
    <details class="fn-menu" open>
      <summary>{L.menu}</summary>
      <ul class="fn-tree"></ul>
    </details>
  </nav>
)

FamilyNav.afterDOMLoaded = script

FamilyNav.css = `
.family-nav {
  margin-top: 0.5rem;
  min-height: 0;
  overflow-y: auto;
  flex: 1 1 auto;
}

.family-nav ul {
  list-style: none;
  margin: 0;
  padding: 0;
}

.family-nav .fn-menu > summary {
  display: none;
}

.family-nav details > summary {
  list-style: none;
  display: flex;
  align-items: center;
  cursor: pointer;
}

.family-nav details > summary::-webkit-details-marker {
  display: none;
}

/* The fold marker: a small chevron drawn from two borders, so it takes
   the text colour and needs no icon. */
.family-nav details > summary::before {
  content: "";
  flex: none;
  width: 0.36rem;
  height: 0.36rem;
  margin: 0 0.55rem 0 0.1rem;
  border-right: 1.5px solid var(--gray);
  border-bottom: 1.5px solid var(--gray);
  transform: rotate(-45deg);
  transition: transform 0.15s ease;
}

.family-nav details[open] > summary::before {
  transform: rotate(45deg) translate(-1px, -1px);
}

.family-nav .fn-link {
  flex: 1;
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  min-height: 2rem;
  padding: 0.2rem 0.5rem;
  border-radius: 6px;
  color: var(--darkgray);
  /* Quartz makes every link bold; here weight marks the current page only. */
  font-weight: 400;
  text-decoration: none;
  line-height: 1.35;
}

.family-nav a.fn-link:hover {
  background: var(--highlight);
  color: var(--dark);
}

.family-nav .fn-link[aria-current="page"] {
  background: color-mix(in srgb, var(--tertiary) 12%, transparent);
  box-shadow: inset 2px 0 0 var(--tertiary);
  color: var(--dark);
  font-weight: 600;
}

.family-nav .fn-count {
  margin-left: auto;
  font-family: var(--codeFont);
  font-size: 0.72rem;
  font-weight: 400;
  color: var(--gray);
}

/* Level 1 reads like the site's headings: Newsreader, a little larger.
   A leaf at level 1 (Start) is indented to line up with the folded ones. */
.family-nav .fn-l1 {
  margin-bottom: 0.2rem;
}

.family-nav .fn-l1 > .fn-link,
.family-nav .fn-l1 > details > summary > .fn-link {
  font-family: var(--headerFont);
  font-size: 1.08rem;
  font-weight: 500;
  color: var(--dark);
}

.family-nav .fn-l1 > .fn-link {
  margin-left: 1.01rem;
}

.family-nav .fn-l1 ul {
  margin: 0.1rem 0 0.35rem 0.3rem;
  padding-left: 0.6rem;
  border-left: 1px solid var(--lightgray);
}

.family-nav .fn-l2 .fn-link {
  font-size: 0.92rem;
}

.family-nav .fn-l2 > .fn-link {
  margin-left: 1.01rem;
}

.family-nav .fn-l3 .fn-link {
  font-size: 0.87rem;
}

/* Recent carries the site's live dot: this is what is moving now. */
.family-nav .fn-l1[data-key="recent"] > details > summary > .fn-link .fn-label::after {
  content: "";
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-left: 0.45rem;
  border-radius: 50%;
  background: var(--tertiary);
  vertical-align: middle;
  animation: fn-pulse 2s ease-in-out infinite;
}

@keyframes fn-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

@media (prefers-reduced-motion: reduce) {
  .family-nav * { animation: none !important; transition: none !important; }
}

/* On a phone the sidebar is a row above the page; the navigation folds
   behind a Menu button there. */
@media all and (max-width: 800px) {
  .family-nav {
    flex-basis: 100%;
    margin-top: 0;
  }

  .family-nav .fn-menu > summary {
    display: inline-flex;
    min-height: 2.5rem;
    padding: 0 0.9rem;
    border: 1px solid var(--lightgray);
    border-radius: 8px;
    font-weight: 600;
    color: var(--dark);
  }

  .family-nav .fn-menu[open] > .fn-tree {
    margin-top: 0.6rem;
  }
}
`

export default (() => FamilyNav) satisfies QuartzComponentConstructor
