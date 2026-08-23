---
name: hyperframes-auto-edit
description: 把 ArcReel 已生成的视频单元转成项目内 HyperFrames 工程，并在用户要求自动剪辑、调整成片时间线或使用 HyperFrames Studio 时编辑该工程。
---

# HyperFrames 自动剪辑

使用 ArcReel 提供的工程边界接入官方 HyperFrames Studio，不复制、不修改 HyperFrames 框架。

## 工作流

1. 调用 `mcp__arcreel__prepare_hyperframes_episode`，传入集号和本次声音版本。
2. 读取工具返回的 `workspace.write_boundary`、`workspace.entry_file` 和工程内 `DESIGN.md`。
3. 只在 `write_boundary` 目录内工作；需要自动编排时只编辑 `entry_file`（HTML）和 `DESIGN.md`。
4. 告知用户从 Web 端当前集的 `HyperFrames` tab 查看、继续编辑和渲染。Studio 已打开时会由它自己的 watcher 热更新。

工具发现已有工程时会原样返回，不能覆盖用户在 Studio 中做过的编辑。

## 不可突破的写入边界

- `write_boundary` 是本次操作唯一可写根目录，通常是 `hyperframes/episode_XX/`。
- 不修改 `project.json`、`scripts/`、原始 `videos/` / `reference_videos/`、ArcReel 源码或其它集的工程。
- 不修改 `media/` 中的已暂存媒体，也不改 `manifest.json` 的来源证据。
- 不运行 `hyperframes init`，不在项目中安装、复制或改写 HyperFrames 包。
- 不把工程、缓存或渲染结果写到项目目录以外。

## HTML 工程约束

- `index.html` 是时间线唯一真相源。用 `data-composition-id`、`data-start`、`data-duration`、`data-track-index` 表达组合与轨道。
- 视频元素必须 `muted playsinline`；需要原声时使用独立 `<audio>` 元素，避免重复音轨。
- 所有动画必须由媒体时间驱动、可 seek、可重复；禁止 `Math.random()`、墙钟和不可复现的定时器。
- 保留稳定的元素 `id` 和 `data-unit-id`，不要破坏 Studio 对元素与时间线的映射。
- 媒体 URL 使用工程内相对路径，禁止外部 URL，也不覆盖源媒体。
- 改时间线时以实际媒体时长为边界；字幕保持在对应单元区间内。

## 失败边界

- 工具报告缺少已完成视频、声音版本不成立或工程不完整时，原样说明阻断原因，不自行伪造媒体或删除目录重建。
- Studio 启动与渲染由 Web 端官方 Studio 负责；不要尝试用替代编辑器绕过它。
