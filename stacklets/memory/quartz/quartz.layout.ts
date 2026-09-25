/**
 * quartz.layout.ts — family wiki
 *
 * Tracks the upstream v4 layout (graph + backlinks + search) with
 * one change: the footer's links table points at the Forgejo
 * `family/memory` repo, so every page surfaces the path back to
 * the canonical source of truth.
 */

import { PageLayout, SharedLayout } from "./quartz/cfg"
import * as Component from "./quartz/components"
// Our own components, imported directly rather than through the
// `Component` namespace so we do not have to overlay upstream's
// components/index.ts as well. Quartz collects a component's `.css` by
// walking the layout, so a direct import styles itself just the same.
import FamstackTitle from "./quartz/components/FamstackTitle"
import Welcome from "./quartz/components/Welcome"
import FamilyNav from "./quartz/components/FamilyNav"
import FamilyCrumbs from "./quartz/components/FamilyCrumbs"
import FamilyHead from "./quartz/components/FamilyHead"
import { L } from "./quartz/components/familyModel"

// `CODE_URL` is set in the container env from {code_url} — the
// user-facing Forgejo URL. Empty falls back to a `#` placeholder so
// the footer still renders even if env wiring drifts.
const codeUrl = process.env.CODE_URL || ""
const repoUrl = codeUrl ? `${codeUrl.replace(/\/$/, "")}/family/memory` : "#"

export const sharedPageComponents: SharedLayout = {
  head: Component.Head(),
  header: [],
  afterBody: [],
  footer: Component.Footer({
    links: {
      [L.editOnForgejo]: repoUrl,
    },
  }),
}

// Single-page layout (one note): search on the left, graph + ToC +
// backlinks on the right. This is the layout that makes the vault feel
// like a wiki rather than a folder dump.
//
// No dark mode toggle. famstack has no dark palette yet, so the toggle
// could only swap parchment for parchment. It comes back with the
// palette, not before.
export const defaultContentPageLayout: PageLayout = {
  beforeBody: [
    // Breadcrumbs, a label, one title, and the page's topics and people.
    // See FamilyCrumbs.tsx and FamilyHead.tsx.
    Component.ConditionalRender({
      component: FamilyCrumbs(),
      condition: (page) => page.fileData.slug !== "index",
    }),
    FamilyHead(),
    // The greeting belongs to the front door only.
    Component.ConditionalRender({
      component: Welcome(),
      condition: (page) => page.fileData.slug === "index",
    }),
  ],
  left: [
    FamstackTitle(),
    Component.MobileOnly(Component.Spacer()),
    Component.Flex({
      components: [{ Component: Component.Search(), grow: true }],
    }),
    // The family's structure, not the vault's folders. See FamilyNav.tsx.
    FamilyNav(),
  ],
  right: [
    Component.Graph(),
    Component.DesktopOnly(Component.TableOfContents()),
    Component.Backlinks(),
  ],
}

// List page (folder / tag index). Same sidebar shape, no graph on
// the right — list pages don't have meaningful in-vault links to
// graph.
export const defaultListPageLayout: PageLayout = {
  beforeBody: [
    FamilyCrumbs(),
    FamilyHead(),
  ],
  left: [
    FamstackTitle(),
    Component.MobileOnly(Component.Spacer()),
    Component.Flex({
      components: [{ Component: Component.Search(), grow: true }],
    }),
    // The family's structure, not the vault's folders. See FamilyNav.tsx.
    FamilyNav(),
  ],
  right: [],
}
