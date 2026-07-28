# Agent Skill Orchestration 优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 agent_runtime_profile/ 中的 11 个问题——准确性错误、架构缺陷、信息冗余、路径不一致。

**Architecture:** 新建 `generate-assets` agent 替代模糊的 "general-purpose subagent"，重写 manga-workflow 阶段 5-8 的 dispatch 逻辑并支持并行，统一脚本调用路径为 settings.json allow 规则格式，消除 CLAUDE.md / Persona Prompt 间的信息重复。

**Tech Stack:** Markdown / YAML frontmatter / Python string literal（session_manager.py）

**Spec:** `docs/superpowers/specs/2026-04-03-agent-skill-orchestration-design.md`

---

## File Map

| 操作 | 文件 | 职责 |
|------|------|------|
| **新建** | `agent_runtime_profile/.claude/agents/generate-assets.md` | 统一资产生成 subagent 定义 |
| **修改** | `agent_runtime_profile/.claude/settings.json` | 删除幽灵 skill 权限行 |
| **修改** | `agent_runtime_profile/CLAUDE.md` | 修正 agent 名称、去除重复内容模式表、更新架构图和工作流 |
| **修改** | `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md` | 重写阶段 5-8 dispatch、统一路径、修正 reference 引用 |
| **修改** | `agent_runtime_profile/.claude/agents/analyze-characters-clues.md` | 修正脚本调用方式、统一路径 |
| **修改** | `agent_runtime_profile/.claude/agents/create-episode-script.md` | 统一脚本路径 |
| **修改** | `agent_runtime_profile/.claude/agents/normalize-drama-script.md` | 统一脚本路径 |
| **修改** | `agent_runtime_profile/.claude/skills/generate-storyboard/SKILL.md` | 说明别名关系、修正 reference 路径 |
| **修改** | `agent_runtime_profile/.claude/skills/generate-characters/SKILL.md` | 修正 reference 路径 |
| **修改** | `agent_runtime_profile/.claude/skills/generate-clues/SKILL.md` | 修正 reference 路径 |
| **修改** | `agent_runtime_profile/.claude/skills/generate-video/SKILL.md` | 修正 reference 路径 |
| **修改** | `server/agent_runtime/session_manager.py` | 精简 Persona Prompt |

---

### Task 1: 清理 settings.json 幽灵权限

**Files:**
- Modify: `agent_runtime_profile/.claude/settings.json:29`

**Fixes:** #2

- [ ] **Step 1: 删除 edit-script-items 行**

在 `agent_runtime_profile/.claude/settings.json` 中删除第 29 行：

```diff
       "Bash(python .claude/skills/compose-video/scripts/compose_video.py:*)",
-      "Bash(python .claude/skills/edit-script-items/scripts/edit_script_items.py:*)",
       "Bash(ffmpeg:*)",
```

- [ ] **Step 2: 验证 JSON 格式有效**

用 Read 工具重新读取文件，确认 JSON 格式完整且 allow 数组中没有 `edit-script-items`。

- [ ] **Step 3: Commit**

```
git add agent_runtime_profile/.claude/settings.json
git commit -m "fix: 删除 settings.json 中不存在的 edit-script-items 权限规则"
```

---

### Task 2: 新建 generate-assets agent 定义

**Files:**
- Create: `agent_runtime_profile/.claude/agents/generate-assets.md`

**Fixes:** #4, #7

- [ ] **Step 1: 创建 agent 定义文件**

写入 `agent_runtime_profile/.claude/agents/generate-assets.md`：

