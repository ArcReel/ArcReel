---
name: generate-script
description: 调用项目配置的文本模型生成 JSON 剧本（同时产出每个分镜的 image_prompt 与 video_prompt）。由 create-episode-script subagent 调用。读取 step1 中间文件和 project.json，输出符合 Pydantic schema 的剧本。
user-invocable: false
---

# generate-script

调用项目配置的文本生成模型（Gemini / Ark / OpenAI / 自定义供应商，由 project.json 决定），
基于 Step 1 中间文件产出最终的 JSON 剧本。剧本里的 `image_prompt` / `video_prompt`
是后续图像 / 视频生成的"种子"，**Prompt 质量基本决定了画面质量**——所以本 skill 是
ArcReel 整条 pipeline 中最值得重点优化的一环。

## 前置条件

1. 项目目录下存在 `project.json`（含 style / overview / characters / scenes / props）
2. 已完成 Step 1 预处理（按 `effective_mode` 选择一种中间文件）：
   - narration（图生视频 / 宫格生视频 + 说书）：`drafts/episode_N/step1_segments.md`
   - drama（图生视频 / 宫格生视频 + 剧集动画）：`drafts/episode_N/step1_normalized_script.md`
   - reference_video（参考生视频）：`drafts/episode_N/step1_reference_units.md`
   - **ad（广告/短片）例外**：不需要任何 step1 中间文件——创作输入是 `project.json` 的
     `brief` + `products`（含 selling_points）+ `target_duration`，prompt 由后端按审定的
     带货八段框架配比表构建（`products` 为空自动分流通用短片 prompt）

## 用法

通过 MCP 工具调用（项目名由 session 绑定，不需要传）：

```text
mcp__arcreel__generate_episode_script({"episode": N})
mcp__arcreel__generate_episode_script({"episode": N, "dry_run": true})   # 仅预览 prompt
```

输出路径由工具内部固定为 `{project}/scripts/episode_{N}.json`，不支持自定义；
如需重命名或归档，请在 Web 端操作。

**重要：生成剧本必须调用上述 MCP 工具。此 skill 不提供任何 Python/Shell 脚本，不得用 BASH 调 `python .../scripts/*.py`。**

## 生成流程

MCP 工具内部通过 `ScriptGenerator` 完成以下步骤：

1. **加载 project.json** — 读取 content_mode、characters、scenes、props、overview、style
2. **加载 Step 1 中间文件** — 根据 effective_mode 选择对应文件
3. **构建 Prompt** — 由 `lib.prompt_builders_script` 或 `lib.prompt_builders_reference` 生成
4. **调用 TextBackend** — 由 `TextGenerator` 按项目配置选择文本模型，传入 Pydantic schema 作为 `response_schema` 强约束 JSON 结构
5. **Pydantic 验证** — 按 content_mode / effective_mode 选 schema：
   - ad → `AdEpisodeScript`（平铺 `shots[]`，骨架不随生成路径更换；storyboard 路径
     duration 按 supported_durations 枚举硬约束，reference_video 路径为 1-15 秒自由整数）
   - reference_video（narration/drama 下）→ `ReferenceVideoScript`（含 `video_units[]`）
   - narration → `NarrationEpisodeScript`
   - drama → `DramaEpisodeScript`
6. **补充元数据** — `episode`、`content_mode`、`novel`（项目 title + `第N集`）、统计信息（片段 / 场景 / unit 数、总时长）、时间戳。这些字段对 LLM 隐藏（SkipJsonSchema），由后端从 `project.json` 注入，避免 LLM 幻觉污染下游消费方（compose-video 的 mp4 文件名、剪映草稿等）。
   - 注：顶层 `generation_mode` 仅在 narration/drama 的参考生视频剧本中写入（值恒为 `reference_video`）；ad 剧本骨架唯一（仅 `shots[]` + `content_mode`），**不写入顶层 `generation_mode`**，消费方不得按该字段对 ad 剧本分派。

## 输出格式

生成的 JSON 文件保存至 `scripts/episode_N.json`，核心结构：

- `title`：LLM 写入的剧集标题
- `episode` / `content_mode` / `novel`（含 title、chapter）：由后端 `_add_metadata` 注入，不依赖 LLM 输出
- narration 模式：`segments[]`（每个片段含 image_prompt、video_prompt、novel_text、duration_seconds 等）
- drama 模式：`scenes[]`（每个场景含 image_prompt、video_prompt、duration_seconds 等）
- ad 模式：`shots[]`（每个镜头含 section、voiceover_text、products_in_shot、image_prompt、video_prompt、duration_seconds 等），`metadata.total_shots`；总时长偏离 `target_duration` 超阈值仅日志提醒，不阻塞保存；无论生成路径如何均**不含**顶层 `generation_mode`
- reference_video 模式：`video_units[]`（每个 unit 含 `shots[]`、`references[]`、`duration_seconds` 等），`metadata.total_units`，并写入顶层 `generation_mode: "reference_video"`
- `metadata`：total_segments / total_scenes、created_at、generator
- `duration_seconds`：全集总时长（秒），由后端按各分镜时长求和重算

## `--dry-run` 输出

打印将发送给文本模型的完整 prompt 文本，不调用 API、不写文件。用于检查 prompt 质量和长度。

> 三种生成模式的数据路径、预处理 subagent、schema 选择详见 `.claude/references/generation-modes.md`。
