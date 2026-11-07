import type * as Preset from "@docusaurus/preset-classic";
import type { Config } from "@docusaurus/types";

const config: Config = {
  title: "ArcReel 文档中心",
  tagline: "开源、自托管的 AI 视频创作平台",
  favicon: "img/favicon.ico",

  url: "https://docs.arc-reel.com",
  baseUrl: "/",

  organizationName: "ArcReel",
  projectName: "ArcReel",

  onBrokenLinks: "throw",
  onBrokenAnchors: "throw",

  markdown: {
    // Parse .md as CommonMark and only .mdx as MDX: angle-bracket fragments such as `<1.0.0` and `<域名>` in documents
    // become JSX and fail MDX compilation; CONTRIBUTING.md must also remain readable unchanged on GitHub
    format: "detect",
    // Render ```mermaid fences as diagrams with @docusaurus/theme-mermaid
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: "throw",
    },
  },

  i18n: {
    defaultLocale: "zh-Hans",
    locales: ["zh-Hans", "en"],
  },

  presets: [
    [
      "classic",
      {
        docs: {
          // docs-only mode mounts documents at the site root, so src/pages/index.* must not exist (route conflict)
          routeBasePath: "/",
          sidebarPath: "./sidebars.ts",
          editUrl: "https://github.com/ArcReel/ArcReel/tree/main/website/",
        },
        blog: false,
        theme: {
          customCss: "./src/css/custom.css",
        },
      } satisfies Preset.Options,
    ],
  ],

  themes: [
    "@docusaurus/theme-mermaid",
    [
      "@easyops-cn/docusaurus-search-local",
      {
        indexBlog: false,
        // In docs-only mode, match the docs routeBasePath or the index is empty
        docsRouteBasePath: "/",
        language: ["en", "zh"],
        hashed: true,
        highlightSearchTermsOnTargetPage: true,
      },
    ],
  ],

  themeConfig: {
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: "ArcReel",
      logo: {
        alt: "ArcReel",
        src: "img/logo.svg",
        srcDark: "img/logo-dark.svg",
      },
      items: [
        {
          type: "custom-sponsors",
          position: "right",
        },
        {
          type: "localeDropdown",
          position: "right",
        },
        {
          href: "https://github.com/ArcReel/ArcReel",
          label: "GitHub 仓库",
          position: "right",
        },
      ],
    },
    footer: {
      style: "dark",
      links: [
        {
          title: "资源",
          items: [
            { label: "ArcReel 官网", href: "https://arc-reel.com" },
            { label: "GitHub 仓库", href: "https://github.com/ArcReel/ArcReel" },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} ArcReel. Licensed under AGPL-3.0.`,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
