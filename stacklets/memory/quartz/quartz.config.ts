/**
 * quartz.config.ts — family wiki
 *
 * Overlays the upstream Quartz v4 config with the bits that matter
 * for famstack: site title, edit-on-Forgejo origin, no analytics,
 * faster builds. Everything not touched here intentionally tracks
 * upstream defaults so a Quartz bump only needs eyes on the diff.
 *
 * The container runs `npx quartz build --serve` on every start, so
 * `process.env.*` reads here happen at startup — env changes in
 * `stacklet.toml` take effect on the next `stack up memory`.
 */

import { QuartzConfig } from "./quartz/cfg"
import * as Plugin from "./quartz/plugins"

// Runtime env. `WIKI_HOST` is "wiki.<domain>" in domain mode and
// the empty-domain rendering "wiki." in port mode — we treat the
// trailing-dot form as missing and fall back to the LAN IP so
// absolute URLs in the sitemap stay reachable in either mode.
const wikiHost = process.env.WIKI_HOST?.replace(/^https?:\/\//, "") || ""
const wikiIp = process.env.WIKI_IP || ""
const wikiPort = process.env.WIKI_PORT || "42070"
const haveRealHost = wikiHost && !wikiHost.endsWith(".")
const baseUrl = haveRealHost ? wikiHost : `${wikiIp || "localhost"}:${wikiPort}`

// The household's own name for the site, from stack.toml [core]
// stack_owner by way of WIKI_TITLE ("The Simpsons"). A family reading
// their own wiki should see themselves at the top of it, not a product
// noun. Instances installed before stack_owner existed send nothing, so
// the generic title stays as the fallback.
const wikiTitle = process.env.WIKI_TITLE?.trim() || "Family Memory"

const config: QuartzConfig = {
  configuration: {
    pageTitle: wikiTitle,
    pageTitleSuffix: "",
    enableSPA: true,
    enablePopovers: true,
    // No analytics — this is a private family site, the upstream
    // Plausible default would leak page views to a third party.
    analytics: null,
    locale: "en-US",
    baseUrl,
    // Skip git internals and Obsidian config dirs. The vault is a
    // real git clone, not a stripped checkout, so `.git` matters.
    ignorePatterns: [".git", ".obsidian", "private", "templates"],
    defaultDateType: "modified",
    theme: {
      // Self-hosted. The comment above about analytics applies with more
      // force to fonts: Google Fonts would hand Google the IP and the
      // page path of every read, on the surface holding the family's
      // most private material. "local" makes Quartz emit nothing at all
      // (it is a no-op branch upstream, by design) — the @font-face
      // rules and the files live in quartz/fonts.scss and static/fonts.
      fontOrigin: "local",
      cdnCaching: false,
      // Four slots, four famstack roles. The names are the
      // @fontsource-variable family names, which is what the vendored
      // files declare and what famstack.dev already renders with.
      typography: {
        title: "Space Grotesk Variable",
        header: "Newsreader Variable",
        body: "Inter Variable",
        code: "JetBrains Mono Variable",
      },
      // The famstack palette, mapped onto the nine slots Quartz gives
      // us. `secondary` is the link colour and it gets teal, not lava:
      // DESIGN.md reserves lava for calls to action but makes an
      // exception for article and guide body links, and a wiki page is
      // body text end to end. Lava stays on hover, via `tertiary`.
      //
      // Light mode only for v1. DESIGN.md has no dark palette yet and
      // says not to port one without a full pass, so darkMode repeats
      // lightMode rather than inventing colours, and the toggle is gone
      // from both layouts.
      colors: {
        lightMode: {
          light: "#EFEEE6", // --parchment, the page
          lightgray: "#e6e4db", // --bg-subtle, borders and inline code
          gray: "#6a7d82", // --text-muted, labels and line numbers
          darkgray: "#3A4447", // --charcoal, body text
          dark: "#161F24", // --slate, headings
          secondary: "#3D8FA0", // --teal, links
          tertiary: "#F07D45", // --lava, hover and selection
          highlight: "rgba(61, 143, 160, 0.14)", // teal wash behind internal links
          textHighlight: "#F5C842aa", // --mark-line
        },
        darkMode: {
          light: "#EFEEE6",
          lightgray: "#e6e4db",
          gray: "#6a7d82",
          darkgray: "#3A4447",
          dark: "#161F24",
          secondary: "#3D8FA0",
          tertiary: "#F07D45",
          highlight: "rgba(61, 143, 160, 0.14)",
          textHighlight: "#F5C842aa",
        },
      },
    },
  },
  plugins: {
    transformers: [
      Plugin.FrontMatter(),
      // The vault is a git repo — let git timestamps win over
      // filesystem mtime so a fresh clone shows real commit dates.
      Plugin.CreatedModifiedDate({
        priority: ["frontmatter", "git", "filesystem"],
      }),
      // github-dark in light mode is not a typo. custom.scss puts code
      // blocks on slate, the way famstack.dev does, and a light token
      // set on a dark block is unreadable. `keepBackground: false`
      // leaves the background to our CSS and takes only the colours.
      Plugin.SyntaxHighlighting({
        theme: { light: "github-dark", dark: "github-dark" },
        keepBackground: false,
      }),
      Plugin.ObsidianFlavoredMarkdown({ enableInHtmlEmbed: false }),
      Plugin.GitHubFlavoredMarkdown(),
      Plugin.TableOfContents(),
      Plugin.CrawlLinks({ markdownLinkResolution: "absolute" }),
      Plugin.Description(),
      // MathJax, not KaTeX, and the reason is privacy rather than
      // typesetting. Quartz's KaTeX engine attaches a stylesheet and a
      // script from cdn.jsdelivr.net to *every* page, whether or not it
      // contains any maths — see the unconditional `externalResources()`
      // in quartz/plugins/transformers/latex.ts. The MathJax engine
      // declares none and renders to SVG at build time instead. Nobody
      // in a family vault writes LaTeX often, but everybody would have
      // been calling jsdelivr on every page load.
      Plugin.Latex({ renderEngine: "mathjax" }),
    ],
    filters: [Plugin.RemoveDrafts()],
    emitters: [
      Plugin.AliasRedirects(),
      Plugin.ComponentResources(),
      Plugin.ContentPage(),
      Plugin.FolderPage(),
      Plugin.TagPage(),
      Plugin.ContentIndex({ enableSiteMap: true, enableRSS: true }),
      Plugin.Assets(),
      Plugin.Static(),
      Plugin.Favicon(),
      Plugin.NotFoundPage(),
      // CustomOgImages does a heavy per-page render. Skipped for the
      // wiki — every container start rebuilds the whole site and
      // OG cards aren't useful for a LAN-only wiki.
    ],
  },
}

export default config
