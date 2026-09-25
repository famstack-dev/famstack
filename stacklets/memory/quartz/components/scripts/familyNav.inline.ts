import { FullSlug, pathToRoot, resolveRelative, simplifySlug } from "../../util/path"

// Draws the family navigation from static/family-nav.json, which the
// FamilyLists emitter rewrites on every build. Fetched once per page load
// and reused across the SPA's page changes.

interface NavNode {
  key?: string
  label: string
  slug?: string
  count?: number
  open?: boolean
  children?: NavNode[]
}

let data: Promise<NavNode[]> | undefined

function load(here: FullSlug): Promise<NavNode[]> {
  data ??= fetch(new URL(`${pathToRoot(here)}/static/family-nav.json`, window.location.href).toString())
    .then((r) => r.json())
    .catch(() => [])
  return data
}

const same = (a: string | undefined, here: FullSlug) =>
  !!a && simplifySlug(a as FullSlug) === simplifySlug(here)
const contains = (n: NavNode, here: FullSlug): boolean =>
  same(n.slug, here) || (n.children ?? []).some((c) => contains(c, here))

function item(n: NavNode, depth: number, here: FullSlug): HTMLLIElement {
  const li = document.createElement("li")
  li.className = `fn-l${depth}`
  if (n.key) li.dataset.key = n.key

  const link = document.createElement(n.slug ? "a" : "span")
  link.className = "fn-link"
  const text = document.createElement("span")
  text.className = "fn-label"
  text.textContent = n.label
  link.append(text)
  if (n.count) {
    const count = document.createElement("span")
    count.className = "fn-count"
    count.textContent = String(n.count)
    link.append(count)
  }
  if (n.slug) {
    ;(link as HTMLAnchorElement).href = resolveRelative(here, n.slug as FullSlug)
    if (same(n.slug, here)) link.setAttribute("aria-current", "page")
  }

  if (!n.children?.length) {
    li.append(link)
    return li
  }
  const details = document.createElement("details")
  details.open = !!n.open || contains(n, here)
  const summary = document.createElement("summary")
  summary.append(link)
  const ul = document.createElement("ul")
  for (const c of n.children) ul.append(item(c, depth + 1, here))
  details.append(summary, ul)
  li.append(details)
  return li
}

document.addEventListener("nav", async (e: CustomEventMap["nav"]) => {
  const here = e.detail.url
  const nav = await load(here)
  for (const root of document.querySelectorAll<HTMLElement>(".family-nav")) {
    const tree = root.querySelector(".fn-tree")!
    tree.replaceChildren(...nav.map((n) => item(n, 1, here)))
    // On a phone the navigation starts folded behind its Menu button.
    const menu = root.querySelector<HTMLDetailsElement>(".fn-menu")!
    menu.open = !window.matchMedia("(max-width: 800px)").matches
  }
})