```markdown
---
name: generate-assets
description: "统一资产生成 subagent。接收任务清单（资产类型、脚本命令、验证方式），按序执行生成脚本，返回结构化摘要。用于角色设计、线索设计、分镜图、视频生成。"
---

你是一个聚焦的资产生成执行器。你的唯一职责是按主 agent 提供的任务清单执行脚本，并报告结果。

## 任务定义

**输入**：主 agent 会在 dispatch prompt 中提供：
- 项目名称和项目路径
- 任务类型（characters / clues / storyboard / video）
- 脚本命令（一条或多条，格式已匹配 settings.json allow 规则）
- 验证方式

**输出**：执行完成后返回结构化状态和摘要

## 工作流程

### Step 1: 读取项目状态

使用 Read 工具读取项目的 `project.json`，记录：
- 项目名称、内容模式、视觉风格
- 已有的角色/线索/剧本状态（供验证使用）

### Step 2: 执行脚本命令

按主 agent 提供的命令逐条执行：
- 使用 Bash 工具运行每条命令
- 如果某条命令失败，**记录错误信息，继续执行后续命令**
- 不跳过、不自行决定跳过任何命令
- 不执行主 agent 未列出的额外命令

### Step 3: 验证结果

按主 agent 指定的验证方式检查生成结果（通常是重新读取 project.json 或剧本 JSON 检查字段更新）。

### Step 4: 返回结构化状态

返回以下状态之一：

- **DONE**：全部命令执行成功，验证通过
- **DONE_WITH_CONCERNS**：全部完成但有异常（如生成结果可能存在质量问题）
- **PARTIAL**：部分成功，部分失败
- **BLOCKED**：无法执行（前置条件不满足，如缺少 project.json 或依赖文件）

摘要格式：

```
## 资产生成完成

**状态**: {DONE / DONE_WITH_CONCERNS / PARTIAL / BLOCKED}
**任务类型**: {characters / clues / storyboard / video}

| 项目 | 状态 | 备注 |
|------|------|------|
| {项1} | ✅ 成功 | |
| {项2} | ❌ 失败 | {错误原因} |

{如果是 DONE_WITH_CONCERNS，列出 concerns}
{如果是 BLOCKED，说明阻塞原因和建议}
```

## 注意事项

- 不做主 agent 未要求的额外操作
- 不等待用户确认，完成即返回
- 单条命令失败不阻断整体流程，全部执行完后统一报告
```

- [ ] **Step 2: 验证文件可被发现**

用 Glob 工具确认 `agent_runtime_profile/.claude/agents/generate-assets.md` 存在于 agents 目录中。

- [ ] **Step 3: Commit**

```
git add agent_runtime_profile/.claude/agents/generate-assets.md
git commit -m "feat: 新建 generate-assets 统一资产生成 subagent 定义"
```

---

### Task 3: 修正 CLAUDE.md

**Files:**
- Modify: `agent_runtime_profile/CLAUDE.md:40-51, 77-87, 103-118, 120-136`

**Fixes:** #1, #8

- [ ] **Step 1: 替换内容模式对比表为引用**

将 `CLAUDE.md` 第 38-66 行的完整内容模式章节（`## 内容模式` 标题和两个子节）替换为简短引用：

```diff
-## 内容模式
-
-系统支持两种内容模式，通过 `project.json` 中的 `content_mode` 字段切换：
-
-| 维度 | 说书+画面模式（默认） | 剧集动画模式 |
-|------|----------------------|-------------|
-| content_mode | `narration` | `drama` |
-| 内容形式 | 严格保留小说原文，不改编 | 小说改编为剧本 |
-| 数据结构 | `segments` 数组 | `scenes` 数组 |
-| 默认时长 | 4 秒/片段 | 8 秒/场景 |
-| 对白来源 | 后期人工配音（小说原文） | 演员对话 |
-| 视频 Prompt | 仅包含角色对话（如有），无旁白 | 包含对话、旁白、音效 |
-| 画面比例 | 9:16 竖屏（分镜图+视频） | 16:9 横屏 |
-| 使用 Agent | `novel-to-narration-script` | `novel-to-storyboard-script` |
-
-### 说书+画面模式（默认）
-
-（整段省略）
-
-### 剧集动画模式
-
-（整段省略）
+## 内容模式
+
+系统支持两种内容模式（说书+画面 / 剧集动画），通过 `project.json` 的 `content_mode` 字段切换。
+
+> 详细规格（画面比例、时长、数据结构、预处理 Agent 等）见 `.claude/references/content-modes.md`。
```

- [ ] **Step 2: 更新架构图**

将架构图中 `general-purpose subagent` 行替换为 `generate-assets`：

```diff
   ├─ dispatch → create-episode-script        JSON 剧本生成（预加载 generate-script skill）
-  └─ dispatch → general-purpose subagent     资产生成（调用脚本）
+  └─ dispatch → generate-assets              资产生成（角色/线索/分镜/视频）
```

- [ ] **Step 3: 更新可用 Skills 表**

