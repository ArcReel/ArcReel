# Docusaurus i18n 机制与翻译滞后追踪调研

背景：文档站以中文为唯一写作源，英文由 AI 自译且无人工校对，单人维护、单版本（不启用 Docusaurus versioning）。本文为该双语工作流收集事实依据。

官方机制部分依据 Docusaurus 3.9.2 官方文档（经 context7 查询）与 `facebook/docusaurus` v3.9.2 源码；社区实践部分依据项目仓库、官方工具文档与实践者一手记录。

---

## 1. Docusaurus i18n 机制

### 1.1 配置与目录结构

站点在 `docusaurus.config.js` 的 `i18n` 字段声明 `defaultLocale` 与 `locales`，每个 locale 可通过 `localeConfigs` 配置 `label`、`htmlLang`、`direction` 等（多域名部署时还可配 `url`）。（[i18n/introduction](https://docusaurus.io/docs/3.9.2/i18n/introduction)）

翻译文件统一放在 `website/i18n/[locale]/[pluginName]/...`，每个 locale、每个插件各占一个子目录；多实例插件路径为 `i18n/[locale]/[pluginName]-[pluginId]/...`。典型结构（[i18n/introduction](https://docusaurus.io/docs/3.9.2/i18n/introduction)）：

```
website/i18n
└── en
    ├── code.json                          # React 代码与主题代码中的文本标签
    ├── docusaurus-plugin-content-docs
    │   ├── current                        # docs/ 的译文（相对路径需与源一致）
    │   │   ├── doc1.md
    │   │   └── doc2.mdx
    │   └── current.json                   # sidebar 分类 label 等 docs 插件文案
    ├── docusaurus-plugin-content-blog     # blog 译文（如启用）
    └── docusaurus-theme-classic
        ├── navbar.json                    # 导航栏文案
        └── footer.json                    # 页脚文案
```

翻译数据分两类：

- **Markdown/MDX 内容** — 将源文件按相同相对路径复制到 `i18n/<locale>/docusaurus-plugin-content-docs/current/` 后翻译（官方教程即 `cp -r docs/. i18n/en/docusaurus-plugin-content-docs/current`）。pages 插件同理复制到 `docusaurus-plugin-content-pages`，且只有 `.md`/`.mdx` 会被处理，React 页面走 JSON。（[i18n/tutorial](https://docusaurus.io/docs/3.9.2/i18n/tutorial)）
- **JSON 文案** — `docusaurus write-translations --locale en` 抽取并初始化 React 代码文本、navbar/footer、`sidebars.js` 分类 label（落在 `current.json`）等；重复运行只追加新 key，默认不覆盖已有译文。（[i18n/git](https://docusaurus.io/docs/3.9.2/i18n/git)、[i18n/tutorial](https://docusaurus.io/docs/3.9.2/i18n/tutorial)）

### 1.2 缺页 fallback 行为（源码级确认）

官方文档页面没有明确成文的 fallback 描述，以下行为从 v3.9.2 源码确认：

- **文档集合由源目录决定**：docs 插件枚举文档时只 glob `contentPath`（即源 `docs/` 目录），不 glob 本地化目录（[`docs.ts` `readVersionDocs` L62-76](https://github.com/facebook/docusaurus/blob/v3.9.2/packages/docusaurus-plugin-content-docs/src/docs.ts)）。
- **逐文件本地化优先、缺失回退源文件**：读取每个文档时按 `[contentPathLocalized, contentPath]` 的优先序查找第一个存在该相对路径的目录（[`docs.ts` `readDocFile` L49-60](https://github.com/facebook/docusaurus/blob/v3.9.2/packages/docusaurus-plugin-content-docs/src/docs.ts)，[`dataFileUtils.ts` `getContentPathList` L70-79](https://github.com/facebook/docusaurus/blob/v3.9.2/packages/docusaurus-utils/src/dataFileUtils.ts)，源码注释即「For all data, we look in the localized folder in priority」）。

对本项目的直接推论：

1. **英文缺页不会 404**：英文站包含全部中文源文档；某篇没有英文译文时，`/en/...` 对应页面直接渲染中文原文。fallback 是逐文件的，无需任何配置，也无法按页关闭。
2. **只存在于 `i18n/en/` 而源 `docs/` 没有的文件会被忽略**：文档集合以源目录为准，孤儿译文不产生页面（删除中文源文档后须同步删除英文译文，否则留下死文件但不报错）。
3. **JSON 文案同样逐 key 回退**：运行时取 `codeTranslations[id ?? message] ?? message`，缺失的 key 直接显示默认文案（[`Translate.tsx` L27](https://github.com/facebook/docusaurus/blob/v3.9.2/packages/docusaurus/src/client/exports/Translate.tsx)）。主题自带的通用标签（如分页的 Next/Previous）另有官方默认翻译。（[i18n/tutorial](https://docusaurus.io/docs/i18n/tutorial)）

### 1.3 frontmatter / sidebar / 静态资产要求

- **相对路径必须与源一致**：fallback 与译文匹配都按「相对 content root 的路径」进行，译文文件不能改名或移动。
- **frontmatter 的 `id`/`slug` 应保持与源一致**：slug 翻译是官方明确的 non-goal（[i18n/introduction](https://docusaurus.io/docs/3.9.2/i18n/introduction) 的 i18n goals 列出不支持 "Translation of slugs"），自动 locale 检测同为 non-goal；`title`、`description`、`sidebar_label` 等展示性 frontmatter 正常翻译。
- **sidebar 不复制**：`sidebars.js` 只有一份，分类 label 的译文经 `write-translations` 落在 `current.json`；label 重复时需给 sidebar item 配 `key` 以避免 i18n 翻译 key 冲突。（[sidebar](https://docusaurus.io/docs/3.9.2/sidebar)）
- **静态资产**：`static/` 目录全 locale 共享，Markdown 中 `/img/...` 绝对路径引用会被转换为 `require()` 调用统一解析（[static-assets](https://docusaurus.io/docs/3.9.2/static-assets)）。相对路径引用的 colocated 资产相对「实际被解析到的那个文件」定位——译文文件若用相对路径引图，需要在译文目录放置同样的相对资产（此推论来自 1.2 的路径解析机制，未逐一实测；规避办法是文档内图片一律用 `/img/...` 绝对路径）。

### 1.4 构建与本地预览

- **开发预览**：`npm run start -- --locale en`。每个 locale 是独立的 SPA，dev server 一次只能跑一个 locale，无法同时预览双语。（[i18n/tutorial](https://docusaurus.io/docs/3.9.2/i18n/tutorial)）
- **构建**：`npm run build` 一次构建全部 locale——单域名部署形态为默认 locale 在 `build/`、其余 locale 在 `build/<locale>/` 子目录，各自是完整 SPA；官方建议 host 把 `/en/*` 的 404 重定向到 `/en/404.html` 以本地化 404 页。`npm run build -- --locale en` 单独构建某 locale（多域名部署用，不加 `/en/` URL 前缀，可配 `localeConfigs[<locale>].url`）。（[i18n/tutorial](https://docusaurus.io/docs/3.9.2/i18n/tutorial)）

### 1.5 官方推荐的两条工作流

官方文档给出两条路线并列陈述取舍（[i18n/git](https://docusaurus.io/docs/3.9.2/i18n/git)、[i18n/crowdin](https://docusaurus.io/docs/3.9.2/i18n/crowdin)）：

- **Git 路线**：译文直接进版本库。免费、上手快、对开发者低摩擦；React、Vue.js、MDN、TypeScript、Nuxt.js 等大型项目均用 Git 管理翻译。官方明说其代价：「编辑了未翻译的源 Markdown 后，你需要自己把改动手工 backport 到对应译文文件」——即 **Docusaurus 自身不提供任何翻译滞后追踪机制**，同步责任完全在维护者。
- **Crowdin 路线**：SaaS 平台，开源项目有免费 plan，CLI 可自动化（官方示例 `crowdin:sync` = `docusaurus write-translations && crowdin upload && crowdin download`）。官方特别提示其 VCS（GitHub 等）集成**不推荐使用**：可靠性差、Git→Crowdin 方向手动、已有 Markdown 译文与源的匹配不可靠、并发编辑可能丢数据。

---

## 2. 翻译滞后追踪先例

（待补：社区实践调研结果）

---

## 3. AI 批量翻译 Markdown 的流水线注意事项

（待补：社区实践调研结果）

---

## 4. 对 ArcReel 工作流的启示

（待补：综合结论）
