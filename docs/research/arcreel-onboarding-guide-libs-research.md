# 前端引导库与纯演示模式实现路径调研

> 对应 issue: [ArcReel/ArcReel#1283](https://github.com/ArcReel/ArcReel/issues/1283)（隶属地图 [#1282](https://github.com/ArcReel/ArcReel/issues/1282)）
> 调研时间：2026-07-24
> 性质：纯事实调研，不做形态决策。形态拍板见 [#1284](https://github.com/ArcReel/ArcReel/issues/1284)

## 研究问题

纯演示（零副作用，即不产生任何真实后端写入）约束下，前端引导有哪些可行技术路径？

1. 主流引导库现状：driver.js、react-joyride、shepherd.js、onborda——维护活跃度、React 19 兼容性、bundle 体积、i18n 支持、a11y 表现
2. 零副作用演示的常见实现模式：mock 数据驱动的 tour、只读示例项目、分步图文/动画演示面板，各自的业界实践与代表产品
3. 各路径与 ArcReel 技术栈（React 19 + Tailwind 4 + wouter + zustand）的适配注意点

## 结论摘要

| 库 | Stars | 最近发布 | React 19 | Bundle (min/gzip) | 依赖 | i18n | a11y（一手文档确认项） |
|---|---|---|---|---|---|---|---|
| [driver.js](https://github.com/nilbuild/driver.js) | 26,427 | 1.8.0 / 2026-07-17 | 天然兼容（无 React 绑定，零 peerDependencies） | 24.9KB / 7.3KB | 0 | 按钮文案可自定义字符串（`nextBtnText`/`prevBtnText`/`doneBtnText`） | `allowKeyboardControl` 配置项存在；ARIA/focus-trap 未在已查阅文档页中找到明确说明 |
| [react-joyride](https://github.com/gilbarbara/react-joyride) | 7,819 | 3.2.0 / 2026-07-09 | 官方支持，peerDeps `"16.8 - 19"` | 75.3KB / 25.6KB | 10 个 bundled 依赖 | `locale` prop（文档提及，未取到完整字段类型） | 官方主页明确声明内置 focus trap、键盘导航、ARIA 属性 |
| [shepherd.js](https://github.com/shipshapecode/shepherd) | 13,766 | 15.2.2 / 2026-03-11 | 核心库天然兼容（无 React 绑定）；官方 React 封装 [react-shepherd](https://www.npmjs.com/package/react-shepherd) 7.0.4（2026-03-11）peerDeps `^18.0.0 \|\| ^19.0.0` | 核心 40.6KB / 14.1KB；react-shepherd 41.3KB / 14.4KB | 核心 2 个依赖（`@floating-ui/dom`、`deepmerge-ts`） | 按钮/取消图标 `label`/`text` 可传函数返回字符串，官方文档标注"useful with i18n solutions" | 官方文档确认方向键导航、Esc 退出（均默认开启，可关闭）、按钮 `aria-label` 可配置；focus trap 细节未在已查阅的官方页面原文中直接确认 |
| [onborda](https://github.com/uixmat/onborda) | 1,394 | 1.2.5 / 2024-12-22（约 19 个月未更新） | 声明支持 `react: ">=18"`（未含 19 的显式上限测试证据） | 7.0KB / 2.3KB（不含 peer 依赖体积） | 需 `next: ">=13"` `framer-motion: ">=11"` `@radix-ui/react-portal` | README 未提及 | README 未提及 |

**与 ArcReel 技术栈的适配结论（事实层面，非选型建议）**：

- **onborda 因硬依赖 Next.js（`next/navigation` 用于步骤级路由跳转）而与 wouter 架构结构性不兼容**，需要移除/替换其路由耦合代码才能使用，不是开箱可用的选项。
- driver.js、react-joyride、shepherd.js 三者均不假设特定路由方案，按 DOM 选择器/ref 定位目标元素，与 wouter 无耦合。
- 三者均无官方 zustand 绑定，当前步骤/运行状态需要由集成方自行接入 zustand store 或组件 state；react-joyride 的受控模式（`stepIndex` + 回调）与外部状态机耦合方式最直接。
- driver.js、shepherd.js 各自附带独立 CSS（需要引入 `driver.css` / shepherd 自带样式），如需与 Tailwind 4 的设计 token 统一视觉，需要覆盖/重写其默认样式；react-joyride 走 `styles`/inline props 全 JS 驱动，无需引入额外 CSS 文件。
- 三者的按钮文案均以字符串或返回字符串的函数形式暴露，可直接接入项目现有 i18next 字典（`frontend/src/i18n/{zh,en,vi}/`），库本身不内置多语言词表。

## 一、主流引导库现状详情

### 1. driver.js

- 仓库：GitHub 上通过 `kamranahmedse/driver.js` 访问会 301 跳转至 **`nilbuild/driver.js`**（HTTP 响应头 `location: https://github.com/nilbuild/driver.js` 实测确认，2026-07-24），仓库归属已变更，需留意此事实但未进一步核实变更原因；活跃度数据取自跳转后的当前仓库。
- Stars 26,427，open issues 21，最近 push 2026-07-18（[GitHub API](https://api.github.com/repos/kamranahmedse/driver.js)）。
- 发布节奏（[npm registry](https://registry.npmjs.org/driver.js)）：1.8.0（2026-07-17）、1.7.0（2026-07-13）、1.6.0（2026-06-25）、1.5.0（2026-06-23）、1.4.0（2025-11-18）——近两个月密集发布，此前有约 7 个月空窗期。
- `peerDependencies` 为空（[npm registry](https://registry.npmjs.org/driver.js/latest)），零依赖 vanilla JS 库，不含任何 React 绑定，因此对 React 19 天然无兼容性问题（不存在需要适配的 React API 面）。
- Bundle 体积（[Bundlephobia API](https://bundlephobia.com/api/size?package=driver.js@1.8.0)）：min 24,940 B / gzip 7,336 B，`dependencyCount: 0`。
- i18n：[官方配置文档](https://driverjs.com/docs/configuration) 确认 `nextBtnText` / `prevBtnText` / `doneBtnText` 等按钮文案可自定义字符串，方式是开发者自行传入已翻译文案（无内置词表）。
- a11y：官方文档确认 `allowKeyboardControl?: boolean`（默认 `true`，控制是否允许键盘导航）与 `allowClose?: boolean`（背景点击关闭）；在已查阅的 Installation/Configuration 两个页面中**未找到** ARIA 属性或 focus-trap 的明确说明，未查阅完整 API Reference 页面，此项标注为未能核实，而非确认不支持。

### 2. react-joyride

- 仓库：`gilbarbara/react-joyride`，stars 7,819，open issues 仅 2（[GitHub API](https://api.github.com/repos/gilbarbara/react-joyride)），issue 数量少且长期维持低位，是四者中信噪比最高的。
- 最近三个发布（[GitHub Releases API](https://api.github.com/repos/gilbarbara/react-joyride/releases)）：3.2.0（2026-07-09）、3.1.0（2026-04-29）、3.0.2（2026-04-01），迭代频率稳定。
- **React 19 官方支持**：当前 `main` 分支及已发布的 3.2.0 版本 `package.json`（[raw.githubusercontent.com 直取](https://raw.githubusercontent.com/gilbarbara/react-joyride/main/package.json) 与 [npm registry 按版本号取值](https://registry.npmjs.org/react-joyride/3.2.0) 两个信源交叉确认一致）声明：
  ```json
  "peerDependencies": { "react": "16.8 - 19", "react-dom": "16.8 - 19" }
  ```
  历史上曾有多个社区 issue/PR 围绕 React 19 兼容展开（如 [#1122 "Incompatible with React v19"](https://github.com/gilbarbara/react-joyride/issues/1122) 已 closed as completed、[#1130](https://github.com/gilbarbara/react-joyride/issues/1130)、[#1178](https://github.com/gilbarbara/react-joyride/pull/1178) 等 PR），当前已在 3.x 系列中收敛为官方声明支持。
- Bundle 体积（[Bundlephobia API](https://bundlephobia.com/api/size?package=react-joyride@3.2.0)）：min 75,288 B / gzip 25,596 B，`dependencyCount: 10`（含 `@floedge-ui` 系列、`@gilbarbara/hooks` 等），是四者中体积最大的。
- a11y：[官方主页](https://react-joyride.com/) 明确写明 "Built-in focus trap, keyboard navigation, and ARIA attributes for screen readers."，是四者中唯一在首页直接给出 a11y 承诺原文的库。
- i18n：官方文档导航中列有 `locale` 相关 Props 页面，用于自定义 back/close/last/next/skip 等按钮文案；本次未取到该页面完整字段类型定义原文，标注为部分核实。

### 3. shepherd.js

- 仓库：`shipshapecode/shepherd`（[GitHub API](https://api.github.com/repos/shipshapecode/shepherd)），stars 13,766，open issues 52（四者中最多），最近 push 2026-07-21（近期仍有开发活动）。
- 发布节奏（[npm registry](https://registry.npmjs.org/shepherd.js)）：核心包最新版 15.2.2 发布于 2026-03-11，此前 15.2.1（02-23）、15.0.0（02-08）、14.5.1（2025-07-23）——releases 频率低于 driver.js/react-joyride，但仓库层面（含未发版的 monorepo 改动）仍保持近期活跃。
- 核心包 `peerDependencies` 为空（[npm registry](https://registry.npmjs.org/shepherd.js/latest)），`dependencies` 为 `@floating-ui/dom ^1.7.5`、`deepmerge-ts ^7.1.5`，本体不含 React 绑定，框架无关。
- **React 集成需经官方封装包 `react-shepherd`**：[npm registry](https://registry.npmjs.org/react-shepherd/latest) 显示最新版 7.0.4（发布于 2026-03-11，与核心包 15.2.2 同日），`peerDependencies` 为 `react: "^18.0.0 || ^19.0.0"`、`react-dom` 同范围——**React 19 官方支持**。该包 `repository` 字段指向 `shipshapecode/shepherd.git`，经核实 monorepo 下确实存在 `packages/react` 目录（[GitHub Contents API](https://api.github.com/repos/shipshapecode/shepherd/contents/packages)），即 react-shepherd 现已并入 shepherd.js 主仓库统一维护发版；独立的旧仓库 `shipshapecode/react-shepherd` 已被归档（`archived: true`，最近 push 2024-05-17，[GitHub API](https://api.github.com/repos/shipshapecode/react-shepherd)），是并入 monorepo 后的正常收编动作，不代表该封装停止维护。
- Bundle 体积：核心 shepherd.js min 40,647 B / gzip 14,077 B（[Bundlephobia](https://bundlephobia.com/api/size?package=shepherd.js)）；react-shepherd 封装层 min 41,324 B / gzip 14,429 B（[Bundlephobia](https://bundlephobia.com/api/size?package=react-shepherd@7.0.4)，`dependencyCount: 1` 即依赖核心包本身）。
- a11y：[官方 Usage 文档](https://docs.shepherdjs.dev/guides/usage/) 原文确认："Navigating the tour via left and right arrow keys will be enabled unless this is explicitly set to false." 以及 "Exiting the tour with the escape key will be enabled unless this is explicitly set to false."；按钮/取消图标的 `label` 字段用于 `aria-label`。搜索引擎摘要另外提到内置 focus trap 与 `aria-describedby`/`aria-labelledby`，但该表述来自搜索结果聚合而非本次直接抓取到的官方页面原文，标注为未完全核实，需要专门查证该细节页面或直接测试确认。
- i18n：官方文档原文确认按钮 `text` 字段与取消图标 `label` 字段均"can also be a function that returns a string (useful with i18n solutions)"。

### 4. onborda

- 仓库：`uixmat/onborda`，stars 1,394，open issues 15（[GitHub API](https://api.github.com/repos/uixmat/onborda)），四者中体量最小。
- 最新发布 1.2.5，发布于 **2024-12-22**（[npm registry](https://registry.npmjs.org/onborda)），截至本次调研（2026-07-24）已近 19 个月未发布新版本，是四者中维护活跃度明显最低的一个；仓库最近 push 为 2026-06-08，说明有主分支改动但未推进发版。
- `peerDependencies`（[npm registry](https://registry.npmjs.org/onborda/latest)）：
  ```json
  { "framer-motion": ">=11", "next": ">=13", "react": ">=18", "react-dom": ">=18", "@radix-ui/react-portal": ">=1.1.1" }
  ```
  声明 `react: ">=18"` 未设上限，但缺乏该库针对 React 19 的显式测试证据（无 CHANGELOG/README 提及）；且 **`next: ">=13"` 为硬性 peerDependency**。README（[raw.githubusercontent.com](https://raw.githubusercontent.com/uixmat/onborda/main/README.md)）原文确认步骤配置中存在 `"Optional. The route to navigate to using `next/navigation` when moving to the next step."`，即该库的多步骤跳转能力直接依赖 Next.js App Router 的 `next/navigation` API，示例代码也使用 `layout.tsx`/`page.tsx` 等 App Router 约定文件名。
- Bundle 体积（[Bundlephobia](https://bundlephobia.com/api/size?package=onborda@1.2.5)）：本体 min 6,989 B / gzip 2,324 B，体积虽小但不含 `next`/`framer-motion`/`@radix-ui/react-portal` 三个硬 peer 依赖的体积，且脱离这三者无法运行。
- a11y / i18n：README 全文中未发现任何 accessibility 或 i18n/多语言相关表述。
- **与 ArcReel 技术栈的关系是结构性不兼容**：仓库定位即为 "The ultimate product tour library for **Next.js**"（[Bundlephobia 抓取的 package description](https://bundlephobia.com/api/size?package=onborda@1.2.5)），核心跳转机制绑定 `next/navigation`，在 wouter 路由的 React SPA 中若要使用，需要先剥离/重写其路由耦合部分，不属于开箱可用选项。

## 二、零副作用演示的常见实现模式

以下三种模式均可独立于第三方引导库选型使用，"零副作用"约束主要通过应用层的数据/写入路径设计满足，而非引导库自身的能力。

### 模式一：mock 数据驱动的 tour

引导覆盖层叠加在渲染真实组件树之上，但组件树所消费的数据来自本地/内存态的演示数据集，而非真实后端读写路径；引导过程中触发的操作按钮被拦截为空操作或仅更新本地演示态。

- 代表实践：[VS Code 官方 Walkthrough 贡献点规范](https://code.visualstudio.com/api/ux-guidelines/walkthroughs)描述的模式为"a multi-step checklist featuring rich content"，强调引导内容以图像/富媒体承载、步骤聚焦用户教育而非状态变更，是"内容与真实数据解耦"的一手范例（虽然 VS Code Walkthrough 本身更接近模式三，但其设计原则——引导不应触发真实副作用——同样适用于 mock 数据驱动 tour 的设计目标）。
- 该模式对 ArcReel 而言的实现要点：CLAUDE.md 已明确"入队走动作层"约束（生成类操作统一经 `frontend/src/actions/`），意味着演示态下拦截真实入队调用有既有的单一收口点，理论上可以在该层做演示模式短路，而不需要在各组件里分散拦截。

### 模式二：只读示例项目（demo project）

预置一个内容完整的示例项目/工作区，用户可以正常浏览、点击，但写操作在该项目上被结构性禁止或被重定向为"复制后可编辑"。

- 代表实践：[Figma 官方帮助中心 "Duplicate Community files"](https://help.figma.com/hc/en-us/articles/360038510873-Duplicate-Community-files) 与 ["Duplicate or copy files"](https://help.figma.com/hc/en-us/articles/360038511533-Duplicate-or-copy-files)：Community 文件默认以 view-only 权限打开，用户只能看到"Duplicate to drafts"而非"Edit"，需要显式复制一份到自己的 drafts 后才能编辑，原文件本身在浏览期间不产生任何写入，且复制后的副本与原文件完全脱钩（不共享版本历史/评论）。这是"只读示例 + 显式脱钩复制"模式的一手代表案例。
- 该模式的关键约束是需要在数据层区分"只读示例资源"与"用户可写资源"，并保证所有写入路径（含级联副作用，如 ArcReel 的生成任务队列）在只读态下被拒绝而非静默失败。

### 模式三：分步图文/动画演示面板

引导内容完全由静态图文、GIF、Lottie 动画或短视频构成，不依赖也不读取应用的真实运行时状态，天然与"零副作用"约束解耦。

- 代表实践一：[VS Code 官方 Walkthrough UX 规范](https://code.visualstudio.com/api/ux-guidelines/walkthroughs) ——通过扩展贡献点声明的多步骤清单，每步搭配图片/媒体与"下一步"式操作按钮，官方设计建议中明确要点是"避免单个流程步骤过多""优先使用可跟随主题变化的 SVG"，内容与扩展的真实数据状态无耦合。
- 代表实践二：[Intercom 官方帮助中心 "Product Tours explained"](https://www.intercom.com/help/en/articles/2900885-product-tours-explained) 描述其 Product Tours 功能提供三种消息呈现形式——post（页面居中展示，不指向具体元素）、pointer、video pointer，其中 post 形式本质即是与目标元素解耦的纯内容展示，可视为"分步图文面板"在商业化引导工具中的对应实现（Intercom 作为该功能的一手厂商文档，用于佐证该呈现形式在业界的既有分类，而非作为 ArcReel 可直接采用的库）。

## 三、与 ArcReel 技术栈（React 19 + Tailwind 4 + wouter + zustand）的适配注意点（事实汇总）

- **React 19**：driver.js（零 React 绑定）、react-joyride（peerDeps 显式含 19）、shepherd.js 经 react-shepherd 封装（peerDeps 显式含 19）三者均有一手证据支持 React 19；onborda 未见针对 19 的显式验证证据。
- **wouter**：driver.js/react-joyride/shepherd.js 均不假设特定路由库，按 DOM 元素定位，无需适配 wouter；onborda 的步骤跳转硬编码依赖 `next/navigation`，与 wouter 结构性冲突。
- **zustand**：四者均无官方 zustand 绑定，"当前步骤/运行状态"这一状态切片需要集成方自行建模；react-joyride 提供受控模式（`stepIndex` prop + `callback`），与外部状态机（如 zustand store）对接路径最直接，shepherd.js/driver.js 则是通过其自身实例 API（`next()`/`show()` 等命令式调用）驱动，需要额外一层适配代码把状态同步回 zustand。
- **Tailwind 4**：driver.js、shepherd.js 各自附带独立 CSS 文件（`driver.css` 等），需要引入后覆盖默认样式以对齐设计系统；react-joyride 通过 `styles` prop 以内联样式对象驱动，不强制引入额外 CSS 文件，与 Tailwind 的 utility-first 风格摩擦更小，但也意味着暗色模式等需要的样式切换需要由集成方通过 `styles` prop 自行传入对应 token，而非依赖 CSS 变量覆盖。
- **i18n（zh/en/vi）**：四者均不内置多语言词典，按钮文案统一是"开发者传入字符串或返回字符串的函数"，可以直接接入项目现有 i18next 字典体系，无额外三语同步机制需要评估。

## 数据来源与核实局限性说明

- 所有版本号、发布时间、`peerDependencies`、依赖列表均直接取自 npm registry JSON（`registry.npmjs.org/<pkg>` 及按版本号/`@latest` 精确查询）或对应 GitHub 仓库的 `package.json`/Releases API，未依赖第三方转述。
- Stars、open issues、最近 push 时间取自 GitHub REST API 实时查询（2026-07-24）。
- Bundle 体积取自 Bundlephobia 官方 API（按精确版本号查询，避免"latest"随时间漂移）。
- 以下几点因信息来源限制标注为**未能完全核实**，供后续如需精确判断时针对性补查：
  - driver.js 是否有官方文档明确的 ARIA / focus-trap 支持（本次仅查阅 Installation 与 Configuration 两页，未查阅完整 API Reference）。
  - shepherd.js 的 focus trap 细节（官方 Usage 页原文仅确认了方向键导航与 Esc 退出，focus trap 与 `aria-describedby`/`aria-labelledby` 的表述来自搜索引擎摘要聚合，未直接定位到官方原文段落）。
  - react-joyride `locale` prop 的完整字段类型定义（仅确认该 Props 页面存在，未取到完整原文）。
  - driver.js 仓库从 `kamranahmedse/driver.js` 变更为 `nilbuild/driver.js` 的具体背景（仅通过 HTTP 301 跳转确认归属已变更，未查证变更原因、是否为原作者主导的组织迁移）。
