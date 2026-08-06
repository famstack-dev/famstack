import { pathToRoot } from "../util/path"
import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import { classNames } from "../util/lang"
import { i18n } from "../i18n"

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
      <span class="fs-brandmark" aria-label="famstack">
        fam
        <span class="fs-brand-accent">
          st<span class="fs-brand-a">a</span>ck
        </span>
      </span>
    </div>
  )
}

FamstackTitle.css = `
.famstack-title {
  display: flex;
  flex-direction: column;
  /* Room for the title's underline to sit clear of the wordmark. */
  gap: 0.5rem;
}

.famstack-title .page-title {
  font-size: 1.6rem;
  margin: 0;
  font-family: var(--titleFont);
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1.1;
}

/* Underlined, because it is the way back to the front page from
   anywhere and should look like somewhere you can go. Drawn as a
   border rather than text-decoration so it sits clear of the
   descenders in a name like Simpsons. */
.famstack-title .page-title > a {
  border-bottom: 2px solid var(--secondary);
  padding-bottom: 2px;
  transition: border-color 0.2s ease;
}

.famstack-title .page-title > a:hover {
  border-bottom-color: var(--tertiary);
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
  .famstack-title .fs-brandmark { display: none; }
  .famstack-title .page-title { font-size: 1.3rem; }
}
`

export default (() => FamstackTitle) satisfies QuartzComponentConstructor
