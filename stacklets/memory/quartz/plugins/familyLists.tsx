import { QuartzEmitterPlugin } from "../types"
import { QuartzComponentProps } from "../../components/types"
import HeaderConstructor from "../../components/Header"
import BodyConstructor from "../../components/Body"
import { pageResources, renderPage } from "../../components/renderPage"
import { defaultProcessedContent } from "../vfile"
import { FullPageLayout } from "../../cfg"
import { FullSlug, pathToRoot } from "../../util/path"
import { defaultListPageLayout, sharedPageComponents } from "../../../quartz.layout"
import { write } from "./helpers"
import FamilyListContent from "../../components/FamilyList"
import { familyModel } from "../../components/familyModel"

// NEW EMITTER (not an upstream override), modelled on upstream's TagPage.
//
// Writes two things on every build:
//
//   * static/family-nav.json: the sidebar, read by FamilyNav in the
//     browser. `quartz --serve` re-renders only the pages that changed,
//     so a sidebar baked into each page would go stale everywhere else.
//     This emitter has no `partialEmit`, which makes Quartz run it in
//     full on every change, so the file is always current.
//   * lists/…: a page for every entry that is not a file in the vault:
//     topics without an about page, document types, needs attention,
//     all notes, all bookmarks.

export const FamilyLists: QuartzEmitterPlugin = () => {
  const opts: FullPageLayout = {
    ...sharedPageComponents,
    ...defaultListPageLayout,
    pageBody: FamilyListContent(),
  }
  const { head: Head, header, beforeBody, pageBody, afterBody, left, right, footer: Footer } = opts
  const Header = HeaderConstructor()
  const Body = BodyConstructor()

  return {
    name: "FamilyLists",
    getQuartzComponents() {
      return [Head, Header, Body, ...header, ...beforeBody, pageBody, ...afterBody, ...left, ...right, Footer]
    },
    async *emit(ctx, content, resources) {
      const allFiles = content.map((c) => c[1].data)
      const model = familyModel(ctx, allFiles)
      const cfg = ctx.cfg.configuration

      yield write({
        ctx,
        content: JSON.stringify(model.nav),
        slug: "static/family-nav" as FullSlug,
        ext: ".json",
      })

      for (const page of model.lists.values()) {
        const slug = page.slug as FullSlug
        const [tree, file] = defaultProcessedContent({
          slug,
          frontmatter: { title: page.title, tags: [] },
        })
        const externalResources = pageResources(pathToRoot(slug), resources)
        const componentData: QuartzComponentProps = {
          ctx,
          fileData: file.data,
          externalResources,
          cfg,
          children: [],
          tree,
          allFiles,
        }
        yield write({
          ctx,
          content: renderPage(cfg, slug, componentData, opts, externalResources),
          slug,
          ext: ".html",
        })
      }
    },
  }
}