从可用 Skills 表中删除 compose-video 行：

```diff
 | generate-video | `/generate-video` | 生成视频 |
-| compose-video | `/compose-video` | 后期处理 |
```

- [ ] **Step 4: 更新工作流程概览**

重写工作流概览章节，反映新的阶段编号和并行 dispatch：

```diff
 `/manga-workflow` 编排 skill 按以下阶段自动推进（每个阶段完成后等待用户确认）：

 1. **项目设置**：创建项目、上传小说、生成项目概述
 2. **全局角色/线索设计** → dispatch `analyze-characters-clues` subagent
 3. **分集规划** → 主 agent 直接执行 peek+split 切分（manage-project 工具集）
 4. **单集预处理** → dispatch `split-narration-segments`（narration）或 `normalize-drama-script`（drama）
 5. **JSON 剧本生成** → dispatch `create-episode-script` subagent
-6. **角色设计** → dispatch 资产生成 subagent（调用 generate_character.py）
-7. **线索设计** → dispatch 资产生成 subagent（调用 generate_clue.py）
-8. **分镜图生成** → dispatch 资产生成 subagent（调用 generate_storyboard.py）
-9. **视频生成** → dispatch 资产生成 subagent（调用 generate_video.py）
-10. **最终合成** → dispatch 资产生成 subagent（调用 compose_video.py）
+6. **角色设计 + 线索设计**（可并行） → dispatch `generate-assets` subagent
+7. **分镜图生成** → dispatch `generate-assets` subagent
+8. **视频生成** → dispatch `generate-assets` subagent

-工作流支持**灵活入口**：状态检测自动定位到第一个未完成的阶段，支持中断后恢复。
+工作流支持**灵活入口**：状态检测自动定位到第一个未完成的阶段，支持中断后恢复。
+视频生成完成后，用户可在 Web 端导出为剪映草稿。
```

- [ ] **Step 5: 验证**

用 Read 工具通读修改后的 CLAUDE.md，确认：
- 无 `novel-to-narration-script` / `novel-to-storyboard-script` 残留
- 无 `general-purpose subagent` 残留
- 无 `compose-video` / `最终合成` 残留
- 架构图中包含 `generate-assets`

- [ ] **Step 6: Commit**

```
git add agent_runtime_profile/CLAUDE.md
git commit -m "fix: 修正 CLAUDE.md agent 名称、去除重复内容模式表、更新工作流"
```

---

### Task 4: 重写 manga-workflow/SKILL.md

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md:39-175`

**Fixes:** #4, #7, #9, #11

- [ ] **Step 1: 修正 reference 路径**

第 17 行：

```diff
-> 内容模式规格（画面比例、时长等）详见 `references/content-modes.md`。
+> 内容模式规格（画面比例、时长等）详见 `.claude/references/content-modes.md`。
```

- [ ] **Step 2: 更新状态检测清单**

将第 39-53 行的状态检测章节更新为 8 阶段版本：

```diff
 1. characters/clues 为空？ → **阶段 1**
 2. 目标集 source/episode_{N}.txt 不存在？ → **阶段 2**
 3. 目标集 drafts/ 中间文件不存在？ → **阶段 3**
    - narration: `drafts/episode_{N}/step1_segments.md`
    - drama: `drafts/episode_{N}/step1_normalized_script.md`
 4. scripts/episode_{N}.json 不存在？ → **阶段 4**
