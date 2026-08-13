import type * as Preset from "@docusaurus/preset-classic";
import type { Config } from "@docusaurus/types";

const config: Config = {
  title: "ArcReel 文档",
  tagline: "开源、自托管的 AI 视频生产工作台",
  favicon: "img/favicon.ico",

  url: "https://docs.arc-reel.com",
  baseUrl: "/",

  organizationName: "ArcReel",
  projectName: "ArcReel",

  onBrokenLinks: "throw",
  onBrokenAnchors: "throw",

  markdown: {
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
          label: "GitHub",
          position: "right",
        },
      ],
    },
    footer: {
      style: "dark",
      links: [
        {
          title: "ArcReel",
          items: [
            { label: "官网", href: "https://arc-reel.com" },
            { label: "GitHub", href: "https://github.com/ArcReel/ArcReel" },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} ArcReel. Licensed under AGPL-3.0.`,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
