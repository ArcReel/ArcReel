---
name: hyperframes-auto-edit
description: 把 ArcReel 已生成的视频单元转成项目内 HyperFrames 工程，并根据剧本与用户 Instruction 自动生成剪辑方案、调整成片时间线、配置背景音乐或使用 HyperFrames Studio。
---

# HyperFrames 自动剪辑

使用 ArcReel 提供的工程边界接入官方 HyperFrames Studio，不复制、不修改 HyperFrames 框架。

## 指令来源与优先级

自动剪辑不是把一句提示直接交给 Studio。先把四类输入编译成一份 `EDITING_PLAN.md`，再据此修改时间线：

1. HyperFrames 可渲染性、安全与项目写入边界，优先级最高。
2. 用户本轮明确给出的 Instruction；保留原意，发生冲突时覆盖默认风格和节奏。
3. ArcReel 正式剧本、已生成视频、字幕、真实时长和项目设计规范；这些是不可编造的事实。
4. `references/editing-prompt-guide.md` 的 Route / Spec / Beats / Copy / Technique / Negatives 方法，只负责补足用户未指定的剪辑决策。

用户没有额外 Instruction 时也能自动剪辑；不要为了常规风格选择暂停询问。用户明确说“不要音乐”“不要字幕”“保持原顺序”等时必须服从。

## 自动剪辑工作流

1. 调用 `mcp__arcreel__prepare_hyperframes_episode`，传入集号和本次声音版本。
2. 读取工具返回的 `source_script`、`workspace.write_boundary`、`workspace.entry_file`、`workspace.editing_plan_file`，再读取工程内 `manifest.json` 与 `DESIGN.md`。
3. 按 `references/editing-prompt-guide.md` 分析剧本，先完整写好 `EDITING_PLAN.md`：
   - Source Facts：剧本主题、项目类型、真实时长、原始单元顺序与可用素材；
   - User Overrides：逐条保留本轮用户 Instruction；无额外要求就写“无”；
   - Route and Spec；
   - Rhythm and Beats：每拍必须有起止时间，并覆盖整集但不越过真实媒体边界；
   - Copy：只引用正式剧本/字幕中的文案，不擅自改写；
   - Technique：剪切、转场、字幕、镜头运动和视觉层次；
   - Background Music；
   - Negative Constraints。
4. 这是“自动剪辑”模式：计划写完后在同一轮继续编辑 `index.html`，不等待中间确认。保留稳定的 `id`、`data-unit-id`、素材路径和与用户要求无关的已有 Studio 编辑。
5. 完成后运行官方 HyperFrames lint/check；报告剪辑方案路径、Studio Tab 与任何阻断问题。

## 背景音乐

- 用户明确不要音乐时不生成、不嵌入。
- 用户允许自动配乐时，根据剧本的主题、情绪弧线、节奏和受众判断。通常超过 10 秒的叙事、旁白、广告或口播成片需要一条贯穿全片的音乐；纯课程讲解段可保持无音乐或只在片头片尾使用。
- 需要音乐时调用 `mcp__arcreel__generate_hyperframes_bgm`。`direction` 只描述曲风、BPM/速度感、主要乐器、情绪弧线和编排，不写歌词；工具会强制纯 instrumental、无任何人声，并按整集时长调用 Croco GPU / MiniMax Music 3。
- 把工具返回的 `html_snippet` 原样放入 composition root。它是从 0 秒开始的一条连续背景音乐，音量固定为 `0.150`（15%）；不要提高音量，不拆成逐镜歌曲，不从外部 URL 引用音乐。
- 已有旁白时保留 `data-audio-group="voiceover"`，背景音乐使用工具片段中的 `data-audio-group="music"`。如需额外 duck/carve，只能在 15% 基线之下衰减，不能把音乐抬高超过 15%。
- 生成失败时保留无 BGM 的可编辑工程并说明 GPU 阻断，不能伪造音乐文件或改用项目外缓存。

## 不可突破的写入边界

- `write_boundary` 是本次操作唯一可写根目录，通常是 `hyperframes/episode_XX/`。
- 只写 `index.html`、`DESIGN.md`、`EDITING_PLAN.md` 以及 ArcReel MCP 工具返回的工程内媒体；不修改 `project.json`、`scripts/`、原始 `videos/` / `reference_videos/`、ArcReel 源码或其它集工程。
- 不修改 `media/` 中已有的源素材副本，也不改 `manifest.json` 的来源证据。
- 不运行 `hyperframes init`，不在项目中安装、复制或改写 HyperFrames 包。
- 不把工程、缓存、音乐或渲染结果写到项目目录以外。

## HTML 工程约束

- `index.html` 是时间线唯一真相源。用 `data-composition-id`、`data-start`、`data-duration`、`data-media-start`、`data-track-index` 表达组合与轨道。
- 视频元素必须 `muted playsinline`；需要原声时使用独立 `<audio>` 元素，避免重复音轨。
- 所有动画必须由媒体时间驱动、可 seek、可重复；禁止 `Math.random()`、墙钟和不可复现的定时器。
- 保留稳定的元素 `id` 和 `data-unit-id`，不要破坏 Studio 对元素与时间线的映射。
- 媒体 URL 使用工程内相对路径，禁止外部 URL，也不覆盖源媒体。
- 改时间线时以实际媒体时长为边界；字幕保持在对应单元区间内。

## 失败边界

- 工具报告缺少已完成视频、声音版本不成立或工程不完整时，原样说明阻断原因，不自行伪造媒体或删除目录重建。
- Studio 启动与渲染由 Web 端官方 Studio 负责；不要尝试用替代编辑器绕过它。