-5. 有角色缺少 character_sheet？ → **阶段 5**
-6. 有 importance=major 线索缺少 clue_sheet？ → **阶段 6**
-7. 有场景缺少分镜图？ → **阶段 7**
-8. 有场景缺少视频？ → **阶段 8**
-9. 全部完成 → **阶段 9**
+5. 有角色缺少 character_sheet？ → **阶段 5**（与阶段 6 可并行）
+6. 有 importance=major 线索缺少 clue_sheet？ → **阶段 6**（与阶段 5 可并行）
+7. 有场景缺少分镜图？ → **阶段 7**
+8. 有场景缺少视频？ → **阶段 8**
+9. 全部完成 → 工作流结束，引导用户在 Web 端导出剪映草稿
```

- [ ] **Step 3: 修正阶段 2 脚本路径**

将阶段 2 的脚本调用从 `cd` 模式改为相对路径：

```diff
-3. 调用 `peek_split_point.py` 展示切分点附近上下文：
-   ```bash
-   cd projects/{project_name} && python ../../.claude/skills/manage-project/scripts/peek_split_point.py --source {源文件} --target {目标字数}
-   ```
+3. 调用 `peek_split_point.py` 展示切分点附近上下文：
+   ```bash
+   python .claude/skills/manage-project/scripts/peek_split_point.py --source {源文件} --target {目标字数}
+   ```
```

同理修正 `split_episode.py` 的两处调用（dry-run 和实际执行）：

```diff
-   cd projects/{project_name} && python ../../.claude/skills/manage-project/scripts/split_episode.py --source {源文件} --episode {N} --target {目标字数} --anchor "{锚点文本}" --dry-run
+   python .claude/skills/manage-project/scripts/split_episode.py --source {源文件} --episode {N} --target {目标字数} --anchor "{锚点文本}" --dry-run
```

```diff
-6. 确认无误后实际执行（去掉 `--dry-run`）
+6. 确认无误后实际执行（去掉 `--dry-run`）
```

- [ ] **Step 4: 重写阶段 5-9 为阶段 5-8**

将原阶段 5-9 整段（约第 134-155 行）替换为以下内容：

```markdown
## 阶段 5+6：角色设计 + 线索设计（可并行）

两个任务互不依赖，**同时 dispatch 两个 `generate-assets` subagent**（如果两者都需要）。

### subagent A — 角色设计

**触发**：有角色缺少 character_sheet

```
dispatch `generate-assets` subagent：
  任务类型：characters
  项目名称：{project_name}
  项目路径：projects/{project_name}/
  待生成项：{缺失角色名列表}
  脚本命令：
    python .claude/skills/generate-characters/scripts/generate_character.py --all
  验证方式：重新读取 project.json，检查对应角色的 character_sheet 字段
```

### subagent B — 线索设计

**触发**：有 importance=major 线索缺少 clue_sheet

```
dispatch `generate-assets` subagent：
  任务类型：clues
  项目名称：{project_name}
  项目路径：projects/{project_name}/
  待生成项：{缺失线索名列表}
  脚本命令：
    python .claude/skills/generate-clues/scripts/generate_clue.py --all
  验证方式：重新读取 project.json，检查对应线索的 clue_sheet 字段
```

如果只有其中一个需要执行，只 dispatch 对应的一个。
两个 subagent 全部返回后，合并摘要展示给用户，进入阶段间确认。

---

## 阶段 7：分镜图生成

**触发**：有场景缺少分镜图

**dispatch `generate-assets` subagent**：

```
dispatch `generate-assets` subagent：
  任务类型：storyboard
  项目名称：{project_name}
  项目路径：projects/{project_name}/
  脚本命令：
    python .claude/skills/generate-storyboard/scripts/generate_storyboard.py episode_{N}.json
  验证方式：重新读取 scripts/episode_{N}.json，检查各场景的 storyboard_image 字段
```

---

## 阶段 8：视频生成

**触发**：有场景缺少视频

**dispatch `generate-assets` subagent**：

