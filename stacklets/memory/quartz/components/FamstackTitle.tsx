import { pathToRoot } from "../util/path"
import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import { classNames } from "../util/lang"
import { i18n } from "../i18n"
import { L } from "./familyModel"

// NEW COMPONENT (not an upstream override) — the sidebar lockup that
// replaces PageTitle.
//
// Two names, deliberately in this order. The wiki belongs to the
// family, so its own name leads; famstack is the software underneath
// and sits below in small type, the way a maker's mark does. Getting
// that backwards would put our branding on their memories.
//
// The wordmark repeats famstack.dev's: "fam" in slate, "stack" in lava,
// with the a lifted onto two teal dots. It is built from styled spans
// rather than an image so it inherits the page's colours and stays
// sharp at any zoom, and so there is no asset to keep in sync.
const FamstackTitle: QuartzComponent = ({ fileData, cfg, displayClass }: QuartzComponentProps) => {
  const title = cfg?.pageTitle ?? i18n(cfg.locale).propertyDefaults.title
  const baseDir = pathToRoot(fileData.slug!)
  return (
    <div class={classNames(displayClass, "famstack-title")}>
      <h2 class="page-title">
        <a href={baseDir}>{title}</a>
      </h2>
      <p class="fs-byline">
        <span class="fs-brandmark" aria-label="famstack">
          fam
          <span class="fs-brand-accent">
            st<span class="fs-brand-a">a</span>ck
          </span>
        </span>
        <span class="fs-tag">{L.familyWiki}</span>
      </p>
    </div>
  )
}

FamstackTitle.css = `
.famstack-title {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding-bottom: 0.8rem;
  border-bottom: 1px solid rgba(61, 143, 160, 0.14);
}

/* The family's name in the headline face, like the page titles. It is
   the way back to the front page from anywhere; the hover says so. */
.famstack-title .page-title {
  font-size: 1.65rem;
  margin: 0;
  font-family: var(--headerFont);
  font-weight: 500;
  letter-spacing: -0.02em;
  line-height: 1.1;
}

.famstack-title .page-title > a {
  color: var(--dark);
  font-weight: 500;
  transition: color 0.2s ease;
}

.famstack-title .page-title > a:hover {
  color: var(--tertiary);
}

.famstack-title .fs-byline {
  display: flex;
  align-items: baseline;
  gap: 0.45rem;
  margin: 0;
}

.famstack-title .fs-tag {
  font-family: var(--codeFont);
  font-size: 0.68rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--gray);
}

.famstack-title .fs-tag::before {
  content: "·";
  margin-right: 0.45rem;
}

.famstack-title .fs-brandmark {
  font-family: var(--bodyFont);
  font-weight: 600;
  font-size: 0.78rem;
  letter-spacing: 0.01em;
  line-height: 1;
  color: var(--darkgray);
  user-select: none;
}

.famstack-title .fs-brand-accent {
  color: var(--tertiary);
}

/* The raised a, standing on two teal dots. */
.famstack-title .fs-brand-a {
  position: relative;
  display: inline-block;
  vertical-align: baseline;
  top: -0.2em;
}

.famstack-title .fs-brand-a::before,
.famstack-title .fs-brand-a::after {
  content: "";
  position: absolute;
  width: 0.15em;
  height: 0.15em;
  border-radius: 50%;
  background: var(--secondary);
  bottom: -0.1em;
}

.famstack-title .fs-brand-a::before { left: 0.08em; }
.famstack-title .fs-brand-a::after { right: 0.1em; }

/* On mobile the sidebar becomes a header row and space is tight, so
   the maker's mark steps aside and the wiki name carries it alone. */
@media all and (max-width: 800px) {
  .famstack-title .fs-byline { display: none; }
  .famstack-title { padding-bottom: 0; border-bottom: 0; }
  .famstack-title .page-title { font-size: 1.2rem; }
}
`

export default (() => FamstackTitle) satisfies QuartzComponentConstructor
