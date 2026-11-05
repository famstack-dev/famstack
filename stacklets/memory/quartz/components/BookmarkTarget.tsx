import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import { classNames } from "../util/lang"
import { bookmarkLink } from "./bookmarkLink"
import { L } from "./familyModel"

// NEW COMPONENT (not an upstream override): the page a bookmark points to,
// shown between the page head (FamilyHead) and the card's text.
//
//   ┌───────────────────────────────────────────────────────────┐
//   ┃ BOOKMARKED PAGE                                           │
//   ┃ en.wikipedia.org                                          │
//   ┃ en.wikipedia.org/wiki/List_of_The_Simpsons_episodes… ↗    │
//   └───────────────────────────────────────────────────────────┘
//
// A bookmark is kept for the page it points to, and its summary only
// describes that page. So the link comes first and is the largest thing
// below the title. The whole block is the link, which gives a phone a
// large target. It renders nothing for any other page.
//
// The site is the block's name, because the card holds no title of the
// bookmarked page (bookmarkLink.ts). The address under it says which page
// of the site it is.

const BookmarkTarget: QuartzComponent = ({ fileData, displayClass }: QuartzComponentProps) => {
  const target = bookmarkLink(fileData.frontmatter as Record<string, unknown> | undefined)
  if (!target) return null

  return (
    // A page of the web: it opens in its own tab, and rel keeps it from
    // reaching back into the wiki's tab.
    <a
      class={classNames(displayClass, "bookmark-target")}
      href={target.href}
      target="_blank"
      rel="noopener noreferrer"
    >
      <span class="bt-label">{L.bookmarkedPage}</span>
      <span class="bt-site">{target.site}</span>
      <span class="bt-address">
        {target.address} <span class="bt-arrow">↗</span>
      </span>
    </a>
  )
}

BookmarkTarget.css = `
a.bookmark-target {
  display: block;
  margin: 0 0 1.6rem;
  padding: 0.95rem 1.2rem 1.05rem;
  border: 1px solid rgba(61, 143, 160, 0.35);
  border-left: 4px solid var(--secondary);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.55);
  color: var(--darkgray);
  font-weight: normal;
  text-decoration: none;
}

a.bookmark-target:hover {
  border-color: var(--tertiary);
  background: rgba(255, 255, 255, 0.8);
}

.bookmark-target .bt-label {
  display: block;
  font-family: var(--codeFont);
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--secondary);
}

.bookmark-target .bt-site {
  display: block;
  margin: 0.25rem 0 0.2rem;
  font-family: var(--headerFont);
  font-size: clamp(1.3rem, 2.6vw, 1.6rem);
  line-height: 1.2;
  color: var(--dark);
}

.bookmark-target .bt-address {
  display: block;
  font-family: var(--codeFont);
  font-size: 0.84rem;
  color: var(--secondary);
  overflow-wrap: anywhere;
}

a.bookmark-target:hover .bt-arrow {
  color: var(--tertiary);
}
`

export default (() => BookmarkTarget) satisfies QuartzComponentConstructor