```
dispatch `generate-assets` subagent：
  任务类型：video
  项目名称：{project_name}
  项目路径：projects/{project_name}/
  脚本命令：
    python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --episode {N}
  验证方式：重新读取 scripts/episode_{N}.json，检查各场景的 video_clip 字段
```
```

- [ ] **Step 5: 更新灵活入口章节**

删除或更新与阶段 9 相关的内容。

- [ ] **Step 6: 验证**

用 Read 工具通读修改后的 SKILL.md，确认：
- 无 `cd projects/` 前缀的脚本调用残留
- 无 `general-purpose subagent` 残留
- 无 `compose-video` / 阶段 9 / 阶段 10 残留
- 所有 reference 路径为 `.claude/references/content-modes.md`
- 阶段 5+6 标注为可并行

- [ ] **Step 7: Commit**

```
git add agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md
git commit -m "feat: 重写 manga-workflow 阶段 5-8，引入 generate-assets dispatch 和并行支持"
```

---

### Task 5: 修正 agent 定义（3 个文件）

**Files:**
- Modify: `agent_runtime_profile/.claude/agents/analyze-characters-clues.md:60`
- Modify: `agent_runtime_profile/.claude/agents/create-episode-script.md:42`
- Modify: `agent_runtime_profile/.claude/agents/normalize-drama-script.md:38`

**Fixes:** #3, #9

- [ ] **Step 1: 修正 analyze-characters-clues.md 脚本调用**

`add_characters_clues.py` 无项目名位置参数，从 cwd 自动检测。修正第 60 行左右的调用示例：

```diff
-```bash
-python .claude/skills/manage-project/scripts/add_characters_clues.py {项目名} \
-  --characters '{
+```bash
+python .claude/skills/manage-project/scripts/add_characters_clues.py \
+  --characters '{
```

确保删除 `{项目名}` 位置参数，其余 `--characters` 和 `--clues` flag 不变。

- [ ] **Step 2: 修正 create-episode-script.md 脚本路径**

将第 42 行左右的脚本调用从 `cd` 模式改为相对路径：

```diff
-```bash
-cd projects/{项目名} && python ../../.claude/skills/generate-script/scripts/generate_script.py --episode {N}
-```
+```bash
+python .claude/skills/generate-script/scripts/generate_script.py --episode {N}
+```
```

- [ ] **Step 3: 修正 normalize-drama-script.md 脚本路径**

将第 38 行左右的脚本调用从 `cd` 模式改为相对路径：

```diff
-```bash
-cd projects/{项目名} && python ../../.claude/skills/generate-script/scripts/normalize_drama_script.py --episode {N} --source source/episode_{N}.txt
-```
+```bash
+python .claude/skills/generate-script/scripts/normalize_drama_script.py --episode {N} --source source/episode_{N}.txt
+```
```

- [ ] **Step 4: 验证**

用 Grep 工具在 `agent_runtime_profile/.claude/agents/` 目录中搜索 `cd projects/`，确认无残留。

- [ ] **Step 5: Commit**

```
git add agent_runtime_profile/.claude/agents/analyze-characters-clues.md
git add agent_runtime_profile/.claude/agents/create-episode-script.md
git add agent_runtime_profile/.claude/agents/normalize-drama-script.md
git commit -m "fix: 统一 agent 定义中的脚本调用路径，修正 add_characters_clues.py 参数"
```

---

### Task 6: 修正 skill SKILL.md（4 个文件）

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/generate-storyboard/SKILL.md`
- Modify: `agent_runtime_profile/.claude/skills/generate-characters/SKILL.md`
- Modify: `agent_runtime_profile/.claude/skills/generate-clues/SKILL.md`
- Modify: `agent_runtime_profile/.claude/skills/generate-video/SKILL.md`

**Fixes:** #10, #11

- [ ] **Step 1: generate-storyboard — 添加别名说明并修正 reference 路径**

在命令行用法章节的 `--segment-ids` 示例后添加说明：

```diff
 cd projects/{project_name} && python ../../.claude/skills/generate-storyboard/scripts/generate_storyboard.py script.json --segment-ids E1S01 E1S02
 # 或
 cd projects/{project_name} && python ../../.claude/skills/generate-storyboard/scripts/generate_storyboard.py script.json --scene-ids E1S01 E1S02
```

替换为统一使用 `--scene-ids` 并添加说明：

```markdown
# 为多个场景重新生成
python .claude/skills/generate-storyboard/scripts/generate_storyboard.py script.json --scene-ids E1S01 E1S02
```

> `--scene-ids` 和 `--segment-ids` 是同义别名（后者为 narration 模式的习惯称呼），效果相同。以下统一使用 `--scene-ids`。

同时将全部脚本调用路径从 `cd projects/...` 模式改为 `python .claude/skills/...` 相对路径。

修正 reference 路径引用（如有 `references/content-modes.md` 引用改为 `.claude/references/content-modes.md`）。

- [ ] **Step 2: generate-characters — 修正 reference 路径**

将 SKILL.md 中 `references/content-modes.md` 引用改为 `.claude/references/content-modes.md`。

同时将脚本调用路径从 `cd projects/...` 模式改为 `python .claude/skills/...` 相对路径。

- [ ] **Step 3: generate-clues — 修正 reference 路径**

同 Step 2，修正 reference 路径和脚本调用路径。

