/**
 * quartz.config.ts — memory garden
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

// Runtime env. `GARDEN_HOST` is "memory.<domain>" in domain mode and
// the empty-domain rendering "memory." in port mode — we treat the
// trailing-dot form as missing and fall back to the LAN IP so
// absolute URLs in the sitemap stay reachable in either mode.
const gardenHost = process.env.GARDEN_HOST?.replace(/^https?:\/\//, "") || ""
const gardenIp = process.env.GARDEN_IP || ""
const gardenPort = process.env.GARDEN_PORT || "42070"
const haveRealHost = gardenHost && !gardenHost.endsWith(".")
const baseUrl = haveRealHost ? gardenHost : `${gardenIp || "localhost"}:${gardenPort}`

const config: QuartzConfig = {
  configuration: {
    pageTitle: "Family Memory",
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
      fontOrigin: "googleFonts",
      cdnCaching: true,
      typography: {
        header: "Schibsted Grotesk",
        body: "Source Sans Pro",
        code: "IBM Plex Mono",
      },
      colors: {
        lightMode: {
          light: "#faf8f8",
          lightgray: "#e5e5e5",
          gray: "#b8b8b8",
          darkgray: "#4e4e4e",
          dark: "#2b2b2b",
          secondary: "#284b63",
          tertiary: "#84a59d",
          highlight: "rgba(143, 159, 169, 0.15)",
          textHighlight: "#fff23688",
        },
        darkMode: {
          light: "#161618",
          lightgray: "#393639",
          gray: "#646464",
          darkgray: "#d4d4d4",
          dark: "#ebebec",
          secondary: "#7b97aa",
          tertiary: "#84a59d",
          highlight: "rgba(143, 159, 169, 0.15)",
          textHighlight: "#b3aa0288",
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
      Plugin.SyntaxHighlighting({
        theme: { light: "github-light", dark: "github-dark" },
        keepBackground: false,
      }),
      Plugin.ObsidianFlavoredMarkdown({ enableInHtmlEmbed: false }),
      Plugin.GitHubFlavoredMarkdown(),
      Plugin.TableOfContents(),
      Plugin.CrawlLinks({ markdownLinkResolution: "shortest" }),
      Plugin.Description(),
      Plugin.Latex({ renderEngine: "katex" }),
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
      // garden — every container start rebuilds the whole site and
      // OG cards aren't useful for a LAN-only wiki.
    ],
  },
}

export default config
