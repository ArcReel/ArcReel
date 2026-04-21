---
name: manage-project
description: 项目管理工具集。使用场景：(1) 分集切分——探测切分点并执行切分，(2) 批量添加角色/场景/道具到 project.json。提供 peek（预览）+ split（执行）的渐进式切分工作流，以及角色/场景/道具批量写入。
user-invocable: false
---

# 项目管理工具集

提供项目文件管理的命令行工具，主要用于分集切分和角色/场景/道具批量写入。

## 工具一览

| 脚本 | 功能 | 调用者 |
|------|------|--------|
| `peek_split_point.py` | 探测目标字数附近的上下文和自然断点 | 主 agent（阶段 2） |
| `split_episode.py` | 执行分集切分，生成 episode_N.txt + _remaining.txt | 主 agent（阶段 2） |
| `add_assets.py` | 批量添加角色/场景/道具到 project.json | subagent |
| `get_video_capabilities.py` | 查当前项目视频模型能力（model 粒度，所有生成模式通用） | **subagent**（执行任务时自行查询） |

## 分集切分工作流

分集切分采用 **peek → 用户确认 → split** 的渐进式流程，由主 agent 在 manga-workflow 阶段 2 直接执行。

### Step 1: 探测切分点

```bash
python .claude/skills/manage-project/scripts/peek_split_point.py --source {源文件} --target {目标字数}
```

**参数**：
- `--source`：源文件路径（`source/novel.txt` 或 `source/_remaining.txt`）
- `--target`：目标有效字数
- `--context`：上下文窗口大小（默认 200 字符）

**输出**（JSON）：
- `total_chars`：总有效字数
- `target_offset`：目标字数对应的原文偏移
- `context_before` / `context_after`：切分点前后上下文
- `nearby_breakpoints`：附近自然断点列表（按距离排序，最多 10 个）

### Step 2: 执行切分

```bash
# Dry run（仅预览）
python .claude/skills/manage-project/scripts/split_episode.py --source {源文件} --episode {N} --target {目标字数} --anchor "{锚点文本}" --dry-run

# 实际执行
python .claude/skills/manage-project/scripts/split_episode.py --source {源文件} --episode {N} --target {目标字数} --anchor "{锚点文本}"
```

**参数**：
- `--source`：源文件路径
- `--episode`：集数编号
- `--target`：目标有效字数（与 peek 一致）
- `--anchor`：切分点的锚点文本（10-20 字符）
- `--context`：搜索窗口大小（默认 500 字符）
- `--dry-run`：仅预览，不写文件

**定位机制**：target 字数计算大致偏移 → 在 ±window 范围内搜索 anchor → 使用距离最近的匹配

**输出文件**：
- `source/episode_{N}.txt`：前半部分
- `source/_remaining.txt`：后半部分（下一集的源文件）

## 角色/场景/道具批量写入

从项目目录内执行，自动检测项目名称：

⚠️ 必须单行，JSON 使用紧凑格式，不可用 `\` 换行：

```bash
python .claude/skills/manage-project/scripts/add_assets.py --characters '{"角色名": {"description": "...", "voice_style": "..."}}' --scenes '{"场景名": {"description": "..."}}' --props '{"道具名": {"description": "..."}}'
```

## 字数统计规则

- 统计非空行的所有字符（包括标点）
- 空行（仅含空白字符的行）不计入

## 查视频模型能力

```bash
python .claude/skills/manage-project/scripts/get_video_capabilities.py --project {项目名}
```

**输出 JSON**（stdout）：`provider_id` / `model` / `supported_durations[]` / `max_duration` / `max_reference_images` / `source` / `default_duration` / `content_mode` / `generation_mode`。

**用途**：所有 generation_mode（storyboard / grid / reference_video）的预处理 subagent 在执行时自查，用于决定单片段 / shot 时长。**决策优先级**：若 `default_duration` 非 null，优先采用为默认值；否则或特殊情况（reference_video 多 shot 组合贴近 `max_duration`、narration 长句需要更长）按规则从 `supported_durations` 选值。

**错误**：项目未找到退出码 2；模型能力无法解析退出码 3；都附 stderr 说明。