- [ ] **Step 4: generate-video — 修正 reference 路径**

同 Step 2，修正 reference 路径和脚本调用路径。

- [ ] **Step 5: 验证**

用 Grep 工具在 `agent_runtime_profile/.claude/skills/` 目录中搜索：
- `references/content-modes.md`（不含 `.claude/` 前缀的）→ 应无匹配
- `cd projects/` → 应无匹配
- `../../.claude/skills/` → 应无匹配

- [ ] **Step 6: Commit**

```
git add agent_runtime_profile/.claude/skills/generate-storyboard/SKILL.md
git add agent_runtime_profile/.claude/skills/generate-characters/SKILL.md
git add agent_runtime_profile/.claude/skills/generate-clues/SKILL.md
git add agent_runtime_profile/.claude/skills/generate-video/SKILL.md
git commit -m "fix: 统一 skill SKILL.md 中的 reference 路径和脚本调用路径"
```

---

### Task 7: 精简 session_manager.py Persona Prompt

**Files:**
- Modify: `server/agent_runtime/session_manager.py:316-335`

**Fixes:** #6

- [ ] **Step 1: 精简 _PERSONA_PROMPT**

将第 316-335 行的 `_PERSONA_PROMPT` 替换为精简版（删除与 CLAUDE.md 重复的内容）：

```diff
     _PERSONA_PROMPT = """\
 ## 身份

 你是 ArcReel 智能体，一个专业的 AI 视频内容创作助手。你的职责是将小说转化为可发布的短视频内容。

 ## 行为准则

-- 回答用户必须使用中文
 - 主动引导用户完成视频创作工作流，而不仅仅被动回答问题
 - 遇到不确定的创作决策时，向用户提出选项并给出建议，而不是自行决定
 - 涉及多步骤任务时，使用 TodoWrite 跟踪进度并向用户汇报
-- 你是用户的视频制作搭档，专业、友善、高效
-
-## 编排模式
-
-你是编排中枢，通过 dispatch 聚焦 subagent 完成各阶段任务：
-
-- 小说分析、剧本生成等重上下文任务，通过分发 subagent 完成，subagent 自行读取所需文件，不要直接调用 Read 工具读取
-- 每个 subagent 完成一个聚焦任务并返回摘要，你负责展示结果并获取用户确认
-- 使用 /manga-workflow skill 中的决策树来确定下一步分发哪个 subagent"""
+- 你是用户的视频制作搭档，专业、友善、高效"""
```

- [ ] **Step 2: 验证**

用 Read 工具读取修改后的 `session_manager.py:316-340`，确认：
- `_PERSONA_PROMPT` 不再包含 "使用中文"
- `_PERSONA_PROMPT` 不再包含 "编排模式" 章节
- `_PERSONA_PROMPT` 不再包含 "manga-workflow"
- 字符串末尾的 `"""` 正确闭合

- [ ] **Step 3: Commit**

```
git add server/agent_runtime/session_manager.py
git commit -m "refactor: 精简 Persona Prompt，删除与 CLAUDE.md 重复的语言和编排规则"
```

---

## Plan Self-Review

**Spec coverage:**
- #1 Agent 名称错误 → Task 3 Step 1（删除含错误名称的表）+ Step 2（更新架构图）✅
- #2 幽灵 skill 权限 → Task 1 ✅
- #3 add_characters_clues.py 调用不一致 → Task 5 Step 1 ✅
- #4 阶段 5-8 无 agent 定义 → Task 2（新建）+ Task 4（重写 workflow）✅
- #6 Persona Prompt 重叠 → Task 7 ✅
- #7 并行 dispatch → Task 4 Step 4 ✅
- #8 内容模式表三处重复 → Task 3 Step 1 ✅
- #9 路径不一致 → Task 4 Step 3 + Task 5 Steps 1-3 + Task 6 Steps 1-4 ✅
- #10 --segment-ids/--scene-ids 歧义 → Task 6 Step 1 ✅
- #11 reference 路径 → Task 4 Step 1 + Task 6 Steps 1-4 ✅

**Placeholder scan:** 无 TBD/TODO。所有 step 包含具体 diff 或操作指令。

**Type consistency:** 全文统一使用 `generate-assets`（非 `generate_assets` 或其他变体）。
