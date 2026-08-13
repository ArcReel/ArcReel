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

### 2.1 关键否定结论：Docusaurus 无内建同步支持

`write-translations` CLI 只生成 UI/插件字符串的 JSON，完全不涉及 Markdown 文档内容；维护者在 issue 中确认框架对「译文与源保持同步」没有任何内建支持（[facebook/docusaurus#8703](https://github.com/facebook/docusaurus/issues/8703)）。滞后追踪必须完全自建，没有第一方钩子可依托。

### 2.2 大型项目的实际做法

- **Kubernetes（kubernetes/website）**：本地化团队人工比对 git history；页面可基于 `lastmod` 时间戳（而非内容/commit hash）显示「译文可能过期」警告（[localization 贡献指南](https://kubernetes.io/docs/contribute/localization/)）。2026-06 的 SIG-Docs 博文批评时间戳/commit-diff 信号噪声大（琐碎本地编辑显得新鲜、纯装饰性源编辑显得过期），其原型改用 **Markdown 感知的分诊脚本**：对比源与译文的文档结构（标题、代码块、锚点）与技术内容（版本号、API 值），把每页分级为 Orphan / Strong / Moderate / No signal；理念是 AI 辅助人工分诊而非自动翻译（[Human-Centered Automation for Kubernetes Localization in the AI Era](https://www.kubernetes.dev/blog/2026/06/26/human-centered-automation-kubernetes-localization-ai-era/)）。未确认其存在 frontmatter hash 字段。
- **react.dev（reactjs/translations.react.dev）**：每语言独立 fork 仓库，bot 定期开「Merge changes from react.dev」PR 钉住上游 commit hash——滞后由 **git 历史本身**隐式追踪，不存任何字段；维护者被明确要求不得 squash-merge 这些 bot PR（会抹掉后续 diff 所需的 commit 轨迹）（[reactjs/translations.react.dev](https://github.com/reactjs/translations.react.dev)、[reactjs/fr.react.dev#448](https://github.com/reactjs/fr.react.dev/issues/448)）。该模式依赖 fork-per-language 仓库结构，与 Docusaurus 单仓 `i18n/` 布局不同构。
- **Vue.js**：org 化 fork-per-language + 同行评审，无文档化的滞后 hash 机制。

值得注意：调研**未找到**「frontmatter 记录源文件 hash」的成熟社区先例——最接近的两个是 lockfile 指纹（见下）与 react.dev 的 git 历史法。

### 2.3 lockfile 指纹：增量重译的最佳先例

- **Lingo.dev（原 Replexica）**：仓库根放 `i18n.lock`，`lingo run` 对比源内容与 lockfile 指纹，只翻译新增/变更内容。二手来源称按源字符串做 SHA-256 指纹，官方文档未证实具体算法（按未验证处理）。支持 Markdown/MDX/YAML/JSON 等源格式，无 Docusaurus 专属集成指南（[lingodotdev/lingo.dev](https://github.com/lingodotdev/lingo.dev)、[CLI 文档](https://lingo.dev/en/cli)）。
- **Azure co-op-translator**：宣称用「source hashes + translation metadata」做确定性新鲜度检查、跳过未变更文件，具体算法/存储位置未披露；会把内链/图片路径改写到自己的 `translations/<lang>/` 结构（与 Docusaurus `i18n/` 布局不兼容）（[Azure/co-op-translator](https://github.com/Azure/co-op-translator)）。

两者均验证了同一形态：**源内容指纹 + 旁路元数据文件 + 「仅重译脏文件」**，这正是 frontmatter-hash 思路的等价物，只是元数据存 lockfile 而非译文头部。

### 2.4 Crowdin 托管方案 vs 自建脚本

Docusaurus 官方文档双路线并列（见 1.5），社区实况（[facebook/docusaurus#4052](https://github.com/facebook/docusaurus/issues/4052) 等）补充的 Crowdin 代价：

- **MDX parser 版本漂移是头号坑**：Crowdin 升级内部 MDX parser 会改变分段方式，原本受保护的链接开始被机翻；缓解是在配置里钉住 parser 版本（如 `mdx_v2_4`），要升级须换名重传源目录。
- **源编辑涟漪**：源文件哪怕改个错字，对应译文段即变「未翻译」，复核前站点静默回退源语言；需配 `skip_untranslated_files` + 译文进 git 缓解。
- **同步方向纪律**：社区推荐严格单向（Git→Crowdin 手动、Crowdin→Git 自动），绝不在 git 里手改译文，双侧并发编辑会静默丢翻译——与官方「VCS 集成不推荐」的警告一致（见 1.5）。
- 替代品：GitLocalize（无 Docusaurus 实战记录，低置信）；Weblate 原生支持 Markdown 但要求自己是唯一真相源，git 侧手改译文无法可靠回收（[Weblate 文档](https://docs.weblate.org/en/latest/formats/markdown.html)）——对「偶尔手修 AI 译文」的工作流是硬伤。

**单人维护结论**：Crowdin 类平台的核心价值是多译者协作界面，对「中文单源 + AI 自译 + 无人工校对」的场景，parser 钉版、同步纪律、未译段复核都是净增负担——托管平台是搬运工作量而非消除。自建脚本（AI 翻译 + 指纹追踪）与该工作流形态匹配得多。

---

## 3. AI 批量翻译 Markdown 的流水线注意事项

### 3.1 保护非散文内容：占位符替换优于提示词叮嘱

- **占位符替换**是主流可靠手法：[rockbenben/md-translator](https://github.com/rockbenben/md-translator)（OSS，明确面向 Docusaurus i18n 目录）把代码围栏、行内代码、LaTeX、链接 URL、图片路径、HTML/JSX 标签先抽出替换为唯一 token（如 `<<<MULTILINE_CODE_x>>>`）再送 LLM，译后原样还原——LLM 根本看不到代码与 URL。支持整目录批量，但无滞后/增量重译追踪。
- **仅靠提示词约束不可靠**：有 Docusaurus 站长用 GPT-4-turbo + 提示词整文件翻译，仍遇到 JSX 组件参数被译坏（[sqybi.com 实践记录](https://sqybi.com/en-US/blog/adding-i18n-for-a-docusaurus-site/)）。大文件另需分块或流式避免超时。

### 3.2 Docusaurus/MDX 特有破坏模式

- **Admonition 指令**（`:::note` 等）对 LLM 是普通文本，可能被改写而静默破坏渲染；AI 译文场景社区建议改用 JSX 形式 `<Admonition type="note">`，或将 `:::` 行纳入占位符保护。
- **标题锚点断裂**：Docusaurus 从标题文本自动生成 heading ID，翻译标题即改变锚点，静默打断所有入站锚链接。文档化解法：批量翻译前给所有标题加显式 ID（`### 标题 {#stable-id}`），并将其作为预翻译 lint 门槛（[facebook/docusaurus#3322](https://github.com/facebook/docusaurus/issues/3322)）。
- **frontmatter 按 key 分治**：`title`/`description`/`sidebar_label` 应译，`slug`/`id` 路由关键必须保留原值。未发现有工具把按 key 策略产品化——实践者用提示词或 key 白名单 ad hoc 解决（真实缺口，自建脚本需自行实现 key 白名单）。
- **机翻引擎默认不懂 Markdown**：DeepL 类引擎会翻译 slug 与内链路径、破坏 i18n 路由，须显式排除（Crowdin 社区经验，同样适用于裸调 LLM）。

### 3.3 译后验证

- 术语一致性：md-translator 以自定义 system prompt + 上下文段落锁术语与风格，是机制最具体的可引用先例。
- **译后构建验证未见先例**：调研未发现社区把「译文过一遍 MDX 编译 / `docusaurus build` 再发布」确立为模式。若 ArcReel 把「双 locale build 通过」作为译文合入门槛，属于新颖但廉价的设计选择（MDX 编译失败即 build 失败，能兜住大部分语法破坏），而非借鉴先例。

其他工具（低置信 / 文档不足）：3ru/gpt-translate（GitHub Action，comment 触发，保护与滞后机制未文档化）、moonrailgun/docusaurus-i18n（OpenAI CLI，内部未文档化）、dicodocus.com（宣称有 missing translations analyzer，仅营销文案）。Lingo.dev 的具体 hash 算法、co-op-translator 的新鲜度实现均未能从可达页面证实——设计若需精确机制，须先读对应仓库源码。

---

## 4. 对 ArcReel 工作流的启示

针对「中文唯一源、英文 AI 自译无校对、单人维护、单版本」：

1. **fallback 让英文站可以增量上线**：中文源进 `docs/`，英文译文进 `i18n/en/docusaurus-plugin-content-docs/current/`；缺译页面自动渲染中文原文而非 404（1.2，源码级确认），因此翻译流水线可以逐文件推进、失败可跳过，不阻塞发布。
2. **滞后追踪必须自建，且有成熟形态可抄**：框架零支持（2.1）；「源内容指纹 + 元数据 + 仅重译脏文件」经 Lingo.dev / co-op-translator 验证（2.3）。指纹存 lockfile（单文件、不污染译文、git diff 集中）或译文 frontmatter（就地自描述）皆可，社区先例偏向 lockfile；应对源**内容** hash 而非 mtime/commit 时间戳（Kubernetes 的教训，2.2）。
3. **不建议引入 Crowdin 类平台**（2.4）：其价值在多译者协作，本场景纯负担。
4. **翻译脚本的四道防线**：(a) 占位符保护代码围栏/行内代码/URL/JSX/`:::` 指令；(b) frontmatter key 白名单（译 `title`/`description`/`sidebar_label`，保 `id`/`slug`）；(c) 翻译前全站标题加显式锚点 ID 并 lint 强制；(d) 合入前跑双 locale `docusaurus build` 作为语法兜底（3.3，属自创门槛）。
5. **译文文件纪律**：相对路径、文件名与源严格一致；删中文源时同步删英文译文（孤儿译文静默无效，1.2）；共享图片一律 `/img/` 绝对路径（1.3）。
