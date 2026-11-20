# 使用记录趋势图的图表库选型实测

2026-09-07，针对 [#2393](https://github.com/ArcReel/ArcReel/issues/2393) 对堆叠柱状图、整桶提示和折线图原型进行了选型实测。调研阶段原本推荐 visx，前端在后续随 shadcn/ui 重构时最终采用了备选方案 Recharts。现行入口见[图表组件](../../frontend/src/components/ui/chart.tsx)与[使用记录趋势图](../../frontend/src/components/usage/UsageTrendChart.tsx)。

调研中的权衡是体积、可定制性和组件复用。visx 的 SVG 原语体积较小，需自行处理整桶提示。Recharts 内建提示和键盘交互，圆角只画在非零的堆叠顶层、段间留缝则需要定制柱形。这里的体积属于实验小包，不能作为站点产物的大小。

## 构建产物实测（gzip，KB）

实测方法：在草稿目录中新建 Vite 8.2.2 项目，采用 `build.lib` 单入口输出 ES 模块，将 `react` / `react-dom` / `react/jsx-runtime` 设为 external，经 oxc 压缩后对输出 JS 做 gzip 统计。入口中，「堆叠柱 + tooltip」仅引入堆叠柱（三个序列）、tooltip、网格与坐标轴所需模块；「迷你图」入口仅引入折线模块；「合并」为两者同时引入。bundlephobia 一列为不做 tree-shake 时的整包参考值。

| 库 | 堆叠柱 + tooltip | 迷你图单独 | 合并 | bundlephobia 整包 |
|---|---|---|---|---|
| visx | **28.5** | 15.2 | 30.2 | 10.7（仅 `@visx/shape`） |
| uPlot（+uplot-react） | 26.8 + 0.8 CSS | — | 26.9 | 21.9 |
| Chart.js + react-chartjs-2 | 57.0 | 53.2 | 62.1 | 68.4 + 1.0 |
| Observable Plot | 94.6 | — | 102.0 | 128.0 |
| nivo | 97.8 | — | — | 79.9（仅 `@nivo/bar`） |
| Recharts | 125.5 | 101.0 | 130.1 | 147.5 |
| ECharts（SVG renderer）+ echarts-for-react/lib/core | 203.7 | 195.6 | 214.8 | 368.0 + 3.6 |

手绘 SVG 原型不引入额外图表库依赖。若折线图与堆叠柱状图共用同一套依赖，可用「合并」减去「堆叠柱 + tooltip」估算当时的边际增量，例如 visx 为 1.7 KB、Recharts 为 4.6 KB。

各候选库版本、标记规范及未采纳方案详见[原始调研快照](https://github.com/ArcReel/ArcReel/blob/fed952b5f25fb48a9b26e70a5878705c9ecbf988/docs/research/chart-library.md)。
