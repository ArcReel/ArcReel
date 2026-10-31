# ffmpeg-skill 优点迁移计划

评估对象：[neilchina/ffmpeg-skill](https://github.com/neilchina/ffmpeg-skill)（fork 自 kajisho5/ffmpeg-skill，MIT，455 commits，活跃）。
结论：**有借鉴价值，选择性迁移**。不整体引入（其形态是 42 个 Python 标准库 CLI 脚本 + Agent Skill + MCP 壳，与本仓 `lib/` 异步服务架构不匹配），迁移的是它的设计与交付质量，用本仓自己的模式实现。

## 1. 对方做什么，我们已有什么

ffmpeg-skill 的核心是「给编码 agent 一个本地视频编辑器」，十条设计原则：

1. Probe first：先实测文件再决定操作
2. Lossless when possible：能流拷贝就不重编码
3. Plan before render：`--dry-run` / `--json` / `--timeout` / `--overwrite`
4. 机器可读契约（SPEC）：每个工具的 input_schema 从代码运行时派生，CI 防漂移
5. MCP 面从契约派生
6. 能力检测（doctor）：跑之前查 ffmpeg 编码器/过滤器齐不齐
7. Unknown is not missing：检测失败报 `unknown`，不假装 available/missing
8. 验证结果：输出重新 probe，按目标规格 check，画面变了看 contact sheet
9. 一份报告一个结论（Done / Failed）
10. 保留原件：永不覆盖输入

对照本仓现状：

| 能力 | 本仓现状 | 结论 |
| --- | --- | --- |
| 先规划后渲染 | `lib/final_cut/render_plan.py` 帧网格规划 + `commit_rendered_artifact` 的 render/accept 两段 | 已有，不迁移 |
| 机器可读契约 | pydantic 模型派生 `ToolDeclaration.input_schema`（ADR-0087），前端 i18n/摘要一致性有测试兜底 | 等价已有，不迁移 |
| 保留原件 | 渲染产物走版本化 artifact，写入新文件原子替换 | 已有，不迁移 |
| 报告纪律 | `ToolOutcome`/`ToolProblem` + 工具摘要 | 已有，不迁移 |
| 媒体探测 | `lib/infra/media_probe.py`：只有容器格式 + 流类型/编码/时长 | **缺** fps、分辨率、旋转、色彩、声道 |
| 成片验收 | `accept_final_cut`：音视频流齐全 + 时长容差 | **缺** 响度/峰值、平台规格、内容信号 |
| 响度 | BGM 登记时一次性静态增益到 −16 LUFS（无峰值上限）；成片混音后不做任何响度处理 | **缺** 成片级 loudness + true peak |
| 平台交付 | 无平台概念；字幕按横竖屏布局，不考虑平台 UI 遮挡 | **缺** |
| 画面验证 | `lib/video_review/` 有 contact sheet + 黑屏/卡帧/切点信号，但只用于**素材单元**（`inspect_video_units`），成片没有 | **缺** 成片联系表 |
| 能力检测 | `lib/infra/ffmpeg.py` 只做 `-version` 自检 | **缺** 组件级检查 |
| 原片剪辑类（无损 cut/join、静音删除、多机位、HDR/LUT、稳定、360） | 不需要 | **不迁移**（见 §4） |

## 2. 迁移阶段

按依赖排序，P0 是 P1/P2/P3 的地基。每阶段可独立出票（`to-tickets` 拆单），阶段间只有 P0 是硬阻塞。

### P0 探测增强 + ffmpeg 组件自检（地基）

**做什么**

1. `lib/infra/media_probe.py` 的 `MediaProbe`/`MediaStream` 增加每流字段：
   - 视频：`width`、`height`、`fps`（帧均速 + VFR 标记，framecrc 已有逐包时间戳可直接算）、`rotation`、`bit_depth`、`color_space`（含 HDR 标记）
   - 音频：`sample_rate`、`channel_count`
   - 实现沿用现有 `framecrc` 解析路径 + stderr 里的流信息，不新增 ffprobe 依赖（本仓 ffmpeg 构建不带 ffprobe）
2. `lib/infra/ffmpeg.py` 的 `FfmpegStatus` 扩展组件检查：
   - 解析 `ffmpeg -encoders` / `-filters` / `-bsfs`，核对渲染链路需要的组件：`libx264`、`aac`、`subtitles`（libass）、`loudnorm`、`ebur128`、`xfade`、`scale`、`aresample`、`blackdetect`、`freezedetect`
   - 三态 `available / missing / unknown`（照抄其原则 7：解析失败一律 `unknown`，不得当成 available 或 missing）
   - 启动时 `log_ffmpeg_status` 输出缺失项；缺失且影响渲染时，`FinalCutService.check` 以明确错误码拒绝（现在是 ffmpeg 跑到一半才失败）

**验收**

- `probe_media` 对合成视频（不同 fps/分辨率/旋转/声道）报告正确；VFR 文件标记 VFR
- 单测覆盖三态检测；`tests/integration/lib/infra/test_ffmpeg.py`、`test_media_probe.py` 扩展
- 现有 `probe_media` 23 个调用点不受影响（字段只增不改）

**工作量**：约 2–3 天

### P1 成片响度归一 + 峰值限制

**做什么**

1. 新增 `lib/final_cut/loudness.py`（或 `lib/audio/loudness.py`，随 glossary 归属定）：
   - 两遍 EBU R128 `loudnorm`：第一遍 `ebur128` 实测（复用 `lib/bgm/loudness.py` 的解析），第二遍 `loudnorm=I=…:TP=…:LRA=…:linear=true` 重编码
   - 默认目标常量：`I=−14 LUFS`、`TP=−1.5 dBTP`（社媒短视频主流目标；ffmpeg-skill 默认 −14/−1）。暂不做每项目配置，常量入 `lib/final_cut/` 常量区
2. `lib/final_cut/ffmpeg_render.py` 渲染链路：`audio_mix_args` 产出混音后，插入「实测 → loudnorm 归一」两遍，替换原 `audio.m4a` 再 mux；deadline 沿用 `RenderDeadlines.for_seconds` 按比例加
3. `accept_final_cut`（`ffmpeg_render.py:401`）验收扩展：重测成片的 integrated loudness + true peak，写入 `FinalCutAcceptance`（新字段 `integrated_lufs`、`true_peak_dbtp`），偏离目标超容差记 warning（不 fail——响度归一后偏离说明源内容极端，警告即可）
4. `FinalCutRender`/渲染记录落库这些字段；工具摘要与前端成片面板展示「响度 −13.8 LUFS / 峰值 −1.6 dBTP」

**不做**：不改动 BGM 登记的静态增益逻辑（−16 LUFS 是 BGM 素材属性，成片级归一才是交付保障）；不做 per-track 动态压缩/人声净化（TTS + BGM 场景不需要）

**验收**

- 响度极端 BGM（如 +6 LUFS 过响）混入后成片仍落在目标 ±0.5 LUFS、峰值不超 TP
- 全静音/无声源不报错（与 `lib/bgm` 的 `SILENCE_LOUDNESS_LUFS` 判据一致）
- 集成测试：渲染一条含旁白 + BGM 的时间线，断言 acceptance 字段在目标内
- 性能回归：渲染耗时增幅可接受（两遍音频编码相对整段视频渲染开销小）

**工作量**：约 3 天

### P2 平台交付规格 + 字幕安全区

**做什么**

1. 新增 `lib/delivery/platforms.py`：平台规格表（照其 `check.py`/`export.py` 共用的 delivery table 思路，单一数据源）：
   - 首批平台：`douyin`、`tiktok`、`youtube`（含 shorts）、`bilibili`、`x`、`instagram`（reels）
   - 每平台字段：允许的宽高比与分辨率、时长上限、fps 范围、容器/编码、文件大小上限、响度/峰值目标、**UI 安全区**（以画面比例为单位的遮挡矩形：底部栏高、右侧操作列宽、顶部状态栏高）
   - 安全区数值 v1 按各平台 UI 截图标定，表内注明来源与标定日期（其 README 明确安全区随版本变化，这是运维事实而非永久常量）
2. `check_delivery(probe, platform) -> 行列表`：PASS/WARN/FAIL + 每行「字段 / 期望 / 实测 / 修复建议」（照其 check.py 的报告格式；FAIL 项附 fix hint，WARN 项如「接近时长上限」）
   - 输入用 P0 增强后的 `MediaProbe` + P1 的 loudness 实测
3. 接入点：
   - 渲染任务执行时，按项目画幅推导默认平台（9:16 → `douyin`，16:9 → `youtube`）跑一遍 check，结果进 `FinalCutRender` 的 `delivery` 字段
   - `GET .../final-cut` 状态接口增加可选 `platform` 查询参数，允许前端/工具按需重查
   - Agent 侧不新增工具：`render_final_cut` 的结果摘要追加交付检查结论（如「交付检查（抖音）：2 WARN — 时长 119s 接近 120s 上限」）
4. 字幕安全区：`lib/final_cut/subtitles.py` 的 `subtitle_layout(profile)` 增加 `safe_zone` 参数，ASS 的 `MarginV`/行宽/`WrapStyle` 布局时避开安全区（9:16 竖屏：字幕基线上移到「底部栏 + 右侧操作列」之上，行宽收窄到安全区内）；`lib/subtitle_style/baseline.py` 的默认值保持为无平台场景的兜底
   - 烧入字幕的 ass_document 生成路径（`render_plan_to_file` → `_stage_subtitles`）按同一 safe zone 走

**不做**：不做「同一时间线渲染多平台变体」（9:16 + 1:1 + 16:9 三份文件）——那要动 `FinalCutVariant`（narration × subtitles）的身份模型和 artifact key，价值未证实；列为后续议题。平台导出预设（码率档位）同理暂不做：本仓成片编码参数固定，规格表先只用于**检查**

**验收**

- 各平台规格表有来源标注；`check_delivery` 单测覆盖 PASS/WARN/FAIL 三分支与 fix hint
- 9:16 成片在 douyin 平台：字幕不出现在底部栏/右侧操作列区域（用联系表人工核对一次，P3 之后可自动化）
- 前端成片面板展示 delivery 行（复用现有 issue/warning 展示模式）

**工作量**：约 4–5 天（安全区标定是主要不确定性）

### P3 成片联系表（look 纪律）

**做什么**

1. 复用 `lib/video_review/contact_sheet.py`（`build_contact_sheets`）与 `lib/video_review/signals.py`（黑屏/卡帧/切点）作用于**成片文件**：
   - 渲染任务在 accept 之后生成一张联系表（帧数取 8–12，切点优先 + 均匀补点，`lib/video_review` 已有采样策略）
   - 写到成片 artifact 旁（如 `{artifact}_sheet.png`），随 artifact 版本一起登记/失效
2. 成片结果与状态接口返回 `preview_url`；`FinalCutToolResult` 加 `preview_url`
3. 前端成片面板：视频播放器旁展示联系表缩略图（点击放大）
4. agent_runtime_profile 指令更新：`render_final_cut` 成功后，agent 应先取联系表查看 + 读 delivery 结论，再向用户汇报（对应其原则 8「画面变了必须 look」；现有 `inspect_video_units` 只覆盖素材侧）

**验收**

- 渲染一条含转场/定格/黑场的时间线，联系表帧点与切点对齐，黑场帧被 signals 标出
- 前端面板可见；agent 会话中确认新指令被遵守（用现有 agent 集成测试模式加一条断言）

**工作量**：约 2 天（大头是复用现有模块，新代码少）

### P4（可选，低优先）成片内容信号告警

在 P3 的 signals 基础上补 `silencedetect`（音频静音段）与 `freezedetect` 阈值复核，异常段作为 warning 进 `FinalCutRender.warnings`（复用 `TimelineIssue` 或新增 warning 形态）。用于兜底「整段视频单元丢失画面只剩黑场」这类 AI 素材质量问题。可并入 P3，也可后做。

## 3. 各阶段与本仓机制的对齐

- **artifact 生命周期**：P1/P3 新增的落盘文件（loudness 无落盘；sheet 落盘）走 `commit_rendered_artifact` 的 basis/登记机制，sheet 绑定成片版本，成片 stale 则 sheet stale（`lib/artifacts/` 现有机制，ADR-0062）
- **i18n**：新增平台名、delivery 字段、warning 文案全部走 `lib/i18n`（zh/en/vi），不硬编码
- **glossary/ADR**：「交付规格（delivery spec）」「平台安全区」入 `GLOSSARY.md`；P1 的「成片响度归一是渲染的一部分、BGM 静态增益保持」与 P2 的「只检查不渲染变体」各写一条 ADR（0107/0108 起）
- **测试**：按 `docs/agents/testing.md` 选域；新增 `tests/unit/lib/delivery/`、`tests/integration/lib/final_cut/test_loudness.py` 等；每阶段跑 `uv run python scripts/gate.py --changed`
- **许可证**：MIT，概念与数据表可借鉴；不拷贝代码（其代码是 stdlib CLI 脚本，与本仓异步分层不符）；在本文档与相关 ADR 注明来源

## 4. 明确不迁移的

| 能力 | 不迁移理由 |
| --- | --- |
| 无损 cut/join/流拷贝 | 本仓成片恒为「时间线 → 全量重渲染」，素材是 AI 生成的视频单元，无原片剪辑需求 |
| 静音删除 / filler words / 多机位 sync | 输入是 TTS 旁白 + AI 视频 + BGM，不存在人声毛刺与多机位 |
| HDR→SDR / LUT / 色彩校正 | AI 生成素材统一 SDR；用户调色走剪映草稿导出路径 |
| reframe/stabilize/360/redact/deinterlace | 素材无手持抖动、无 360、无台标；画幅在项目创建期已定（ADR-0011） |
| graphics/overlay/karaoke 字幕动画 | 字幕样式已有独立体系（`lib/subtitle_style/`）；花字需求走剪映草稿 |
| 平台导出预设（多文件交付） | 见 P2「不做」：先检查，后考虑多变体渲染 |
| batch 文件夹 / render.py 项目编排器 | 本仓渲染由 generation queue + RenderPlan 承担，职责已有归属 |
| 本地 ASR 转录（whisper 系） | 旁白文本就是剧本原文，无需从音频反推 |

## 5. 开放问题（开工前定）

1. P2 首批平台表是否加 `wechat-channels`（视频号）？安全区数值标定依据（截图/官方规范）需要一次小调研
2. P1 默认目标 −14 LUFS / −1.5 dBTP 是否需要按平台差异化（youtube 长视频常取 −14，广播 −23）——v1 建议单一目标，表里留 per-platform 字段但渲染只用默认
3. P3 联系表帧数上限与文件大小（控制前端加载），沿用 `MAX_FRAMES_PER_SHEET` 现有值即可？
