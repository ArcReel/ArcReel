---
name: manage-project
description: 项目管理工具集。使用场景：(1) 分集切分——探测切分点并执行切分，(2) 新增/修改角色/场景/道具到 project.json（经 patch_project 工具，按 table+name upsert 或写顶层 settings 字段）。提供 peek（预览）+ split（执行）的渐进式切分工作流，以及角色/场景/道具与项目级 settings 写入。
user-invocable: false
---

# 项目管理工具集

提供项目文件管理的命令行工具，主要用于分集切分和角色/场景/道具批量写入。

## 工具一览

| 脚本 | 功能 | 调用者 |
|------|------|--------|
| `peek_split_point.py` | 探测目标字数附近的上下文和自然断点 | 主 agent（阶段 2） |
| `split_episode.py` | 执行分集切分，生成 episode_N.txt + _remaining.txt | 主 agent（阶段 2） |
| `mcp__arcreel__patch_project`（SDK tool） | 新增/修改 project.json 的角色/场景/道具（按 table+name upsert）或顶层 settings 字段 | subagent / 主 agent |
| `mcp__arcreel__get_video_capabilities`（SDK tool） | 查当前项目视频模型能力（model 粒度，所有生成模式通用） | **subagent**（执行任务时自行查询） |

## 分集切分工作流

分集切分采用 **peek → 用户确认 → split** 的渐进式流程，由主 agent 在 manga-workflow 阶段 2 直接执行。

### Step 1: 探测切分点

```bash
python .claude/skills/manage-project/scripts/peek_split_point.py --source {源文件} --target {目标字数}
```

**参数**：
- `--source`：源文件路径（`source/novel.txt` 或 `source/_remaining.txt`）
- `--target`：目标阅读单位数（按 `source_language` 解读）
- `--context`：上下文窗口大小（默认 200 字符）
- `--language`：可选，覆盖 `project.json` 的 `source_language`（zh/en/vi）

**输出**（JSON）：
- `language`：度量语言（zh/en/vi，按 `project.json` 的 `source_language` 解读，可由 `--language` 覆盖）
- `total_units`：总阅读单位（zh 数汉字 + CJK 标点，en/vi 数 word）
- `target_units`：目标阅读单位（即入参 `--target`）
- `split_target_chars`：换算后的字符级 target（**给 split_episode.py --target 用**；阅读单位与字符不等价时不能直接复用 target_units）
- `target_offset`：目标对应的原文字符偏移
- `context_before` / `context_after`：切分点前后上下文
- `nearby_breakpoints`：附近自然断点列表（按距离排序，最多 10 个）

### Step 2: 执行切分

```bash
# Dry run（仅预览）
python .claude/skills/manage-project/scripts/split_episode.py --source {源文件} --episode {N} --target {split_target_chars} --anchor "{锚点文本}" --dry-run

# 实际执行
python .claude/skills/manage-project/scripts/split_episode.py --source {源文件} --episode {N} --target {split_target_chars} --anchor "{锚点文本}"
```

**参数**：
- `--source`：源文件路径
- `--episode`：集数编号
- `--target`：目标**字符数**（必须用 peek 输出的 `split_target_chars`；不能直接复用 peek 的 `--target` 阅读单位值，否则混排 / en / vi 场景下锚点搜索可能落空）
- `--anchor`：切分点的锚点文本（10-20 字符）
- `--context`：搜索窗口大小（默认 500 字符）
- `--dry-run`：仅预览，不写文件

**定位机制**：target 字数计算大致偏移 → 在 ±window 范围内搜索 anchor → 使用距离最近的匹配

**输出文件**：
- `source/episode_{N}.txt`：前半部分
- `source/_remaining.txt`：后半部分（下一集的源文件）

## 角色/场景/道具写入

经 `mcp__arcreel__patch_project` 工具写入（项目名由 session 绑定，无需传参）。按 table 分别调用，
每个 entry 以 name 为键 upsert：name 不存在则新增、存在则合并改字段。**修订已有资产描述需用户显式
意图驱动**（避免静默覆盖人工编辑过的字段）;新增提取由 analyze-assets subagent 负责并默认 skip 已存在的。

```text
mcp__arcreel__patch_project({"table": "characters", "entries": {"角色名": {"description": "...", "voice_style": "..."}}})
mcp__arcreel__patch_project({"table": "scenes", "entries": {"场景名": {"description": "..."}}})
mcp__arcreel__patch_project({"table": "props", "entries": {"道具名": {"description": "..."}}})
mcp__arcreel__patch_project({"settings": {"episode_target_units": 1000}})
mcp__arcreel__patch_project({"settings": {"source_language": "en"}})
```

**两种调用形态二选一**：传 `{"table", "entries"}` 走资产 upsert，传 `{"settings"}` 走顶层字段写入；
两者同时给出或都不给会被拒。`settings` 白名单字段：

- `episode_target_units`：`int >= 1` 设置 / `null` 清除
- `source_language`：`"zh" / "en" / "vi"` 设置 / `null` 清除。**仅在 overview 跳过或失败、且用户明确确认语言后写入**；正常路径由 overview 生成自动落盘

工具返回会区分**新增 N 个 / 合并改字段 N 个**,并显式列出被忽略的字段（``reference_image`` /
``character_sheet`` 等系统管理字段、``type`` / ``importance`` 等已废弃字段）。结构非法（如缺
description）时不落盘并返回 `is_error: true`。
**严禁**用 Write/Edit/Bash 直接改 `project.json`——只能走 patch_project 工具。

## 字数统计规则

- peek 的 `--target` 是阅读单位：zh 数汉字 + CJK 标点，en/vi 数 word
- split 的 `--target` 是字符级（非空行字符数），不感知语言；peek 已按 total 比例换算并输出 `split_target_chars` 字段，agent 应把该值传给 split
- 空行（仅含空白字符的行）在两种度量下均不计入

## 查视频模型能力

通过 MCP 工具查询（项目名由 session 绑定，无需传参）：

```text
mcp__arcreel__get_video_capabilities({})
```

**返回**：JSON 文本，含 `provider_id` / `model` / `supported_durations[]` / `max_duration` / `max_reference_images` / `source` / `default_duration` / `content_mode` / `generation_mode`。

**用途**：所有 generation_mode（storyboard / grid / reference_video）的预处理 subagent 在执行时自查，用于决定单片段 / shot 时长。**决策优先级**：若 `default_duration` 非 null，优先采用为默认值；否则或特殊情况（reference_video 多 shot 组合贴近 `max_duration`、narration 长句需要更长）按规则从 `supported_durations` 选值。

**错误**：项目未找到或模型能力无法解析时返回 `is_error: true`，文本中包含原因。
