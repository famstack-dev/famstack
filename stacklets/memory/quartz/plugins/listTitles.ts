import { QuartzTransformerPlugin } from "../types"
import { listTitle } from "../../components/familyModel"

// NEW TRANSFORMER (not an upstream override).
//
// A family list is titled the way the family named it, often for what it
// is about: "Camping". On its own page that reads as the topic, so a list
// page says it is a list ("Camping Liste"). The title is set here, once,
// so the page heading, the browser tab, search and the menu all agree.
// The file in the vault keeps the family's own title.

export const ListTitles: QuartzTransformerPlugin = () => ({
  name: "ListTitles",
  markdownPlugins() {
    return [
      () => (_tree, file) => {
        const fm = file.data.frontmatter
        if (fm?.type === "list" && typeof fm.title === "string") fm.title = listTitle(fm.title)
      },
    ]
  },
})
