# 图表库选型：设置页趋势图与顶栏迷你图

> 状态：调研完成，结论供地图 [#2286](https://github.com/ArcReel/ArcReel/issues/2286) 汇总。
> 关联：[#2393](https://github.com/ArcReel/ArcReel/issues/2393)（本票）、[#2290](https://github.com/ArcReel/ArcReel/issues/2290)（设置页使用记录原型，手绘 SVG 趋势图）。
> 数据日期：2026-09-07。版本、发布日期、许可证来自 npm registry（`npm view`）；仓库活跃度来自 GitHub REST / Search API（`gh api`）；整包体积来自 bundlephobia API；tree-shaken 体积来自本机 Vite 8.2.2 + rolldown 实测（见 §3.2）；API 事实来自各库官方文档（Context7）与安装到 `node_modules` 的源码 / 类型定义。

## 结论一句话

**推荐 visx（`@visx/shape` 等按包引入）：SVG + React 原生、不注入任何字体 / 颜色 / 动画、堆叠柱 + tooltip 实测 28.5 KB gzip，dataviz 四条标记规范全部原生可做，且与原型手绘结构一一对应，迁移约半天到一天；备选 Recharts 3：声明式、键盘无障碍内建，但 125.5 KB gzip，且顶端圆角与段间隙两条规范都要走自定义 `shape`。**

## 结论速览

| 问题 | 一行结论 |
|---|---|
| 1 硬约束筛选 | 七个候选都能在 React 19 + Vite 8 + TS 下跑；echarts-for-react（供应链事件 + React 19 类型 issue 未关）与 uPlot（无官方 React 封装、堆叠柱靠示例插件）在「React 生态」这一条上最弱。 |
| 2 体积（堆叠柱 + tooltip，gzip） | uPlot 26.8 KB + 0.8 KB CSS < visx 28.5 KB < Chart.js 57.0 KB < Plot 94.6 KB < nivo 97.8 KB < Recharts 125.5 KB < ECharts 203.7 KB。 |
| 3 标记规范原生程度（≤ 24px / 顶端圆角 / 段间隙 / 图例四条） | visx 4/4（段间隙靠 render-prop 减高度）；Chart.js、uPlot、Plot 各 3/4；Recharts、nivo、ECharts 各 2/4。明细见 §3.3。 |
| 4 推荐 / 备选 | 推荐 visx，备选 Recharts；若将来体积成为唯一决定因素再考虑 Chart.js（canvas，与 oklch CSS 变量体系不兼容，需手工解析色值）。 |
| 5 迁移工作量 | 原型 → visx：0.5–1 人日；原型 → Recharts：约 1 人日；两者都要另加「表格替代」与测试，这部分与库无关。 |

## 1. 环境事实（仓库核实）

- `frontend/pnpm-lock.yaml`：`react@19.2.8`、`react-dom@19.2.8`、`vite@8.2.2`、`tailwindcss@4.3.3`、`typescript@6.0.3`；`frontend/package.json` 没有任何图表 / d3 依赖。
- `frontend/src/index.css`：Tailwind v4 `@theme` 定义 oklch 色板（`--color-hairline*`、`--color-text*`、`--color-accent*` 等）与字体（`--font-sans` Inter、`--font-mono` JetBrains Mono），`.num` 类使用 mono 字体；`@custom-variant dark` 存在但产品是暗色单主题。
- `frontend/src/main.tsx`：`createRoot` 直接挂载，**没有** `<StrictMode>` 包裹，也没有 `hydrateRoot` / SSR。因此 SSR 是非需求；StrictMode 兼容只影响将来是否能开启。
- 手绘原型 `frontend/src/components/pages/settings/prototype/UsageTrendChart.tsx`（分支 `prototype/2290-usage-records-settings`，201 行）：自己算 `niceMax` / 刻度 / `scaleBand` 等价的 slot 宽度、`ResizeObserver` 测宽、`<pattern>` 斜线填充「已取消」、路径手绘顶端 4px 圆角、段间 2px 间隙、整桶 hover 的 HTML tooltip、`role="img"` + `aria-label`。
- dataviz skill `references/marks-and-anatomy.md`（bundled-skills 2.1.263）：柱 ≤ 24px、4px 圆角只在数据端、基线处方角；段间 2px **面色间隙**而不是描边；网格 hairline 1px 实线；≥ 2 个序列图例常驻；文字用文字 token 不用序列色。`interaction.md`：柱状图每根柱 / 段自带 hover tooltip，tooltip 列出该 X 的全部序列。

## 2. 候选逐一核实

各库「维护状态」四个数字：最新版发布日、仓库最后一次 commit、2026-06-01 以来新开 / 关闭的 issue 数（GitHub Search API，`is:issue`）。

### 2.1 Recharts 3.10.1（MIT）

- 维护：3.10.1 发布 2026-07-25；最后 commit 2026-09-07；2026-06 以来 issue 新开 39 / 关闭 56；27.5k stars。peerDeps 明确列出 React 19。
- React 19 / StrictMode：无未关闭的 React 19 兼容 issue；StrictMode 相关只有 2022 年已关闭的 ResponsiveContainer 问题（#2878）。
- 无障碍：`accessibilityLayer` 在 3.x 默认为 `true`（`es6/state/rootPropsSlice.js`），根 `<svg>` 得到 `role="application"` + `tabIndex=0`（`es6/container/RootSurface.js`），键盘左右键驱动 tooltip（`es6/state/keyboardEventsMiddleware.js`）；图表接受 `title` / `desc` prop 渲染 `<title>` / `<desc>`；也可自行覆盖 `role`。表格替代需自写。—— [storybook Accessibility.mdx](https://github.com/recharts/recharts/blob/main/storybook/stories/API/Accessibility.mdx)
- 样式可定制：SVG 文字不注入字体；`CartesianGrid` 的 `stroke` / `strokeDasharray` 都是 prop（默认取主题 `theme.grid`，dark 主题只定义 `stroke: '#3f3f46'`，标记为 `@experimental`）；tooltip 用 `content` prop 换成自家 HTML；动画需每个 `<Bar>` 传 `isAnimationActive={false}`。
- 堆叠 / tooltip：`stackId` 堆叠；BarChart 的 tooltip 默认 `axis` 触发即整桶（`es6/chart/BarChart.js: defaultTooltipEventType: "axis"`）；`maxBarSize` 限宽；`radius` 支持 `[tl, tr, br, bl]` 四角（`src/cartesian/Bar.tsx`）；`shape` prop 接受自定义组件（[Customise.mdx](https://github.com/recharts/recharts/blob/main/storybook/stories/Customise.mdx)）；`<defs><pattern>` 可作为子元素直接写入。
- 体积：整包 147.5 KB gzip（bundlephobia）；tree-shaken 堆叠柱 + tooltip 125.5 KB gzip（内含 redux toolkit、d3 子集、react-smooth 等）。

### 2.2 visx 4.0.0（MIT，Airbnb）

- 维护：4.0.0 发布 2026-06-11（"v4 official with React 19 support?" #2005 已关闭）；最后 commit 2026-06-22；2026-06 以来 issue 新开 4 / 关闭 5；21k stars。peerDeps `react ^18 || ^19`。
- React 19 / StrictMode：`@visx/tooltip`、`@visx/responsive` 源码中已无 `findDOMNode`（早年 StrictMode issue #883/#1009 已关闭）。
- 无障碍：visx 只产出 `<rect>` / `<path>`，`<svg>` 是我们自己写的，`role` / `aria-label` / `<title>` 随意加；表格替代自写。
- 样式可定制：`@visx/shape`、`@visx/scale`、`@visx/grid`、`@visx/pattern` 不带任何字体 / 颜色默认值；只有 `@visx/axis` 的刻度文字默认 `fill:#222; fontFamily:'Arial'; fontSize:10`（`esm/axis/AxisBottom.js`），用 `tickLabelProps` 覆盖或干脆沿用原型自己画刻度；`@visx/tooltip` 有 `defaultStyles`，传 `unstyled` 即无样式（`esm/tooltips/Tooltip.js`）。没有动画。
- 堆叠 / tooltip：`BarStack` 基于 d3-shape stack，children render-prop 给出每段 `x/y/width/height/color/bar.data`（`packages/visx-shape/src/shapes/BarStack.tsx`）；`BarRounded` 原生支持 `top` / `topLeft` 等单边圆角（`esm/shapes/BarRounded.js`）；`PatternLines` 输出 `<pattern>`（[visx-pattern README](https://github.com/airbnb/visx/blob/master/packages/visx-pattern/Readme.md)）；`useTooltip` + `TooltipWithBounds` / `useTooltipInPortal` 管 tooltip 位置（[visx-tooltip README](https://github.com/airbnb/visx/blob/master/packages/visx-tooltip/Readme.md)）；`ParentSize` 管响应式。
- 体积：`@visx/shape` 整包 10.7 KB gzip；tree-shaken 堆叠柱 + 网格 + 双轴 + tooltip + pattern + ParentSize 合计 28.5 KB gzip；迷你图（`LinePath` + `scaleLinear`）单独 15.2 KB，与堆叠柱共用后增量 1.7 KB。

### 2.3 nivo 0.99.0（MIT）

- 维护：0.99.0 发布 2025-05-23，此后 15 个月无新版本；最后 commit 2026-07-21；2026-06 以来 issue 新开 2 / 关闭 3；14k stars。peerDeps 含 React 19。
- React 19 / StrictMode：React 19 支持 issue #2678 已关闭；仍有一个 React 19 key 警告 issue 开着（#2801，Choropleth 图例，不影响 bar）。
- 无障碍：`@nivo/bar` 类型定义原生有 `role`、`ariaLabel`、`ariaLabelledBy`、`ariaDescribedBy`、`isFocusable`、`barAriaLabel`、`barAriaHidden`（`dist/types/types.d.ts` 178–197 行），是七个候选里 SVG 级无障碍属性最全的。表格替代自写。
- 样式可定制：`theme` 对象覆盖 `text.fontFamily/fontSize`、`grid.line`、`axis.ticks`、`tooltip`（`@nivo/theming/dist/types/types.d.ts`）；`animate={false}` 关动画，但 react-spring 仍在包里；`tooltip` prop 换自家组件。
- 堆叠 / tooltip：`groupMode="stacked"`；`defs` + `fill` 规则（`patternLinesDef`）原生图案；`innerPadding` 在堆叠模式下同样作用于段间（源码 `nivo-bar.mjs` 堆叠生成器读取 `innerPadding`）；`borderRadius` 是单个数字、四角同时圆（`types.d.ts` 152 行）；tooltip 是**每段**触发，但回调拿到的 `ComputedDatum.data` 是整行原始数据，可自行渲染整桶；无 `maxBarWidth`，柱宽由容器宽 × `padding` 决定。
- 体积：整包 79.9 KB gzip（`sideEffects: true`）；tree-shaken 堆叠柱 + pattern + tooltip 97.8 KB gzip（含 `@nivo/core`、react-spring、d3 子集）。

### 2.4 react-chartjs-2 5.3.1 + Chart.js 4.5.1（MIT，canvas）

- 维护：Chart.js 4.5.1 发布 2025-10-13，最后 commit 2026-05-27，2026-06 以来 issue 新开 10 / 关闭 2；react-chartjs-2 5.3.1 发布 2025-10-27，最后 commit 2026-05-26，2026-06 以来 issue 0 / 0。React 19 peer 已加（#1235 / #1238 / #1308 已关闭）。
- React 19 / StrictMode：无相关 issue；封装层只是 `useEffect` 建销实例。
- 无障碍：canvas 只能在 `<canvas>` 上放 `role="img"` + `aria-label` 与 fallback 内容（[accessibility.html](https://www.chartjs.org/docs/latest/general/accessibility.html)），没有逐段语义；表格替代自写。
- 样式可定制：`Chart.defaults.font.family` 全局字体；`animation: false`；canvas **不能直接用 CSS 变量**，Darkroom 的 oklch token 需要 `getComputedStyle` 解析后再喂给 `backgroundColor`；hover tooltip 用 `external` 回调改为 HTML。
- 堆叠 / tooltip：`scales.x/y.stacked`；`interaction.mode: 'index', intersect: false` 整桶 tooltip（[interactions.html](https://www.chartjs.org/docs/latest/configuration/interactions.html)）；`maxBarThickness` 限宽；`borderRadius` + `borderSkipped: 'middle'` 在堆叠时只圆最外层段（源码 `dist/chart.js` `setBorderSkipped`：`edge === 'middle' && stack` 时按 `stack._top` / `_bottom` 决定）；图案填充要自己用 `ctx.createPattern` 造 `CanvasPattern`（[colors.html](https://www.chartjs.org/docs/latest/general/colors.html)）；段间隙没有原生选项。
- 体积：整包 68.4 KB gzip；tree-shaken 只注册 `BarController/BarElement/CategoryScale/LinearScale/Tooltip` 57.0 KB gzip；加折线迷你图 62.1 KB。

### 2.5 uPlot 1.6.32（MIT，canvas）

- 维护：1.6.32 发布 2025-03-14，此后 18 个月无新版本；最后 commit 2026-04-22；2026-06 以来 issue 新开 5 / 关闭 2；单一作者维护。无官方 React 封装，社区 `uplot-react@1.2.4`（2025-07-05）；官方文档建议自己在 effect 里 `new uPlot()` / `setData()` / `destroy()`（[llms.txt](https://context7.com/leeoniya/uplot/llms.txt)）。
- 无障碍：canvas，同 Chart.js；图例是 DOM 元素。
- 样式可定制：必须引入 `uPlot.min.css`（gzip 0.8 KB）；`series.fill` 类型是 `CanvasRenderingContext2D['fillStyle'] | (self, idx) => fillStyle`（`dist/uPlot.d.ts` 794 行），可返回 `CanvasPattern`；无动画。
- 堆叠 / tooltip：**没有堆叠概念**，官方 `stacked-series.html` / `bars-grouped-stacked.html` 示例靠预先累加数据 + `bands` + 200 行 `seriesBarsPlugin`；`uPlot.paths.bars({ size: [factor, maxPx], radius, gap })`，`radius` 可按序列返回 `[top, bottom]`（`high-low-bands.html` 示例只圆顶层）；`gap` 是相邻柱之间不是段之间；没有 tooltip，需用 `setCursor` hook 写插件（`legendAsTooltipPlugin` 示例）。
- 体积：整包 21.9 KB gzip；实测堆叠柱 + uplot-react 26.8 KB gzip + 0.8 KB CSS。定位是万点级时序折线，类目堆叠柱是「能做但要写插件」。

### 2.6 @observablehq/plot 0.6.17（ISC，SVG）

- 维护：0.6.17 发布 2025-02-14，此后 19 个月无新版本；最后 commit 2026-09-01（main 上持续有提交）；2026-06 以来 issue 新开 9 / 关闭 3，open issue 251。
- React：命令式，`useEffect` 里 `Plot.plot()` 返回 SVG 后 `append` 到 ref，卸载 `remove()`（[getting-started.md](https://github.com/observablehq/plot/blob/main/docs/getting-started.md)）；SSR 需传虚拟 `document`。不走 React 协调，hover 由 Plot 自己的 DOM 事件驱动。
- 无障碍：顶层 `ariaLabel` / `ariaDescription` 写到根 SVG（[accessibility.md](https://github.com/observablehq/plot/blob/main/docs/features/accessibility.md)）。
- 样式可定制：根 SVG 硬编码 `font-family="system-ui, sans-serif"` 属性（`src/plot.js` 254 行），需用 `style` 选项或 `className` + CSS 覆盖；无动画。
- 堆叠 / tooltip：`barY` + 隐式 `stackY`；`ry2` / `rx1y1` 等单边圆角原生（[rect.md](https://github.com/observablehq/plot/blob/main/docs/marks/rect.md)）；`inset` / `insetTop` 原生段间隙；`tip` + `pointerX` 只显示最近**一个** datum 的通道，整桶需先把数据 pivot 成每桶一行再用 `channels` 列出（[tip.md](https://github.com/observablehq/plot/blob/main/docs/marks/tip.md)）；`fill` 字面量只接受 CSS 颜色（[marks.md](https://github.com/observablehq/plot/blob/main/docs/features/marks.md)），`url(#pattern)` 不是颜色，图案填充要在渲染后改 DOM。
- 体积：整包 128.0 KB gzip；tree-shaken 实测 94.6 KB gzip（d3 全家桶随包）。

### 2.7 echarts-for-react 3.0.6 + ECharts 6.1.0（MIT / Apache-2.0）

- 维护：ECharts 6.1.0 发布 2026-05-19，最后 commit 2026-09-04，2026-06 以来 issue 新开 40 / 关闭 195，极活跃。echarts-for-react 3.0.6 发布 2026-01-21，最后 commit 2026-01-21，2026-06 以来 issue 1 / 0。
- **供应链事件**：2026-05-20 issue #625 记录 2026-05-19 "Mini Shai-Hulud" npm 攻击中，维护者账号被盗，恶意版本 `echarts-for-react 3.0.7 / 3.1.7 / 3.2.7` 与 `size-sensor 1.0.4 / 1.1.4 / 1.2.4` 被发布；维护者回复已全部 deprecated 并请 npm 删除。另有 React 19 JSX 命名空间类型错误 #628（2026-07-03，open）和 StrictMode 下 ref / effect 拿不到实例 #608（open）。若选 ECharts 应直接用 `echarts/core` 自写 hook，不引入这层封装。
- 无障碍：`aria.enabled` 后自动在图表 DOM 上生成 `aria-label`（`lib/visual/aria.js` 134/206 行），`aria.label.description` 可自定义；`aria.decal` 内建图案（[aria.md](https://github.com/apache/echarts-handbook/blob/master/contents/en/best-practices/aria.md)）。
- 样式可定制：`textStyle.fontFamily` 全局；`animation: false`；`SVGRenderer` 可选；ECharts 6 自带新默认主题，所有颜色需显式覆盖。
- 堆叠 / tooltip：`stack` 原生；`tooltip.trigger: 'axis'` 整桶；`barMaxWidth` 限宽；`itemStyle.borderRadius` 逐项设置（`lib/chart/bar/BarView.js` 700 行 `el.setShape('r', ...)`），顶层才圆需按桶逐 datum 写 `itemStyle`；段间隙只能用 `borderColor` = 面色 + `borderWidth: 2` 的描边模拟；`itemStyle.decal` 原生图案。
- 体积：整包 368 KB gzip；tree-shaken（`echarts/core` + BarChart + Tooltip + Grid + Aria + SVGRenderer + `echarts-for-react/lib/core`）203.7 KB gzip，加折线 214.8 KB。

## 3. 横向对比

### 3.1 硬约束与维护

| 库 | 渲染 | React 19 peer | StrictMode 已知问题 | 最新版发布 | 最后 commit | issue 新开 / 关闭（2026-06 起） | 许可证 |
|---|---|---|---|---|---|---|---|
| Recharts 3.10.1 | SVG | ✅ 明确 | 无 | 2026-07-25 | 2026-09-07 | 39 / 56 | MIT |
| visx 4.0.0 | SVG | ✅ 明确 | 无 | 2026-06-11 | 2026-06-22 | 4 / 5 | MIT |
| nivo 0.99.0 | SVG | ✅ 明确 | 无（#2801 是 key 警告） | 2025-05-23 | 2026-07-21 | 2 / 3 | MIT |
| react-chartjs-2 5.3.1 / Chart.js 4.5.1 | canvas | ✅ 明确 | 无 | 2025-10-27 / 2025-10-13 | 2026-05-26 / 2026-05-27 | 0 / 0 与 10 / 2 | MIT |
| uPlot 1.6.32 | canvas | 无 peer（自写 effect） | 无 | 2025-03-14 | 2026-04-22 | 5 / 2 | MIT |
| Plot 0.6.17 | SVG（命令式） | 无 peer（自写 effect） | 无 | 2025-02-14 | 2026-09-01 | 9 / 3 | ISC |
| echarts-for-react 3.0.6 / ECharts 6.1.0 | canvas 或 SVG | `>=16`（类型 issue #628 open） | #608 open | 2026-01-21 / 2026-05-19 | 2026-01-21 / 2026-09-04 | 1 / 0 与 40 / 195 | MIT / Apache-2.0 |

### 3.2 体积（gzip，KB）

实测方法：草稿目录建 Vite 8.2.2 项目，`build.lib` 单入口 ES 输出，`react` / `react-dom` / `react/jsx-runtime` 设为 external，oxc 压缩，对输出 JS 做 gzip。入口只引用堆叠柱（三个序列）+ tooltip + 网格 + 轴所需模块；「迷你图」入口只引折线；「合并」是两者同时引用。bundlephobia 一列是整包不 tree-shake 的参考值。

| 库 | 堆叠柱 + tooltip | 迷你图单独 | 合并 | bundlephobia 整包 |
|---|---|---|---|---|
| visx | **28.5** | 15.2 | 30.2 | 10.7（仅 `@visx/shape`） |
| uPlot（+uplot-react） | 26.8 + 0.8 CSS | — | 26.9 | 21.9 |
| Chart.js + react-chartjs-2 | 57.0 | 53.2 | 62.1 | 68.4 + 1.0 |
| Observable Plot | 94.6 | — | 102.0 | 128.0 |
| nivo | 97.8 | — | — | 79.9（仅 `@nivo/bar`） |
| Recharts | 125.5 | 101.0 | 130.1 | 147.5 |
| ECharts（SVG renderer）+ echarts-for-react/lib/core | 203.7 | 195.6 | 214.8 | 368.0 + 3.6 |

对照：原型手绘 SVG 为 0 KB 依赖。顶栏悬浮层迷你图若用同一库，增量只有「合并 − 堆叠柱」一列（visx 1.7 KB，Recharts 4.6 KB）。

### 3.3 dataviz 标记规范逐条核对

「原生」= 一个 prop / 选项；「半原生」= 库给几何数据、我们自己画；「hack」= 要绕开库的抽象。

| 规范 | Recharts | visx | nivo | Chart.js | uPlot | Plot | ECharts |
|---|---|---|---|---|---|---|---|
| 柱 ≤ 24px | 原生 `maxBarSize` | 半原生 `Math.min(24, bandwidth)` | hack（无 maxWidth，按容器宽反推 `padding`） | 原生 `maxBarThickness` | 原生 `size: [f, 24]` | hack（无 maxWidth，反推 `padding`） | 原生 `barMaxWidth` |
| 顶端 4px 圆角、只在堆叠顶层 | hack（`radius` 只按序列；顶层为 0 的桶要自定义 `shape`） | 原生 `BarRounded top` + render-prop 判断顶层 | hack（`borderRadius` 四角同圆，需自定义 `barComponent`） | 原生 `borderRadius` + `borderSkipped:'middle'` | 原生 `radius` 函数按序列返回 `[top, 0]` | 原生 `ry2` / `rx1y1`（顶层为 0 的桶仍要 pivot 处理） | hack（逐 datum `itemStyle.borderRadius`） |
| 段间 2px 面色间隙 | hack（自定义 `shape` 减高度） | 半原生（render-prop 减 2px 高度） | 原生 `innerPadding` | hack（`borderWidth` 面色描边） | hack（预堆叠时减值） | 原生 `insetTop` | hack（`borderColor` 面色描边） |
| 「已取消」图案填充 | 原生（子元素 `<defs><pattern>` + `fill="url()"`） | 原生 `PatternLines` | 原生 `defs` + `fill` 规则 | hack（手造 `CanvasPattern`） | 半原生（`fill` 函数返回 `CanvasPattern`） | hack（`fill` 只认 CSS 颜色，渲染后改 DOM） | 原生 `decal` |
| 整桶 tooltip | 原生（axis 触发） | 半原生（自写，桶数据在手） | 半原生（每段触发，`data` 是整行） | 原生 `mode:'index'` | hack（插件） | hack（pivot + `channels`） | 原生 `trigger:'axis'` |
| 图例常驻 | 原生 `<Legend>` | 自写 HTML（原型已有） | 原生 legends | 需注册 Legend 插件（canvas 内） | DOM 图例 | `color.legend` | 原生 `legend` |
| 网格 hairline 实线 / 字体 / 色板 | prop 逐个覆盖 | 无默认可覆盖（Axis 例外） | `theme` 一处覆盖 | 全局 defaults + 解析 CSS 变量 | CSS 变量可用（DOM 部分）| `style` / `className` 覆盖内联 `font-family` | `textStyle` 全局覆盖 |
| 去掉动画 | 每个元素 `isAnimationActive={false}` | 无动画 | `animate={false}` | `animation:false` | 无动画 | 无动画 | `animation:false` |

## 4. 推荐与取舍

**推荐：visx。** 理由：

1. 标记规范是决定性的：dataviz 的四条硬规范里，只有 visx（和 Plot）做到不 hack；而 Plot 的 React 集成是命令式、tooltip 与图案都要绕。
2. 「Darkroom 程度」的可定制性：visx 不注入字体、颜色、动画、CSS，所有像素都由我们的 JSX 决定，Tailwind 类和 oklch token 直接用；Recharts / nivo 要逐 prop 关默认值，canvas 三家要解析 CSS 变量。
3. 体积：28.5 KB gzip 是 SVG 方案里最小的，迷你图增量 1.7 KB。
4. 与原型同构：原型已经是「scale + stack + path + HTML tooltip」结构，visx 只是把手写的 `niceMax` / slot 计算 / 堆叠累加 / ResizeObserver / tooltip 定位换成 d3-scale、BarStack、ParentSize、useTooltip，JSX 几乎不动。
5. 维护：Airbnb 出品，4.0.0 明确支持 React 19，issue 量少且有回应。

代价：visx 不是「配一下就出图」，轴、图例、tooltip 视觉都要自己写（原型已写）；将来若要饼图 / 雷达等其他形态，每种都要再拼一次。

**备选：Recharts 3。** 若团队更看重声明式写法与开箱即用的键盘导航 / `role="application"` 无障碍层，Recharts 是最活跃、React 19 最稳的选择；代价是体积约为 visx 的 4.4 倍，且圆角与段间隙（含顶层判断）都要塞进一个自定义 `shape` 组件，最终「手写量」与 visx 相当。

**不推荐的原因**：nivo 15 个月未发版、无 `maxBarWidth`、圆角四角同圆；Chart.js 与 uPlot 的 canvas 与 oklch CSS 变量体系、per-segment 语义、Tailwind tooltip 不兼容；Plot 19 个月未发版、命令式集成、图案与整桶 tooltip 都要绕；ECharts 体积 200 KB+ 且封装层刚经历供应链事件。

## 5. 迁移工作量估算（原型 → 推荐库）

原型 `UsageTrendChart.tsx` 201 行，含 `mergeWeeks` / `niceMax` / 手绘路径 / tooltip / `TrendLegend`。

原型 → visx（0.5–1 人日）：

| 原型代码 | 替换为 | 备注 |
|---|---|---|
| `niceMax` + `divisions` 刻度 | `scaleLinear({ domain, range, nice: true })` + `scale.ticks(4)` | 去掉约 15 行 |
| `slot` / `barW` | `scaleBand({ domain, range, padding })` + `Math.min(24, bandwidth)` | |
| 手写堆叠累加 | `BarStack keys={...} value={...}` render-prop | 顶层判断保留（`segs.slice(idx+1).every(v===0)`） |
| 手绘圆角 path | `BarRounded top radius={4}` 或保留原路径 | 段间 2px 仍在 render-prop 里减 |
| `<pattern>` | `PatternLines` | 或保留原 `<pattern>` |
| `ResizeObserver` | `ParentSize` | |
| `hover` state + `tipLeft` | `useTooltip` + `TooltipWithBounds unstyled` | tooltip 内容 JSX 原样保留 |
| 轴刻度 `<text>` | 保留手写（避开 `@visx/axis` 的 Arial 默认） | |
| 新增 | 视觉隐藏的 `<table>` 表格替代、i18n key、vitest 渲染测试 | 与库无关，都要做 |

迷你图（顶栏悬浮层）：`LinePath` + `curveMonotoneX` + 两个 `scaleLinear`，约 30 行，可与趋势图共用 `@visx/scale`。

原型 → Recharts（约 1 人日）：数据要 reshape 成每桶一行；写一个 `StackedSegment` shape 组件承担圆角 / 顶层判断 / 段间隙 / 图案；`Tooltip content` 换原型 tooltip；每个 `<Bar>` 关动画；`ResponsiveContainer` 需固定高度父容器；无障碍层免费获得。

## 6. 数据来源清单

- npm registry：`npm view <pkg> version license time`，2026-09-07。
- GitHub：`gh api repos/<r>`、`repos/<r>/releases`、`repos/<r>/commits`、`search/issues?q=repo:<r>+is:issue+created:>2026-06-01`，2026-09-07。
- bundlephobia：`https://bundlephobia.com/api/size?package=<pkg>@<ver>`，2026-09-07。
- tree-shaken 体积：本机草稿项目，Vite 8.2.2 / rolldown 1.2.7 / React 19.2.8，入口见 §3.2 描述。
- 文档：Context7 拉取的各库官方文档 / 仓库 Markdown（链接见各小节）。
- 源码：`node_modules` 中 recharts 3.10.1、@visx/* 4.0.0、@nivo/bar 0.99.0、chart.js 4.5.1、uplot 1.6.32、@observablehq/plot 0.6.17、echarts 6.1.0 的 dist / 类型文件（路径见各小节）。
- echarts-for-react 供应链事件：[hustcc/echarts-for-react#625](https://github.com/hustcc/echarts-for-react/issues/625)。
