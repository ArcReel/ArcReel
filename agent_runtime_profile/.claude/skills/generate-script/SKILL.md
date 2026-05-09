---
name: generate-script
description: 使用当前配置的文本后端生成 JSON 剧本。由 create-episode-script subagent 调用。读取 step1 中间文件和 project.json，调用当前文本模型生成符合 Pydantic 模型的 JSON 剧本。
user-invocable: false
---

# generate-script

使用当前配置的文本后端生成 JSON 剧本。此 skill 由 `create-episode-script` subagent 调用，不直接面向用户。

## 前置条件

1. 项目目录下存在 `project.json`（包含 style、overview、characters、scenes、props）
2. 已完成 Step 1 预处理（按 `effective_mode` 选择一种中间文件）：
   - narration（图生视频/宫格生视频 + 说书）：`drafts/episode_N/step1_segments.md`
   - drama（图生视频/宫格生视频 + 剧集动画）：`drafts/episode_N/step1_normalized_script.md`
   - reference_video（参考生视频）：`drafts/episode_N/step1_reference_units.md`

## 用法

```bash
# 生成指定剧集的剧本
python .claude/skills/generate-script/scripts/generate_script.py --episode {N}

# 自定义输出路径
python .claude/skills/generate-script/scripts/generate_script.py --episode {N} --output scripts/ep1.json

# 预览 Prompt（不实际调用 API）
python .claude/skills/generate-script/scripts/generate_script.py --episode {N} --dry-run
```

## 生成流程

脚本内部通过 `ScriptGenerator` 完成以下步骤：

1. **加载 project.json** — 读取 content_mode、characters、scenes、props、overview、style
2. **加载 Step 1 中间文件** — 根据 content_mode 选择 `step1_segments.md`（narration）或 `step1_normalized_script.md`（drama）
3. **构建 Prompt** — 将项目概述、风格、角色、场景、道具和中间文件内容组合成完整 prompt
4. **调用当前文本后端** — 文本模型按以下优先级解析：项目级 `text_backend_script` → 系统级 `text_backend_script` → `default_text_backend` → 第一个 ready 的文本供应商。脚本启动时会打印实际使用的 `provider/model`。
5. **Pydantic 验证** — 按 effective_mode 选 schema：
   - reference_video → `ReferenceVideoScript`（含 `video_units[]`）
   - narration → `NarrationEpisodeScript`
   - drama → `DramaEpisodeScript`
6. **补充元数据** — 写入 episode、content_mode、统计信息（片段/场景数、总时长）、时间戳

## 输出格式

生成的 JSON 文件保存至 `scripts/episode_N.json`，核心结构：

- `episode`、`content_mode`、`novel`（title、chapter、source_file）
- narration 模式：`segments` 数组（每个片段包含 visual、novel_text、duration_seconds 等）
- drama 模式：`scenes` 数组（每个场景包含 visual、dialogue、action、duration_seconds 等）
- reference_video 模式：`video_units` 数组（每个 unit 含 `shots[]`、`references[]`、`duration_seconds` 等），`metadata.total_units`
- `metadata`：total_segments/total_scenes、created_at、generator
- `duration_seconds`：全集总时长（秒）

## `--dry-run` 输出

打印将发送给当前文本后端的完整 prompt 文本，不调用 API、不写文件。用于检查 prompt 质量和长度。

> 三种生成模式的数据路径、预处理 subagent、schema 选择详见 `.claude/references/generation-modes.md`。
