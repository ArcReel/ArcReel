# 前端重构的验收与回归手段

> 日期：2026-10-02。对应 [#2966](https://github.com/ArcReel/ArcReel/issues/2966)（地图 [#2959](https://github.com/ArcReel/ArcReel/issues/2959)）。
> 问题：基于 shadcn/ui 的前端重构用什么手段做视觉与交互验收，包括 Playwright 截图基线、组件预览页或 Storybook 的取舍、「内容溢出但不可达」探针能否做成自动守卫，以及新手段与现有 Vitest + Testing Library 测试的分工。
> 来源：工具事实来自 Playwright、Vitest、Storybook、Ladle 官方文档（Playwright、Vitest、Storybook 经 Context7 检索后回到官方页面核对）、Playwright 源码、jsdom README、MDN、axe-core 规则表与 GitHub 官方文档。版本号来自 npm registry（2026-10-02 `npm view`）：`@playwright/test` 1.63.0、`vitest` 5.0.3、`storybook` 10.6.1、`@ladle/react` 5.1.1。仓库事实以 main `b610d9406` 为准。
> 实测范围：溢出探针在本机 headless Chromium（Playwright 1.63.0 库）上对一个合成页面跑过（见 §5.3），没有在 ArcReel 真实页面上运行。文中标「估算」「待实测」的数字是推断。

## 结论速览

| 问题 | 结论 |
|---|---|
| 1 Playwright 截图基线 | 可行，但只在固定渲染环境里稳定：基线与 CI 都在锁版本的 Playwright Docker 镜像里生成，字体自托管，时间、网络数据和动效全部固定。重构期间视觉本来就要变，截图差异先作为评审材料，某个区域重做完成后再把它的截图升格为回归闸门。 |
| 2 组件预览 | 不引入 Storybook 或 Ladle。采用 Playwright 1.62 起的组件测试模型：在本仓 Vite 应用里放一个「story gallery」页面，`*.story.tsx` 描述原语的各个状态，`mount()` fixture 挂载后截图或交互。它就是「应用内组件预览路由」的官方形态，复用本仓 Vite 配置、Tailwind 与别名。优先级低于页面级手段。 |
| 3 溢出探针 | 能做成自动守卫，而且不需要基线，重构第一天就能当闸门用。合成页面实测：漏设 `min-h-0` 的 flex 链、`overflow-x: hidden` 吞掉的宽内容、`overflow: clip`、固定高度裁切都被检出；单行省略、`line-clamp`、`sr-only`、折叠态、`object-fit: cover` 图片都被正确放过。唯一误报是装饰性绝对定位层，用 `data-overflow-ok="原因"` 显式豁免。必须在真实浏览器里跑，jsdom 不做布局。 |
| 4 分工 | jsdom 里的 Vitest + Testing Library 继续守行为契约（角色、状态、API 调用），不承担布局与视觉。新增一个 Playwright 前端 job，承担页面级的溢出探针、axe 无障碍扫描和截图对比。Vitest browser mode 只作为可选项，用来替换目前靠打桩布局 API 的 9 个测试文件。 |

推荐优先级与成本见 §7。

## 1. 本仓现状

### 1.1 前端检查与测试

- `frontend/package.json` 的 `check` 脚本是 `pnpm typecheck && pnpm lint && pnpm knip && vitest run`；`vitest`、`@vitest/coverage-v8` 版本为 `^4.1.10`（npm 上最新为 5.0.3）。
- `frontend/vitest.config.ts`：`environment: "jsdom"`，只收 `src/**/*.test.{ts,tsx}`；`src/test/setup.ts` 用空类 stub 掉 `ResizeObserver`，注释写明「测试只断言可见性、交互与结构，不验位置像素」。
- 规模：247 个测试文件，共 61,725 行。全部 `.test.tsx` 中 `ByRole` 查询出现 2,629 次，`querySelector` 与 `toHaveClass` 合计 131 次。
- 票面提到的 `src/components/ui/ProviderModelSelect.test.tsx` 共 457 行：35 处 `ByRole`（`combobox` 23、`option` 11、`status` 1），18 处 `userEvent`，没有 `querySelector`、`className` 或 `toHaveClass` 断言。被测组件是手写的组合框（`role="combobox"` / `listbox` / `option`）。这类测试断言的是 ARIA 语义，换底座时只要新原语保持相同角色，用例本身不必重写。
- 9 个测试文件打桩了布局相关 API（`getBoundingClientRect`、`scrollHeight`、`scrollIntoView`、`ResizeObserver` 等）：`ui/Popover`、`canvas/StudioCanvasRouter.edit-render`、`canvas/lorebook/CharacterCard`、`canvas/timeline/ResponsiveDetailGrid`、`canvas/EpisodeSourceReview`、`canvas/episodes/EpisodesView`、`canvas/edit/EditTimelineView`、`canvas/reference/ReferenceVideoCanvas`、`hooks/useScrollTarget`。
- 仓库里没有 Playwright、Storybook 或任何截图测试。

### 1.2 前端 CI

- `.github/workflows/test.yml` 有两个前端 job，都由 `.github/actions/domain-filter` 的 `frontend` 域（`frontend/**`）触发，`runs-on: ubuntu-latest`，`timeout-minutes: 15`：
  - `frontend-static`：`pnpm install --frozen-lockfile` → `pnpm lint` → `pnpm knip` → `pnpm build`（含 typecheck）。
  - `frontend-tests`：`pnpm test:coverage`，上传 Codecov。
- `.github/workflows/nightly-full.yml` 每天以 `full: true` 调用 `test.yml`，失败时开或更新带 `ci` 标签的追踪 issue。
- `ci-required` 汇总全部 job，任一 `failure` / `cancelled` 即失败。

### 1.3 与验收相关的约束

- `docs/standards/testing.md`：前端 API 打桩边界是 `vi.spyOn(API, method)`，**不引入 msw**；**不引入自动重试**（pytest-rerunfailures、CI job 级 retry），偶发失败视同缺陷。Playwright 套件因此应保持 `retries: 0`，网络替身用 Playwright 自带的 `page.route()`。
- `frontend/index.html` 通过 `fonts.googleapis.com` 加载 Inter、JetBrains Mono、Noto Sans SC、Instrument Serif。网络字体会让截图随网络状况变化；地图 Notes 已定「字体自托管」，截图基线应在自托管落地后再生成。
- `frontend/src/onboarding/demo-project.ts` 有一份纯前端的演示项目数据（`onboarding_demo`），用真实的 `ProjectData` 等类型标注。它可以作为截图与探针的 fixture 起点，其余接口仍需用 `page.route()` 补齐。
- 非测试 `.tsx` 中 `overflow-hidden` 出现在 139 行，`truncate` / `line-clamp` / `text-ellipsis` 出现在 130 行。这是溢出探针需要甄别的规模参考。

## 2. jsdom 能验证什么

jsdom README 在「Unimplemented parts of the web platform」中写明：jsdom 不实现 Layout，即「根据 CSS 计算元素布局位置」，这会影响 `getBoundingClientRects()`、`offsetTop` 等；对许多布局属性返回 0，并建议用 `Object.defineProperty()` 改写这些 getter 作为变通。——[jsdom README](https://github.com/jsdom/jsdom#unimplemented-parts-of-the-web-platform)

本仓 `CharacterCard.test.tsx` 就是用 `Object.defineProperty(textarea, "scrollHeight", …)` 走这条变通。结论：滚动、溢出、栏宽、宽窗口布局、视觉回归都不在现有测试的能力范围内，必须引入真实浏览器。

## 3. Playwright 截图对比

### 3.1 断言机制

- `toHaveScreenshot()` 等到连续两次截图结果相同，再拿最后一次与基线比较。——[PageAssertions](https://playwright.dev/docs/api/class-pageassertions)
- `animations` 默认 `"disabled"`：停止 CSS 动画、CSS 过渡和 Web Animations。有限时长的动画快进到结束（会触发 `transitionend`），无限动画取消回初始状态，截图后再恢复播放。——[PageAssertions](https://playwright.dev/docs/api/class-pageassertions)；实现见 [`screenshotter.ts`](https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/server/screenshotter.ts) 的 `inPagePrepareForScreenshots`，它同时监听 `transitionrun` / `animationstart`，截图期间新起的动画也会被处理。
- 截图前等待 `document.fonts.ready`，可用环境变量 `PW_TEST_SCREENSHOT_NO_FONTS_READY` 关闭。——[`screenshotter.ts`](https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/server/screenshotter.ts)
- `caret` 默认 `"hide"`，隐藏文本光标。`mask` 用 `#FF00FF` 色块（`maskColor` 可改）盖住指定 locator 的包围盒。`stylePath` 指定截图时注入的样式表，用于隐藏动态元素。`scale` 默认 `"css"`，每个 CSS 像素对应一个图像像素。——[PageAssertions](https://playwright.dev/docs/api/class-pageassertions)
- 阈值：`threshold` 是 YIQ 色彩空间中同一像素的可接受感知色差，0（严格）到 1（宽松），默认 0.2；`maxDiffPixels` 是允许不同的像素数，`maxDiffPixelRatio` 是允许不同的像素比例（0–1）。可在 `playwright.config.ts` 的 `expect.toHaveScreenshot` 里全局设置。——[PageAssertions](https://playwright.dev/docs/api/class-pageassertions)、[TestProject](https://playwright.dev/docs/api/class-testproject)
- 截取可滚动容器时，只有当前滚动位置可见的内容会进入截图。——[ElementHandle.screenshot](https://playwright.dev/docs/api/class-elementhandle)。ArcReel 是「外壳固定、区域内滚动」的布局，`fullPage: true` 拍不到滚动区里的长内容；长列表要么分别截取区域，要么交给溢出探针（§5）。

### 3.2 环境一致性

- 官方原文：浏览器渲染会因宿主操作系统、版本、设置、硬件、电源（电池或适配器）、headless 模式等因素而不同；要得到一致的截图，测试必须在生成基线的同一环境中运行。快照文件名包含浏览器名与平台（如 `chromium`、`darwin`、`linux`）。——[Visual comparisons](https://playwright.dev/docs/test-snapshots)
- Vitest 文档对同一问题的建议更直接：视觉回归在标准化、严格受控的环境中最可靠，因此「强烈推荐」Docker 容器、只在 CI 里做视觉测试的流程或云服务。——[Vitest Visual Regression Testing](https://vitest.dev/guide/browser/visual-regression-testing)
- 官方 Docker 镜像 `mcr.microsoft.com/playwright:v1.63.0-noble`（另有 `-jammy`、`-resolute`）包含浏览器及其系统依赖，不含 Playwright npm 包本身。官方建议始终把镜像钉到具体版本：镜像版本与项目里的 Playwright 版本不一致时，Playwright 找不到浏览器可执行文件。运行 Chromium 时建议加 `--ipc=host`，否则 Chromium 可能内存不足而崩溃；建议加 `--init` 避免 PID 1 进程导致的僵尸进程。——[Docker](https://playwright.dev/docs/docker)
- 远程模式：在容器里运行 `npx -y playwright@1.63.0 run-server --port 3000 --host 0.0.0.0`，宿主上用 `PW_TEST_CONNECT_WS_ENDPOINT=ws://127.0.0.1:3000/ npx playwright test` 连接。——[Docker](https://playwright.dev/docs/docker)。这让 macOS 上的开发者在本地也能用 Linux 容器渲染，生成与 CI 相同的基线。
- GitHub Actions 可直接在 job 上声明 `container: image: mcr.microsoft.com/playwright:v1.63.0-noble`，`options: --user 1001`。——[CI](https://playwright.dev/docs/ci)

### 3.3 ArcReel 需要额外固定的变量

| 变量 | 手段 | 来源 |
|---|---|---|
| 字体 | 自托管字体落地后再生成基线；截图本身会等 `document.fonts.ready` | §1.3、[`screenshotter.ts`](https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/server/screenshotter.ts) |
| 接口数据 | `page.route()` 拦截并 `route.fulfill({ json })`，fixture 复用演示项目数据；符合「不引入 msw」 | [Mock APIs](https://playwright.dev/docs/mock) |
| 相对时间（「3 分钟前」等） | `page.clock.setFixedTime()` 固定 `Date.now()` 与 `new Date()`，计时器照常运行 | [Clock](https://playwright.dev/docs/clock) |
| 动效 | 断言默认停掉动画；需要观察 reduced-motion 分支时用 `page.emulateMedia({ reducedMotion: "reduce" })` | [Page.emulateMedia](https://playwright.dev/docs/api/class-page) |
| 生成的图片与视频 | fixture 指向固定占位图；无法固定的区域用 `mask` | [PageAssertions](https://playwright.dev/docs/api/class-pageassertions) |
| 视口 | 每个视口一个 Playwright project；宽度取票面的 1280 / 1440 / 1920 / 2560，最终清单以 [#2964](https://github.com/ArcReel/ArcReel/issues/2964)（宽窗口原则与最低支持视口）结论为准 | — |

视口高度建议成对给出（如 1280×720、1440×900、1920×1080、2560×1440）：纵向裁切取决于高度，只变宽度测不出矮窗口下的问题。这是本文的建议，不是工具约束。

### 3.4 基线更新流程

- `--update-snapshots [mode]` 取值 `all`、`changed`、`missing`、`none`。不带该参数时默认 `missing`（只补缺失的基线）；带参数但不给值时默认 `changed`。——[CLI](https://playwright.dev/docs/test-cli)
- 基线路径可用 `snapshotPathTemplate` 或 `expect.toHaveScreenshot.pathTemplate` 定制，模板支持 `{projectName}`、`{testFilePath}`、`{arg}` 等占位。——[TestConfig](https://playwright.dev/docs/api/class-testconfig)、[TestProject](https://playwright.dev/docs/api/class-testproject)
- 更新基线必须在与 CI 相同的容器里执行（本地用 §3.2 的远程模式，或由 CI job 产出后提交）。

### 3.5 重构期间截图的定位

地图 Notes 定的是「在与过去一致和更简更优之间选更简更优」，重构会主动改变大多数页面的外观。重构进行中，截图对比不能当闸门，否则每个 PR 都在批量更新基线。建议分两段使用：

1. **重做中的区域**：截图作为评审材料。CI 照常截图并上传 HTML 报告（官方示例用 `actions/upload-artifact` 上传 `playwright-report/`，`retention-days: 30`，见 [CI](https://playwright.dev/docs/ci)），维护者在报告里看改前改后，不比对基线。
2. **重做完成的区域**：提交该区域的基线，截图对比转为回归闸门，防止后续区域的改动（共享原语、token、外壳）波及它。

「替换底座但不打算改外观」的 PR（例如只把 Toast 换成 Sonner）在第 2 段语境里正好能被截图对比兜住。

### 3.6 CI 成本

- 公开仓库使用标准 GitHub 托管 runner 免费；公开仓库的 `ubuntu-latest` 为 4 CPU、16 GB 内存、14 GB SSD。——[GitHub Actions 计费](https://docs.github.com/en/billing/concepts/product-billing/github-actions)、[GitHub 托管 runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。成本主要是墙钟时间和维护基线的人力，不是账单。
- Playwright 建议 CI 上 `workers: 1`，以稳定与可复现为先；不建议缓存浏览器，因为恢复缓存的耗时与下载二进制相当，系统依赖也无法缓存；可用 `npx playwright install --with-deps chromium` 只装 Chromium，或直接用官方镜像；用例多了再按矩阵分片并合并报告。——[CI](https://playwright.dev/docs/ci)
- 估算（待实测）：只用 Chromium，约 15 个关键页面 × 4 个视口 = 60 个用例；每个用例包含导航、截图稳定等待、探针与 axe 扫描，按 2–4 秒计约 2–4 分钟；加上拉取镜像、`pnpm install`、`vite build` 与启动 `vite preview`，单个 job 约 5–8 分钟，与现有 `frontend-tests` 并行，不拉长关键路径太多。
- 基线体积（待实测）：每张 PNG 的大小取决于视口与画面复杂度，具体数值待实测。60 张基线每次整体更新都会进入 git 历史，所以只为「重做完成的区域」提交基线，且优先截取区域而不是整页。

## 4. 组件预览：Storybook、Ladle 与应用内 gallery

### 4.1 Storybook 10.6.1

- `@storybook/react-vite@10.6.1` 的 peerDependencies 支持 `vite ^5 || ^6 || ^7 || ^8`、`react ^16.8–^19`，与本仓 Vite 8、React 19 兼容（npm registry）。
- Vitest 插件（`@storybook/addon-vitest`）用 portable stories 把每个 story 转成 Vitest 测试，并配置 Vitest 在 browser mode 下用 Playwright 的 Chromium 运行；每个 story 做一次渲染冒烟，定义了 `play` 函数的再执行交互断言；要求 Vite 系的 Storybook 框架。CI 上官方建议 `isolate: false` 降低资源占用，并用 `vitest run --shard=1/3` 分片。——[Vitest addon](https://storybook.js.org/docs/writing-tests/integrations/vitest-addon)
- 视觉测试：Storybook 原生支持的跨浏览器视觉测试走 Chromatic（Storybook 团队的云服务），需要登录 Chromatic 账号并关联项目，story 被发送到云端截图比对。——[Visual tests](https://storybook.js.org/docs/writing-tests/visual-testing)
- Storybook 10.6.0 移除了实验性的 Playwright CT 桥（`createPlaywrightTest`），说明 Playwright 已改为「在你自己的 dev server 上提供 story gallery」的模型；Storybook 作为这种 dev server 还没有文档化的集成。——[Storybook MIGRATION.md](https://github.com/storybookjs/storybook/blob/next/MIGRATION.md)

取舍：Storybook 带来独立的 `.storybook/` 配置、额外依赖与第二个 dev server；自带的视觉测试依赖外部云服务；离开 Chromatic 后仍要自己用 Playwright 截图。它的强项（文档站式组件目录、插件生态）不是本次重构的需求。

### 4.2 Ladle 5.1.1

- 最新版 5.1.1 发布于 2025-11-04，此后近 11 个月没有新版本；仓库最近一次 push 为 2026-06-28（GitHub API）。
- `@ladle/react` 把 `vite: ^6.0.5` 和 `@vitejs/plugin-react: ^4.3.4` 列为自身 dependencies（npm registry），会用自带的 Vite 6 运行；本仓是 Vite 8 与 `@vitejs/plugin-react` 6。它默认读取项目根的 `vite.config.*`（`viteConfig` 选项可改路径），插件跨大版本的兼容性需要逐个验证。——[Ladle Config](https://ladle.dev/docs/config)
- 视觉快照方案：读取 Ladle 产出的 `meta.json` 列出全部 story，逐个打开 `/?story=<storyKey>&mode=preview`，等待 `[data-storyloaded]` 后 `toHaveScreenshot()`。——[Ladle Visual Snapshots](https://ladle.dev/docs/visual-snapshots)

取舍：轻量，但构建链与本仓分叉，且发布节奏停滞。截图部分本来就是 Playwright 在做。

### 4.3 应用内 gallery：Playwright 1.62 组件测试模型

- Playwright 1.62 起，组件测试改为 stories 与 gallery 模型：一个组件测试就是普通的 Playwright 端到端测试，运行在由**你自己的 dev server** 提供的小型 story gallery 页面上。——[Release notes 1.62](https://playwright.dev/docs/release-notes)、[Components](https://playwright.dev/docs/test-components)
- 三个组成部分：`*.story.tsx` 中的具名导出（包装被测组件，写死 props 与 mock 数据）；gallery 页面（暴露 `window.mount(params)` / `window.unmount()`，把 story 渲染进 `#root`）；`mount(storyId, props?)` fixture（导航到 gallery，返回附带 `update(props)` / `unmount()` 的 Locator）。——[Components](https://playwright.dev/docs/test-components)、[Fixtures](https://playwright.dev/docs/api/class-fixtures)
- gallery 是应用代码，归项目所有，不由 Playwright 提供；官方建议用 `npx playwright init-skills` 让编码 agent 按项目的框架与打包器生成 gallery、Playwright project 和首个 story 与 spec。网络替身照常用 `page.route()`，须在 `mount()` 之前注册，因为挂载会触发导航；已有 MSW handler 的团队也可在 story 里启动 worker。——[Components](https://playwright.dev/docs/test-components)
- 旧的 `@playwright/experimental-ct-react` 等实验包已移除且不再发布。——[Components](https://playwright.dev/docs/test-components)
- 官方示例配置：`baseURL: 'http://localhost:5173/playwright/gallery/index.html'`，`webServer.command: 'npm run dev'`，即 gallery 直接由项目的 Vite dev server 提供。——[Components](https://playwright.dev/docs/test-components)

取舍：这正是票面「在应用内放一个组件预览路由」的官方化形态。gallery 走本仓 `vite.config.ts`、Tailwind v4、`@` 别名与 i18n，不需要第二套构建；截图、交互、溢出探针与 axe 都和页面级套件共用同一套 Playwright 设施。代价是 gallery 页面要自己维护，且 story 不能用 `vi.spyOn(API, …)` 打桩，只能用写死的 props 或 `page.route()`。

### 4.4 Vitest browser mode

- Vitest 4.0 去掉了 browser mode 的 `experimental` 标签，provider 改为独立包（`@vitest/browser-playwright` 等），并新增视觉回归断言 `toMatchScreenshot` 与 `toBeInViewport`。——[Vitest 4.0](https://vitest.dev/blog/vitest-4)
- 官方动机：jsdom 这类环境只模拟浏览器，与真实环境可能不一致；缺点是初始化要启动 provider 与浏览器，耗时更长；browser mode 不能完全替代独立的端到端测试运行器。——[Why Browser Mode](https://vitest.dev/guide/browser/why)
- CI 上需要 `playwright` 或 `webdriverio` provider，官方推荐从 Playwright 起步，因为它支持并行执行。可用 `projects` 让 jsdom 单元测试与 browser 测试并存。——[Browser Mode](https://vitest.dev/guide/browser/)
- `toMatchScreenshot` 基线按测试文件、测试名、浏览器与平台存放在 `__screenshots__/`；首次运行生成基线并让测试失败以便人工复核；内置 pixelmatch 比较器支持 `threshold` 与 `allowedMismatchedPixelRatio`；有稳定截图检测，使用 Playwright provider 时自动禁用动画；官方建议把视觉套件单独放进 `*.vrt.test.*` 的 project，并在受控 CI 环境里执行 `vitest --project vrt --update`。——[Vitest Visual Regression Testing](https://vitest.dev/guide/browser/visual-regression-testing)

取舍：适合「组件级、需要真实布局」的单元测试，可以去掉 §1.1 中 9 个文件对布局 API 的打桩。视觉回归能力与 Playwright 重叠，且本仓 Vitest 还在 4.x，接入要同步考虑 5.x 升级。页面级验收仍需 Playwright。

### 4.5 对比

| 方案 | 构建链 | 截图 | 交互 | 额外成本 | 结论 |
|---|---|---|---|---|---|
| Storybook 10 + Vitest addon | 独立 `.storybook/`，Vite 8 兼容 | 原生走 Chromatic 云；自建需另接 Playwright | `play` 函数 | 依赖多、第二个 dev server | 不采用 |
| Ladle 5 | 自带 Vite 6 | 自建 Playwright | 无内建断言 | 版本分叉、发布停滞 | 不采用 |
| Playwright gallery（1.62+） | 本仓 Vite dev server | `toHaveScreenshot` | Playwright Locator | 自维护 gallery 页面 | 采用，P2 |
| Vitest browser mode | 本仓 Vite | `toMatchScreenshot` | Vitest locator | 新 provider 依赖、启动较慢 | 可选，P3 |

## 5. 「内容溢出但不可达」探针

### 5.1 判据所依据的规范事实

- `overflow: hidden`：内容在内边距盒处被裁切，溢出时元素是没有滚动条的滚动容器，仍可通过 Tab 到被隐藏的可聚焦元素、`scrollLeft`、`scrollTo()` 等方式滚动。`overflow: clip`：元素不是滚动容器，被裁内容不可见，不支持程序化滚动。`auto` / `scroll` 会显示滚动条。——[MDN overflow](https://developer.mozilla.org/en-US/docs/Web/CSS/overflow)。所以 `hidden` / `clip` 上的溢出对鼠标与触控用户就是「不可达」。
- `scrollHeight` 是包含溢出部分的内容高度，取整数；内容无需纵向滚动条时等于 `clientHeight`。MDN 给出的 `isScrollable()` 示例正是「`scrollHeight > clientHeight` 且 `overflowY` 为 `scroll` 或 `auto`」，探针取它的反面。——[MDN scrollHeight](https://developer.mozilla.org/en-US/docs/Web/API/Element/scrollHeight)
- `checkVisibility()` 默认对没有盒子（`display: none` / `contents`）或被 `content-visibility: hidden` 跳过渲染的元素返回 `false`；`opacityProperty`、`visibilityProperty` 选项再排除透明度为 0 与 `visibility: hidden`；2024 年 3 月起各浏览器均可用。——[MDN checkVisibility](https://developer.mozilla.org/en-US/docs/Web/API/Element/checkVisibility)
- `text-overflow` 只作用于块容器内联方向的溢出，需要配合 `overflow: hidden` 与 `white-space: nowrap`。——[MDN text-overflow](https://developer.mozilla.org/en-US/docs/Web/CSS/text-overflow)。因此「横向溢出且 `text-overflow: ellipsis`」可以判定为刻意截断。

### 5.2 算法

```js
function findUnreachableOverflow({ tolerance = 1 } = {}) {
  const CLIPPING = new Set(["hidden", "clip"]);
  const out = [];
  for (const el of [document.documentElement, ...document.body.querySelectorAll("*")]) {
    if (el.closest("[data-overflow-ok]")) continue;               // 显式豁免，属性值写原因
    if (!el.checkVisibility({ visibilityProperty: true, opacityProperty: true })) continue;
    const cs = getComputedStyle(el);
    const clipX = CLIPPING.has(cs.overflowX), clipY = CLIPPING.has(cs.overflowY);
    if (!clipX && !clipY) continue;
    if (el.clientWidth <= 1 || el.clientHeight <= 1) continue;     // sr-only、折叠态
    const overX = clipX && el.scrollWidth - el.clientWidth > tolerance;
    const overY = clipY && el.scrollHeight - el.clientHeight > tolerance;
    if (!overX && !overY) continue;
    if (overX && !overY && cs.textOverflow === "ellipsis") continue;                  // 单行省略
    if (overY && cs.webkitLineClamp !== "none" && cs.webkitLineClamp !== "") continue; // 多行 line-clamp
    out.push({ el, axis: (overX ? "x" : "") + (overY ? "y" : "") });
  }
  return out;
}
```

要点：

- `tolerance = 1`：`scrollHeight` 等属性取整，亚像素布局会产生 1px 的假溢出。
- 豁免必须带原因（`data-overflow-ok="装饰光晕"`），审查时可 grep。
- 在 Playwright 里用 `page.evaluate()` 执行，结果非空则断言失败并输出元素的选择器路径、溢出轴与尺寸。

### 5.3 合成页面实测

在 Playwright 1.63.0 驱动的 headless Chromium 中，对一个包含 12 种情形的合成页面，在 1280×720、1440×900、1920×1080、2560×1440 四个视口下运行，四个视口结果一致，单次 `evaluate` 耗时 11–16 ms：

| 情形 | 期望 | 结果 |
|---|---|---|
| 外壳 `height` 固定 + `overflow: hidden`，内部 flex 链漏设 `min-height: 0`，滚动区撑高 | 报告 | 报告（外壳，y 轴） |
| 同上，flex 链逐层设 `min-height: 0` | 放过 | 放过（内部滚动区正常滚动） |
| `overflow-x: hidden` 容器内放 600px 宽内容 | 报告 | 报告（x 轴） |
| `overflow: clip` 裁掉的内容 | 报告 | 报告（y 轴） |
| 固定高度 + `overflow: hidden` 裁掉的长表单 | 报告 | 报告（y 轴） |
| 单行 `truncate` | 放过 | 放过 |
| `-webkit-line-clamp: 2` | 放过 | 放过 |
| `sr-only` | 放过 | 放过 |
| `height: 0` 折叠态 | 放过 | 放过 |
| `object-fit: cover` 图片 | 放过 | 放过 |
| 圆角卡片内超出边界的绝对定位装饰层 | 视为刻意 | **误报**，需加 `data-overflow-ok` |
| 同上，加了 `data-overflow-ok` | 放过 | 放过 |

### 5.4 局限

- **报告的是裁切者，不是肇事者。** 漏设 `min-h-0` 时被报告的是外层 `overflow: hidden` 的外壳，真正缺属性的是中间某层 flex 子项。输出里应附上从裁切者到最深溢出子元素的路径，帮助定位。
- **只检查当前状态。** 弹层、抽屉、折叠面板要先打开再跑；内容长度取决于 fixture，fixture 需要准备长文本、多条目的压力变体，否则探针看到的永远是「刚好放得下」。
- **不覆盖的裁切方式。** 祖先的 `clip-path`、被 fixed / sticky 元素遮挡的内容、视口外的 `position: fixed` 内容，都不经过 `overflow` 属性，探针检测不到。
- **真实页面的误报规模未知。** 本仓 139 行 `overflow-hidden` 中有多少是装饰层或动画容器，需要在真实页面上跑一轮才知道（待实测）。真实页面 DOM 规模远大于合成页面，单次耗时预计在数十毫秒量级（估算）。
- **必须在真实浏览器里运行。** jsdom 对布局属性返回 0（§2），放进现有 Vitest 套件只会全部通过。

与 [#2967](https://github.com/ArcReel/ArcReel/issues/2967)（滚动失效的系统性排查）的边界：#2967 负责找出根因与写法规范；本探针负责把规范变成可回归的守卫，排查时也可直接用它扫描现状。

### 5.5 同一位置顺带跑 axe

地图 Notes 要求 WCAG 2.2 AA。Playwright 官方的无障碍测试方案是 `@axe-core/playwright` 的 `AxeBuilder`，可用 `withTags([...])` 限定规则集，并建议用 fixture 集中配置标签与已知问题的排除项；官方同时说明自动化测试无法发现所有 WCAG 违规。——[Accessibility testing](https://playwright.dev/docs/accessibility-testing)。axe-core 的 WCAG 2.2 A/AA 规则（含 `target-size`，标签 `wcag22aa`）默认关闭，需要显式加入标签；`scrollable-region-focusable`（可滚动区域可经键盘访问）属于 `wcag2a`。——[axe-core 规则表](https://github.com/dequelabs/axe-core/blob/develop/doc/rule-descriptions.md)。axe 与溢出探针一样不需要基线，可以在同一个用例里先后执行。

## 6. 与现有测试的分工

| 层 | 工具 | 运行环境 | 守什么 | 不守什么 |
|---|---|---|---|---|
| 行为契约（现有） | Vitest + Testing Library | jsdom | ARIA 角色与可访问名称、状态流转、`API` 调用参数、i18n 文案、键盘交互 | 布局、滚动、视觉、真实尺寸 |
| 页面级守卫（新增） | Playwright + 溢出探针 + axe | Docker 中的 Chromium | 各视口下不可达溢出、WCAG 自动规则 | 业务逻辑分支 |
| 页面级视觉（新增） | Playwright `toHaveScreenshot` | 同上 | 重做中：改前改后评审材料；重做后：回归闸门 | 尚在重做的区域不设闸门 |
| 原语状态（可选） | Playwright gallery + `*.story.tsx` | 同上 | 原语各状态（禁用、错误、长文本、加载）的外观与交互 | 页面组合 |
| 需要真实布局的单元测试（可选） | Vitest browser mode | Chromium | 替换 9 个打桩布局 API 的文件 | 视觉基线 |
| 人工验收 | 原型分支 + 维护者评审 | 本地 dev server | 视觉方向、动效手感、信息架构 | — |

迁移现有测试时的原则：

- 用 `ByRole` 断言的用例（如 `ProviderModelSelect.test.tsx`）按 ARIA 契约保留；新原语若改变了角色结构（例如组合框从手写改为 Popover + Command），先在新组件上核对 `combobox` / `listbox` / `option` 是否保持，再决定是改测试还是改实现。具体原语的语义取决于 [#2960](https://github.com/ArcReel/ArcReel/issues/2960)（Radix 还是 Base UI）的结论。
- 断言 `className` / `toHaveClass` 的 131 处属于实现细节，换 token 命名与原语时按 `docs/standards/testing.md`「每条用例保护一个可观察的契约」处置：能用角色、状态或可见文本表达的改写，表达不了且只为样式而存在的删除，样式由截图层兜底。
- 不把截图或溢出断言塞进 jsdom 套件。

## 7. 推荐

### 7.1 选用的手段与优先级

| 优先级 | 手段 | 何时启用 | 理由 |
|---|---|---|---|
| P0 | Playwright 页面级套件骨架：Docker 镜像钉版本、仅 Chromium、`retries: 0`、`page.route()` fixture、`page.clock` 固定时间、按视口分 project | 重构第一个 PR 之前 | 其余手段都依赖它；fixture 与稳定化一次做好 |
| P0 | 溢出探针 + axe（含 `wcag22aa`）作为断言 | 与骨架同时 | 不需要基线，重构期间就能当闸门；直接针对地图 Notes 中「每个区域只有一个滚动容器」等滚动与溢出标准 |
| P1 | 页面级截图对比 | 字体自托管落地后；按区域从「评审材料」升格为「回归闸门」 | 重构期间外观主动变化，过早设闸门只会制造基线更新负担 |
| P2 | Playwright gallery（`*.story.tsx`）承载原语状态 | `components/ui/` 的 shadcn 原语稳定后 | 复用本仓 Vite 与同一套 Playwright 设施，替代 Storybook |
| P3 | Vitest browser mode | 有需要时，与 Vitest 5 升级一并评估 | 只为去掉布局打桩，非验收必需 |
| 不采用 | Storybook、Ladle、Chromatic 等云服务 | — | 第二套构建链或外部服务，收益与 Playwright 重叠 |

### 7.2 CI 落地形态

- 在 `test.yml` 新增 `frontend-e2e` job，同样由 `frontend` 域触发并纳入 `ci-required`；`container` 用 `mcr.microsoft.com/playwright:v<与 @playwright/test 相同版本>-noble`；`pnpm build` 后用 `webServer` 启动 `vite preview`；失败时上传 `playwright-report/`。
- `@playwright/test` 的版本与镜像标签必须同步升级，Dependabot 升级 npm 包时要连同 workflow 中的镜像标签一起改（否则找不到浏览器，见 §3.2）。
- 预计成本（估算，待实测）：约 60 个用例时单 job 5–8 分钟，与现有前端 job 并行；公开仓库不产生 Actions 费用。基线只为重做完成的区域提交，优先截取区域。
- 本地：开发者通过 `run-server` 远程模式在容器里生成与 CI 一致的基线；不在宿主机直接生成基线。

### 7.3 待其他票决定的输入

- 视口清单与最低支持视口：[#2964](https://github.com/ArcReel/ArcReel/issues/2964)。
- 渐进替换还是集中重写，决定「区域重做完成」的粒度与截图升格时机：[#2961](https://github.com/ArcReel/ArcReel/issues/2961)。
- 原语底座与其 ARIA 结构，决定现有角色断言能保留多少：[#2960](https://github.com/ArcReel/ArcReel/issues/2960)。
- 滚动写法规范，决定探针的豁免清单：[#2967](https://github.com/ArcReel/ArcReel/issues/2967)。
