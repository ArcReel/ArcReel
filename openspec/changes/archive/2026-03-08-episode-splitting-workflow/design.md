## Context

### 现状

ArcReel 的剧本创建流程中，`normalize_drama_script.py` 默认读取 `source/` 下所有文件拼接后传给 Gemini。对于短篇小说这没问题，但用户上传完整长篇小说时，无法指定"本集只用第 X 到第 Y 段"。`manga-workflow` 编排中虽然预留了"本集小说范围"参数位，但没有实际的切分机制。

### 依赖关系

本 change 依赖 `refactor-script-creation-workflow` 已完成的架构——聚焦 subagent + manga-workflow 编排 skill。新增的分集规划流程嵌入 manga-workflow 的阶段 2 前置检查中。

### 相关文件

| 文件 | 作用 |
|------|------|
| `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md` | 编排 skill，需增加前置检查 |
| `agent_runtime_profile/.claude/skills/manage-project/scripts/` | 项目管理脚本目录，新脚本放这里 |
| `agent_runtime_profile/.claude/settings.json` | 权限配置 |
| `agent_runtime_profile/.claude/agents/normalize-drama-script.md` | drama 模式预处理 subagent |
| `agent_runtime_profile/.claude/agents/split-narration-segments.md` | narration 模式预处理 subagent |

## Goals / Non-Goals

**Goals:**

1. 提供 `peek_split_point.py` 脚本，展示目标字数附近的上下文供 agent 和用户决策
2. 提供 `split_episode.py` 脚本，将小说物理切分为 per-episode 文件 + 剩余文件
3. 将分集规划嵌入 manga-workflow 阶段 2 的前置检查
4. 保持现有脚本（`normalize_drama_script.py`、`generate_script.py`）不变

**Non-Goals:**

- 不实现自动分集（AI 全自动决定每集边界）——保留人工确认环节
- 不实现前端 UI 交互（拖拽标记范围等）——通过 agent 对话完成
- 不修改 project.json 数据结构（不存储 episode_plan 映射）
- 不支持逻辑映射方案（基于锚点文本的动态截取）——采用物理切分

## Decisions

### Decision 1：切分定位方式——锚点文本匹配

**选择**：`split_episode.py` 使用**锚点文本**（切分点前的 N 个字符）定位切分位置，而非数字偏移

**流程**：
```
peek 输出上下文 → agent 建议断点 → 用户确认
    ↓
split --anchor "他转身离开了。" --dry-run    ← 先 dry run 验证
    ↓
输出: "找到匹配位置，将在第 1047 字符处切分。
      前文末尾: ...月光洒在青石板路上。他转身离开了。
      后文开头: 第二章 大漠..."
    ↓
用户确认 → split --anchor "他转身离开了。"  ← 实际执行
```

**参数设计**：
- `--anchor <text>`：切分点前的文本片段（建议 10-20 个字符），脚本在原文中查找该文本，在其**末尾**处切分
- `--dry-run`：仅展示切分预览（前文末尾 + 后文开头各 50 字），不实际写文件
- 如果 anchor 在原文中匹配到多处，报错并要求用户提供更长的锚点文本

**替代方案**：
- 数字偏移 `--split-at 1047` → peek 和 split 的计数基准必须严格一致，用户无法验证位置是否正确
- 行号 → 中文小说段落长度差异大，不够精确

**理由**：锚点文本是人类可读的、可验证的。dry run 让用户在实际切分前确认位置正确。即使文件内容有细微变化（如修正了错别字），只要锚点文本仍然存在，切分位置就是正确的。

### Decision 2：物理切分 vs 逻辑映射

**选择**：物理切分（生成 `source/episode_N.txt` 文件）

**替代方案**：
- 在 project.json 中记录 `{start_marker, end_marker}` 映射，脚本运行时动态截取 → 需要改多个下游脚本，锚点匹配容易出错
- 用户手动拆分文件上传 → 用户体验差

**理由**：物理切分后，下游流程（`normalize_drama_script.py --source source/episode_N.txt`）**零改动**。文件即状态，简单可靠，可调试。

### Decision 2：字数计数规则

**选择**：含标点，不含空行

- 计数范围：所有非空行中的字符（包括中文字、标点符号、数字、英文字母）
- 排除：纯空白行（`\n`、`\r\n`、只含空格的行）
- 理由：标点是内容的一部分（影响朗读时长），空行只是格式

### Decision 3：剩余内容管理方式

**选择**：覆盖式 `_remaining.txt`

- 每次 split 后，`_remaining.txt` 被更新为剩余内容
- 原文 `novel.txt`（或用户上传的原始文件）始终保留
- 如需重新切分，可从原文重新开始

**替代方案**：
- 只记录偏移量，每次从原文动态截取 → 引入状态管理复杂度
- 不保留剩余文件，每次从原文扣除已切分部分 → 计算复杂，易出错

### Decision 4：脚本放置位置

**选择**：`agent_runtime_profile/.claude/skills/manage-project/scripts/`

**理由**：分集操作属于项目管理范畴，与已有的 `add_characters_clues.py` 同目录。不属于 `generate-script` skill（那个是生成 JSON 剧本的）。

### Decision 5：分集规划在工作流中的位置

**选择**：阶段 2 的前置检查，而非独立阶段

**理由**：
- 分集规划只在需要时触发（`source/episode_{N}.txt` 不存在时）
- 如果用户自己准备了 per-episode 文件，完全跳过
- 不增加工作流的阶段数量，保持简洁

**触发逻辑**：
```
阶段 2 开始（制作第 N 集）→
  source/episode_{N}.txt 存在？
    ├─ 是 → 直接用它做预处理
    └─ 否 → 触发单集切分：
         _remaining.txt 存在？
           ├─ 是 → peek _remaining.txt（从上次剩余内容继续）
           └─ 否 → peek 原始小说文件（首次切分）
         → agent 建议断点 → 用户确认
         → split --dry-run 验证 → split 执行
         → 生成 episode_{N}.txt + 更新 _remaining.txt
         → 继续预处理
```

**按需特性**：每次只切分当前要制作的那一集，不要求一次性规划全部集数。用户可以做完一集后隔几天再做下一集。

## Risks / Trade-offs

### [风险] 重新切分场景

用户切完 3 集后发现第 1 集切分点不好，想重做。

→ **缓解**：原文始终保留。用户可以删除 `episode_*.txt` 和 `_remaining.txt`，从头重新切分。也可以只重做某一集（手动编辑 episode 文件）。

### [Trade-off] 物理文件增多

每集一个文件 + `_remaining.txt`，source/ 目录会有较多文件。

→ **接受**：文件数量与集数成正比，可控。文件名清晰（`episode_N.txt`），不会混淆。
