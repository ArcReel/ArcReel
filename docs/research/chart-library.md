# Chart-library selection: settings trends and header sparklines

> Status: research complete, for map [#2286](https://github.com/ArcReel/ArcReel/issues/2286).
> Outcome: this report recommended visx, but Recharts was implemented through shadcn/ui Chart (`frontend/src/components/ui/chart.tsx`) for usage trends. Retain research-time recommendations below.
> Related: [#2393](https://github.com/ArcReel/ArcReel/issues/2393), this ticket; [#2290](https://github.com/ArcReel/ArcReel/issues/2290), usage-settings prototype with hand-drawn SVG trends.
> Data date:2026-09-07. Versions/releases/licenses: npm registry `npm view`; activity: GitHub REST/Search `gh api`; whole-package sizes: bundlephobia; tree-shaken: local Vite 8.2.2/rolldown (§3.2); APIs: official Context7 docs and installed `node_modules` source/types.

## Recommendation

**visx (`@visx/shape`, etc., per-package): native SVG/React, no injected fonts/colors/animation, measured 28.5 KB gzip stacked bars/tooltips, all four dataviz mark rules achievable natively, prototype-isomorphic, roughly 0.5–1 day migration. Recharts 3 alternative: declarative/built-in keyboard accessibility, but125.5 KB and custom `shape` for top rounding/gaps.**

## Overview

| Question | Finding |
|---|---|
| 1 Constraints | All seven run React 19/Vite 8/TS; echarts-for-react supply-chain/open React 19 type issues and uPlot's unofficial wrappers/plugin stacking are weakest React fits |
| 2 Stacked bars/tooltips gzip | uPlot 26.8+0.8 CSS < visx 28.5 < Chart.js 57.0 < Plot 94.6 < nivo 97.8 < Recharts 125.5 < ECharts 203.7 KB |
| 3 Native marks, ≤24px/top rounding/gaps/legends | visx 4/4, render-prop height reduction for gaps; Chart.js/uPlot/Plot 3/4; Recharts/nivo/ECharts 2/4, §3.3 |
| 4 Choice | visx, Recharts alternative; consider canvas Chart.js only if size becomes sole criterion, requiring oklch variable parsing |
| 5 Migration | Prototype→visx 0.5–1 person-day; Recharts approximately 1; accessible tables/tests extra and library-independent |

## 1. Verified repository environment

- `frontend/pnpm-lock.yaml`: `react@19.2.8`, `react-dom@19.2.8`, `vite@8.2.2`, `tailwindcss@4.3.3`, `typescript@6.0.3`; no chart/d3 dependencies in `frontend/package.json`.
- `frontend/src/index.css`: Tailwind4 `@theme` oklch palette (`--color-hairline*`, `--color-text*`, `--color-accent*`) and fonts (`--font-sans` Inter, `--font-mono` JetBrains Mono), `.num` mono; `@custom-variant dark` exists but product has one dark theme.
- `frontend/src/main.tsx`: direct `createRoot`, **no** `<StrictMode>`, `hydrateRoot`, or SSR. SSR unnecessary; StrictMode compatibility only future-facing.
- Prototype `frontend/src/components/pages/settings/prototype/UsageTrendChart.tsx`, branch `prototype/2290-usage-records-settings`,201 lines: own `niceMax`/ticks/`scaleBand`-equivalent slots, `ResizeObserver`, cancellation `<pattern>`, hand-drawn4px top curves,2px gaps, bucket HTML hovers, `role="img"`/`aria-label`.
- dataviz `references/marks-and-anatomy.md`, bundled-skills2.1.263: bars≤24px,4px data-end rounding only, square baselines;2px **surface-color gaps**, not strokes;1px solid hairlines; permanent legends for≥2series; text tokens, not series colors. `interaction.md`: hover per bar/segment listing every series at X.

## 2. Candidate verification

Maintenance numbers: latest release, last commit, issues opened/closed since 2026-06-01 using GitHub Search `is:issue`.

### 2.1 Recharts 3.10.1, MIT

- Released 2026-07-25; commit 2026-09-07; issues 39/56;27.5k stars; explicit React 19 peers.
- No open React 19 compatibility issues; StrictMode only closed2022 ResponsiveContainer #2878.
- Accessibility:3.x `accessibilityLayer` defaults `true`, `es6/state/rootPropsSlice.js`; root `<svg>` `role="application"`/`tabIndex=0`, `es6/container/RootSurface.js`; left/right tooltip navigation, `es6/state/keyboardEventsMiddleware.js`. `title`/`desc` render `<title>`/`<desc>`; `role` overridable; tables manual. [storybook Accessibility.mdx](https://github.com/recharts/recharts/blob/main/storybook/stories/API/Accessibility.mdx)
- Styling: no SVG fonts; `CartesianGrid` `stroke`/`strokeDasharray` props, defaults `theme.grid`, experimental dark `stroke: '#3f3f46'` marked `@experimental`; own HTML via tooltip `content`; disable each `<Bar>` with `isAnimationActive={false}`.
- Stack/tooltip: `stackId`; default `axis` whole-bucket triggering (`es6/chart/BarChart.js: defaultTooltipEventType: "axis"`); `maxBarSize`; `radius` `[tl, tr, br, bl]`, `src/cartesian/Bar.tsx`; custom `shape` [Customise.mdx](https://github.com/recharts/recharts/blob/main/storybook/stories/Customise.mdx); child `<defs><pattern>`.
- Whole 147.5 KB; tree-shaken125.5 KB including redux toolkit/d3 subsets/react-smooth.

### 2.2 visx 4.0.0, MIT, Airbnb

- Maintenance: 4.0.0 released 2026-06-11 ("v4 official with React 19 support?" #2005 closed); last commit 2026-06-22; since 2026-06, 4 issues opened / 5 closed; 21k stars. peerDeps `react ^18 || ^19`.
- No `findDOMNode` in `@visx/tooltip`/`@visx/responsive`; old StrictMode #883/#1009 closed.
- Only `<rect>`/`<path>`; own `<svg>` permits `role`/`aria-label`/`<title>`; tables manual.
- `@visx/shape`/`@visx/scale`/`@visx/grid`/`@visx/pattern` have no font/color defaults. Only `@visx/axis` tick `fill:#222; fontFamily:'Arial'; fontSize:10`, `esm/axis/AxisBottom.js`; override `tickLabelProps` or retain prototype ticks. `@visx/tooltip` `defaultStyles` removed by `unstyled`, `esm/tooltips/Tooltip.js`; no animation.
- `BarStack` d3-shape render-props `x/y/width/height/color/bar.data`, `packages/visx-shape/src/shapes/BarStack.tsx`; `BarRounded` `top`/`topLeft` selective corners, `esm/shapes/BarRounded.js`; `PatternLines` `<pattern>`, [visx-pattern README](https://github.com/airbnb/visx/blob/master/packages/visx-pattern/Readme.md); `useTooltip`+`TooltipWithBounds`/`useTooltipInPortal`, [visx-tooltip README](https://github.com/airbnb/visx/blob/master/packages/visx-tooltip/Readme.md); responsive `ParentSize`.
- `@visx/shape`10.7 KB whole; stack/grid/axes/tooltip/pattern/ParentSize28.5 KB; standalone `LinePath`+`scaleLinear`15.2 KB, incremental 1.7 KB combined.

### 2.3 nivo 0.99.0, MIT

- Released 2025-05-23,15 months without release; commit 2026-07-21;2/3 issues;14kstars; React 19 peers.
- React 19 #2678 closed; key-warning #2801 open, Choropleth legends not bars.
- `@nivo/bar` has `role`, `ariaLabel`, `ariaLabelledBy`, `ariaDescribedBy`, `isFocusable`, `barAriaLabel`, `barAriaHidden`, `dist/types/types.d.ts`178–197: richest SVG accessibility; tables manual.
- `theme` controls `text.fontFamily/fontSize`, `grid.line`, `axis.ticks`, `tooltip`, `@nivo/theming/dist/types/types.d.ts`; `animate={false}` still bundles react-spring; custom `tooltip`.
- `groupMode="stacked"`; `defs`/`fill` `patternLinesDef`; `innerPadding` also separates stack segments; the `nivo-bar.mjs` stacking generator reads `innerPadding`; scalar `borderRadius` rounds all corners, `types.d.ts`152. **Per-segment** tooltip receives whole-row `ComputedDatum.data`; whole-bucket rendering manual. No `maxBarWidth`; container×`padding` determines widths.
- Whole 79.9 KB (`sideEffects: true`); tree-shaken97.8 KB including `@nivo/core`,react-spring,d3.

### 2.4 react-chartjs-2 5.3.1 + Chart.js 4.5.1, MIT, canvas

- Chart.js released 2025-10-13,commit 2026-05-27,10/2 issues; wrapper released 2025-10-27,commit 2026-05-26,0/0. React 19 peers added, #1235/#1238/#1308 closed.
- No related React 19/StrictMode issues; wrapper only creates/destroys in `useEffect`.
- Canvas `role="img"`/`aria-label`/fallback on `<canvas>`, [accessibility.html](https://www.chartjs.org/docs/latest/general/accessibility.html), without per-segment semantics; manual tables.
- `Chart.defaults.font.family`, `animation: false`; canvas **cannot use CSS variables directly**. Resolve Darkroom oklch through `getComputedStyle` into `backgroundColor`; HTML tooltip `external`.
- `scales.x/y.stacked`; whole-bucket `interaction.mode: 'index', intersect: false`, [interactions.html](https://www.chartjs.org/docs/latest/configuration/interactions.html); `maxBarThickness`; `borderRadius`+`borderSkipped: 'middle'` rounds only outer stack (`dist/chart.js` `setBorderSkipped`, `edge === 'middle' && stack`, `stack._top`/`_bottom`). Manually create `CanvasPattern` via `ctx.createPattern`, [colors.html](https://www.chartjs.org/docs/latest/general/colors.html); no native segment gaps.
- Whole 68.4 KB; only `BarController/BarElement/CategoryScale/LinearScale/Tooltip`57.0 KB; with sparkline62.1 KB.

### 2.5 uPlot 1.6.32, MIT, canvas

- Released 2025-03-14,18 months; commit 2026-04-22;5/2 issues; one maintainer. No official React wrapper; community `uplot-react@1.2.4`,2025-07-05. Official effect `new uPlot()`/`setData()`/`destroy()`, [llms.txt](https://context7.com/leeoniya/uplot/llms.txt).
- Canvas accessibility like Chart.js; DOM legends.
- Required `uPlot.min.css`,0.8 KB gzip; `series.fill` type `CanvasRenderingContext2D['fillStyle'] | (self, idx) => fillStyle`, `dist/uPlot.d.ts`794, supports `CanvasPattern`; no animation.
- **No stacking concept**: examples `stacked-series.html`/`bars-grouped-stacked.html` preaccumulate + `bands` +200-line `seriesBarsPlugin`. `uPlot.paths.bars({ size: [factor, maxPx], radius, gap })`; per-series `radius` `[top, bottom]`, `high-low-bands.html` top-only; `gap` is between bars, not segments. No tooltip; `setCursor` plugin, `legendAsTooltipPlugin` example.
- Whole 21.9 KB; measured 26.8+0.8 CSS with wrapper. Designed for tens-of-thousands-point time series; categorical stacks require plugins.

### 2.6 @observablehq/plot 0.6.17, ISC, SVG

- Released 2025-02-14,19 months; commit 2026-09-01,main active;9/3 issues,251open.
- Imperative `useEffect` `Plot.plot()` SVG→ref `append`, unmount `remove()`, [getting-started.md](https://github.com/observablehq/plot/blob/main/docs/getting-started.md); SSR virtual `document`. Outside React reconciliation; own DOM hovers.
- Root SVG `ariaLabel`/`ariaDescription`, [accessibility.md](https://github.com/observablehq/plot/blob/main/docs/features/accessibility.md).
- Hardcoded `font-family="system-ui, sans-serif"`, `src/plot.js`254; override `style` or `className`/CSS; no animation.
- `barY` implicit `stackY`; native selective `ry2`/`rx1y1`, [rect.md](https://github.com/observablehq/plot/blob/main/docs/marks/rect.md); `inset`/`insetTop` gaps. `tip`+`pointerX` shows nearest **single** datum; bucket tooltips require pivot/`channels`, [tip.md](https://github.com/observablehq/plot/blob/main/docs/marks/tip.md). Literal `fill` accepts CSS colors only, [marks.md](https://github.com/observablehq/plot/blob/main/docs/features/marks.md); `url(#pattern)` requires postrender DOM changes.
- Whole 128 KB; measured 94.6 KB tree-shaken, bundled d3 family.

### 2.7 echarts-for-react 3.0.6 + ECharts 6.1.0, MIT/Apache-2.0

- ECharts released 2026-05-19,commit 2026-09-04,40/195 issues,very active. Wrapper released/committed2026-01-21,1/0 issues.
- **Supply chain**:2026-05-20 #625 records May19 Mini Shai-Hulud compromise, malicious `echarts-for-react 3.0.7 / 3.1.7 / 3.2.7`/`size-sensor 1.0.4 / 1.1.4 / 1.2.4`; maintainer deprecated/requested npm removal. React 19 JSX namespace #628 (2026-07-03) and StrictMode instance/ref/effect #608 open. Prefer own `echarts/core` hook, not wrapper, if selected.
- `aria.enabled` generates DOM `aria-label`, `lib/visual/aria.js`134/206; custom `aria.label.description`; native `aria.decal`, [aria.md](https://github.com/apache/echarts-handbook/blob/master/contents/en/best-practices/aria.md).
- Global `textStyle.fontFamily`, `animation: false`, optional `SVGRenderer`; ECharts 6 new theme needs explicit all-color overrides.
- Native `stack`, whole-bucket `tooltip.trigger: 'axis'`, `barMaxWidth`; per-datum `itemStyle.borderRadius`, `lib/chart/bar/BarView.js`700 `el.setShape('r', ...)`, with per-bucket/per-datum `itemStyle` for top-only rounding. Gaps simulated through surface `borderColor`+`borderWidth: 2`; native `itemStyle.decal`.
- Whole 368 KB; tree-shaken `echarts/core`+BarChart/Tooltip/Grid/Aria/SVGRenderer+`echarts-for-react/lib/core`203.7 KB,214.8with lines.

## 3. Cross-library comparison

### 3.1 Constraints and maintenance

| Library | Rendering | React 19 peer | StrictMode known issues | Latest release | Last commit | Issues opened/closed since 2026-06 | License |
|---|---|---|---|---|---|---|---|
| Recharts 3.10.1 | SVG | ✅ explicit | None | 2026-07-25 | 2026-09-07 | 39 / 56 | MIT |
| visx 4.0.0 | SVG | ✅ explicit | None | 2026-06-11 | 2026-06-22 | 4 / 5 | MIT |
| nivo 0.99.0 | SVG | ✅ explicit | None (#2801 key warning) | 2025-05-23 | 2026-07-21 | 2 / 3 | MIT |
| react-chartjs-2 5.3.1 / Chart.js 4.5.1 | canvas | ✅ explicit | None | 2025-10-27 / 2025-10-13 | 2026-05-26 / 2026-05-27 | 0 / 0 and 10 / 2 | MIT |
| uPlot 1.6.32 | canvas | No peer (own effect) | None | 2025-03-14 | 2026-04-22 | 5 / 2 | MIT |
| Plot 0.6.17 | SVG (imperative) | No peer (own effect) | None | 2025-02-14 | 2026-09-01 | 9 / 3 | ISC |
| echarts-for-react 3.0.6 / ECharts 6.1.0 | canvas or SVG | `>=16` (type issue #628 open) | #608 open | 2026-01-21 / 2026-05-19 | 2026-01-21 / 2026-09-04 | 1 / 0 and 40 / 195 | MIT / Apache-2.0 |

### 3.2 Gzip size, KB

Method: draft Vite 8.2.2 project, single-entry `build.lib` ES output; external `react`/`react-dom`/`react/jsx-runtime`, oxc minification then gzip. Stacked entry imports only three-series bars/tooltips/grid/axes; mini entry only lines; combined imports both. bundlephobia is unshaken whole-package reference.

| Library | Stacked bars + tooltip | Standalone mini | Combined | Whole bundlephobia |
|---|---|---|---|---|
| visx | **28.5** | 15.2 | 30.2 | 10.7 (only `@visx/shape`) |
| uPlot (+uplot-react) | 26.8 + 0.8 CSS | — | 26.9 | 21.9 |
| Chart.js + react-chartjs-2 | 57.0 | 53.2 | 62.1 | 68.4 + 1.0 |
| Observable Plot | 94.6 | — | 102.0 | 128.0 |
| nivo | 97.8 | — | — | 79.9 (only `@nivo/bar`) |
| Recharts | 125.5 | 101.0 | 130.1 | 147.5 |
| ECharts (SVG renderer)+ echarts-for-react/lib/core | 203.7 | 195.6 | 214.8 | 368.0 + 3.6 |

Hand-drawn prototype adds0 KB dependencies. Same-library header miniatures add combined-minus-stack only: visx 1.7 KB,Recharts 4.6 KB.

### 3.3 dataviz mark requirements

Native=one prop/option; seminative=provided geometry, own drawing; hack=bypass abstraction.

| Rule | Recharts | visx | nivo | Chart.js | uPlot | Plot | ECharts |
|---|---|---|---|---|---|---|---|
| Bars≤24px | Native `maxBarSize` | Semi `Math.min(24, bandwidth)` | Hack, no maxWidth, infer `padding` | Native `maxBarThickness` | Native `size: [f, 24]` | Hack, infer `padding` | Native `barMaxWidth` |
| 4px top-only stack rounding | Hack, per-series `radius`, zero-top buckets need `shape` | Native `BarRounded top`+render-prop top checks | Hack, all-corner `borderRadius`, custom `barComponent` | Native `borderRadius`+`borderSkipped:'middle'` | Native per-series `radius` returns `[top, 0]` | Native `ry2`/`rx1y1`, zero-top pivot still needed | Hack per-datum `itemStyle.borderRadius` |
| 2px surface gaps | Hack, custom `shape` height | Semi, render-prop height−2px | Native `innerPadding` | Hack surface `borderWidth` | Hack subtract prestacked values | Native `insetTop` | Hack surface `borderColor` |
| Cancelled patterns | Native child `<defs><pattern>`+`fill="url()"` | Native `PatternLines` | Native `defs`/`fill` rules | Hack manual `CanvasPattern` | Semi `fill` returns `CanvasPattern` | Hack CSS-only `fill`, postrender DOM | Native `decal` |
| Bucket tooltip | Native axis | Semi own data | Semi per-segment, whole-row `data` | Native `mode:'index'` | Hack plugin | Hack pivot/`channels` | Native `trigger:'axis'` |
| Permanent legends | Native `<Legend>` | Own HTML, prototype exists | Native legends | Register canvas Legend | DOM | `color.legend` | Native `legend` |
| Solid hairlines/fonts/palette | Individual props | No defaults except Axis | `theme` | Global defaults+CSS parsing | DOM CSS variables | `style`/`className` override inline `font-family` | Global `textStyle` |
| No animation | Per-element `isAnimationActive={false}` | None | `animate={false}` | `animation:false` | None | None | `animation:false` |

## 4. Recommendation and tradeoffs

**Recommend visx**:

1. Decisive mark rules: only visx/Plot avoid hacks for four hard requirements; Plot's imperative integration/tooltips/patterns require workarounds.
2. Darkroom customization: no injected font/color/animation/CSS; JSX owns every pixel with direct Tailwind/oklch. Recharts/nivo disable defaults individually; three canvas options parse variables.
3. Smallest SVG at 28.5 KB,mini increment1.7 KB.
4. The prototype already uses “scale + stack + path + HTML tooltip”: replace `niceMax`/slots/stack accumulation/ResizeObserver/tooltip positioning with d3-scale/BarStack/ParentSize/useTooltip, nearly unchanged JSX.
5. Airbnb maintenance, explicit React 19 in 4.0.0, low/responsive issue volume.

Cost: not configuration-only; axes/legends/tooltip visuals manual, though prototype exists. Future pies/radars require separate assembly.

**Alternative Recharts 3**: prioritize declarations and built-in keyboard/`role="application"`; most active/stable React 19 choice. Approximately 4.4×visx size; custom `shape` still combines rounding/top detection/gaps, comparable manual work.

**Rejected**: nivo 15 months without releases/no `maxBarWidth`/all-corner curves; Chart.js/uPlot canvas conflicts with oklch, per-segment semantics, Tailwind tooltips; Plot 19 months/imperative/pattern+bucket workarounds; ECharts 200 KB+ and recent wrapper supply-chain compromise.

## 5. Migration estimates

Prototype `UsageTrendChart.tsx`,201 lines: `mergeWeeks`/`niceMax`/paths/tooltips/`TrendLegend`.

Prototype→visx,0.5–1 person-day:

| Prototype | Replacement | Notes |
|---|---|---|
| `niceMax`+`divisions` | `scaleLinear({ domain, range, nice: true })`+`scale.ticks(4)` | Remove approximately 15 lines |
| `slot`/`barW` | `scaleBand({ domain, range, padding })`+`Math.min(24, bandwidth)` | |
| Manual stacks | `BarStack keys={...} value={...}` render-prop | Retain `segs.slice(idx+1).every(v===0)` top check |
| Rounded paths | `BarRounded top radius={4}` or original | Still subtract2px in render-props |
| `<pattern>` | `PatternLines` | Or retain original `<pattern>` |
| `ResizeObserver` | `ParentSize` | |
| `hover`+`tipLeft` | `useTooltip`+`TooltipWithBounds unstyled` | Preserve tooltip JSX |
| Axis `<text>` | Retain manual, avoiding `@visx/axis` Arial | |
| Add | Hidden `<table>`,i18n keys,Vitest rendering | Library-independent |

Header sparkline: `LinePath`+`curveMonotoneX`+two `scaleLinear`,approximately 30 lines, sharing `@visx/scale`.

Prototype→Recharts,approximately 1 day: pivot buckets; `StackedSegment` shape handles rounding/top/gaps/patterns; prototype `Tooltip content`; disable each `<Bar>` animation; fixed-height parent for `ResponsiveContainer`; free accessibility layer.

## 6. Data sources

- npm `npm view <pkg> version license time`,2026-09-07.
- GitHub `gh api repos/<r>`, `repos/<r>/releases`, `repos/<r>/commits`, `search/issues?q=repo:<r>+is:issue+created:>2026-06-01`,2026-09-07.
- bundlephobia `https://bundlephobia.com/api/size?package=<pkg>@<ver>`,2026-09-07.
- Local tree-shaking Vite 8.2.2/rolldown 1.2.7/React 19.2.8,entries§3.2.
- Context7 official documentation/repository Markdown, section links.
- Installed `node_modules`: recharts 3.10.1,@visx/*4.0.0,@nivo/bar0.99.0,chart.js 4.5.1,uplot 1.6.32,@observablehq/plot 0.6.17,echarts 6.1.0 dist/types, paths above.
- Supply-chain incident: [hustcc/echarts-for-react#625](https://github.com/hustcc/echarts-for-react/issues/625).
