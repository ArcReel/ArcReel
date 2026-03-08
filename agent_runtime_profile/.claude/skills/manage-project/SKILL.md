---
name: manage-project
description: 项目管理工具集。使用场景：(1) 分集切分——探测切分点并执行切分，(2) 批量添加角色/线索到 project.json。提供 peek（预览）+ split（执行）的渐进式切分工作流，以及角色/线索批量写入。
user-invocable: false
---

# 项目管理工具集

提供项目文件管理的命令行工具，主要用于分集切分和角色/线索批量写入。

## 工具一览

| 脚本 | 功能 | 调用者 |
|------|------|--------|
| `peek_split_point.py` | 探测目标字数附近的上下文和自然断点 | 主 agent（阶段 2） |
| `split_episode.py` | 执行分集切分，生成 episode_N.txt + _remaining.txt | 主 agent（阶段 2） |
| `add_characters_clues.py` | 批量添加角色/线索到 project.json | subagent |

## 分集切分工作流

分集切分采用 **peek → 用户确认 → split** 的渐进式流程，由主 agent 在 manga-workflow 阶段 2 直接执行。

### Step 1: 探测切分点

```bash
cd projects/{project_name} && python ../../.claude/skills/manage-project/scripts/peek_split_point.py \
  --source {源文件} --target {目标字数}
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
cd projects/{project_name} && python ../../.claude/skills/manage-project/scripts/split_episode.py \
  --source {源文件} --episode {N} --target {目标字数} --anchor "{锚点文本}" --dry-run

# 实际执行
cd projects/{project_name} && python ../../.claude/skills/manage-project/scripts/split_episode.py \
  --source {源文件} --episode {N} --target {目标字数} --anchor "{锚点文本}"
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

## 角色/线索批量写入

```bash
# 通过命令行参数
python .claude/skills/manage-project/scripts/add_characters_clues.py {project_name} \
  --characters '{"角色名": {"description": "...", "voice_style": "..."}}' \
  --clues '{"线索名": {"type": "prop", "description": "...", "importance": "major"}}'

# 通过 stdin
echo '{"characters": {...}, "clues": {...}}' | \
  python .claude/skills/manage-project/scripts/add_characters_clues.py {project_name} --stdin
```

## 字数统计规则

- 统计非空行的所有字符（包括标点）
- 空行（仅含空白字符的行）不计入
