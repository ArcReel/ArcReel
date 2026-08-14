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
    // .md 按 CommonMark 解析，只有 .mdx 走 MDX：文档正文里的 `<1.0.0`、`<域名>` 等
    // 尖括号片段在 MDX 下会被当成 JSX 而编译失败，且 CONTRIBUTING.md 还要在 GitHub 上原样可读
    format: "detect",
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
          // docs-only 模式：文档直接挂在站点根，因此 src/pages/index.* 不能存在（路由冲突）
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
    [
      "@easyops-cn/docusaurus-search-local",
      {
        indexBlog: false,
        // docs-only 模式下须与 docs 的 routeBasePath 一致，否则索引为空
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
        src: "img/logo.png",
      },
      items: [
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
