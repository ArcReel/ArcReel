---
name: generate-script
description: 使用 Gemini API 生成 JSON 剧本。使用场景：(1) 用户运行 /generate-script 命令，(2) 已完成 Step 1/2 需要生成最终剧本，(3) 用户想用 Gemini 替代 Claude 生成剧本。读取 step1_segments.md 和 project.json，调用 gemini-3-flash-preview 生成符合 Pydantic 模型的 JSON 剧本。
---

# generate-script

使用 Gemini API 生成 JSON 剧本。

## 前置条件

1. 项目目录下存在 `project.json`（包含 style、overview、characters、clues）
2. 已完成 Step 1：
   - narration：`drafts/episode_N/step1_segments.md`
   - drama：`drafts/episode_N/step1_normalized_script.md`
3. 已完成 Step 2：角色和线索已写入 `project.json`

## 用法

```bash
# 生成指定剧集的剧本
python .claude/skills/generate-script/scripts/generate_script.py --episode <N>

# 指定输出路径
python .claude/skills/generate-script/scripts/generate_script.py --episode <N> --output <path>

# 预览 Prompt（不实际调用 API）
python .claude/skills/generate-script/scripts/generate_script.py --episode <N> --dry-run
```

## 示例

```bash
# 生成第 1 集的剧本
python .claude/skills/generate-script/scripts/generate_script.py --episode 1

# 预览将发送给 Gemini 的 Prompt
python .claude/skills/generate-script/scripts/generate_script.py --episode 1 --dry-run
```

## 输出

生成的 JSON 文件保存至 `projects/<project>/scripts/episode_N.json`

## 支持的模式

- **narration**（说书模式）：9:16 竖屏，保留原文到 novel_text
- **drama**（剧集动画模式）：16:9 横屏，场景改编
