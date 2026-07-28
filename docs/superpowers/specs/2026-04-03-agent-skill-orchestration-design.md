# Agent Skill Orchestration 优化设计

## 背景

对 `agent_runtime_profile/` 的全面审查发现 11 个需要修复的问题，涉及准确性错误、架构缺陷、信息冗余和路径不一致。本设计为方案 B（引入 Asset Generation Agent + 信息去重）。

## 问题清单

| # | 严重度 | 问题 | 位置 |
|---|--------|------|------|
| 1 | P0 | Agent 名称引用错误：`novel-to-narration-script` / `novel-to-storyboard-script` 应为 `split-narration-segments` / `normalize-drama-script` | `CLAUDE.md:51` |
| 2 | P0 | 幽灵 skill 权限：`edit-script-items` 在 settings.json 中有 allow 规则但 skill 不存在 | `settings.json:29` |
| 3 | P0 | `add_characters_clues.py` 调用方式不一致：agent 定义中多了一个不存在的 `{项目名}` 位置参数 | `analyze-characters-clues.md:60` |
| 4 | P1 | 阶段 5-8 说 "dispatch general-purpose subagent" 但没有对应的 agent 定义 | `manga-workflow/SKILL.md:134` |
| 6 | P1 | Persona Prompt 与 CLAUDE.md 信息重叠（语言规范、编排模式两处定义） | `session_manager.py:316` vs `CLAUDE.md` |
| 7 | P1 | 阶段 5（角色设计）和阶段 6（线索设计）互相独立，可并行 dispatch 但当前串行 | `manga-workflow/SKILL.md` |
| 8 | P2 | 内容模式表在 CLAUDE.md、references/content-modes.md、session_manager.py 三处重复 | 多处 |
| 9 | P2 | 脚本调用路径假设不一致：部分用 `cd projects/{name} && python ../../.claude/skills/...`，部分用 `python .claude/skills/...` | 多个 SKILL.md |
| 10 | P2 | `--segment-ids` 和 `--scene-ids` 在 generate-storyboard SKILL.md 中同时出现，未说明是别名关系 | `generate-storyboard/SKILL.md:23-24` |
| 11 | P2 | reference 路径引用有误：写 `references/content-modes.md` 但实际位于 `.claude/references/content-modes.md` | 多个 SKILL.md |

## 设计方案

### 1. 新建 `agents/generate-assets.md`

创建统一资产生成 subagent，替代模糊的 "general-purpose subagent"。

**设计理念**：controller 精确构造任务清单，subagent 聚焦执行并返回结构化状态（借鉴 subagent-driven-development 模式）。

**Agent 定义结构**：

```yaml
name: generate-assets
description: "统一资产生成 subagent。接收任务清单（包含资产类型、脚本命令、验证方式），按序执行生成脚本，返回结构化摘要。用于角色/场景/道具设计、分镜图、视频生成。"
```

**工作流程**：
1. 读取 `{项目路径}/project.json` 了解项目状态
2. 按主 agent 提供的脚本命令逐条执行
3. 单条失败记录错误继续后续，不阻断整体
4. 按主 agent 指定的验证方式检查结果
5. 返回结构化状态

**返回状态协议**：
- **DONE**：全部成功
- **DONE_WITH_CONCERNS**：全部完成但有异常
- **PARTIAL**：部分成功，部分失败
- **BLOCKED**：无法执行（前置条件不满足）

**返回摘要格式**：

```markdown
## 资产生成完成

**状态**: DONE / PARTIAL / BLOCKED
**任务类型**: {类型}

| 项目 | 状态 | 备注 |
|------|------|------|
| {项1} | ✅ 成功 | |
| {项2} | ❌ 失败 | {错误原因} |
```

**核心约束**：
- 不做主 agent 未要求的额外操作
- 不等待用户确认，完成即返回
- 任务类型仅限：`character` / `scene` / `prop` / `storyboard` / `video`

> 注：本 spec 撰写时资产模型为 character + clue（线索）两类；后续「全局资产库」重构把资产统一为 character / scene / prop 三类（见 `lib/asset_types.ASSET_SPECS`），`generate-assets` 的任务类型与下方各阶段随之改为该三类资产。`analyze-characters-clues` agent 亦更名为 `analyze-assets`。

### 2. 重写 manga-workflow 阶段 5-8

#### 工作流阶段总览（修正后）

1. 项目设置
2. 全局角色/场景/道具提取 → dispatch `analyze-assets`
3. 分集规划 → 主 agent 直接执行
4. 单集预处理 → dispatch `split-narration-segments`（narration）或 `normalize-drama-script`（drama）
5. JSON 剧本生成 → dispatch `create-episode-script`
6. 角色/场景/道具设计 → dispatch `generate-assets`（一次覆盖三类，缺哪类生成哪类）
7. 分镜图生成 → dispatch `generate-assets`
8. 视频生成 → dispatch `generate-assets`

> 原阶段 9（合成）已移除。视频生成完成后用户在 Web 端导出为剪映草稿。

#### 阶段 6：角色/场景/道具设计

三类资产由统一的 `generate-assets` skill 处理：`--type` 省略时自动扫描所有 pending（缺对应
`*_sheet` 的资产）并按 `character → scene → prop` 顺序分别生成。

**触发**：有任一 character/scene/prop 缺少对应的 `*_sheet`。

```
dispatch `generate-assets` subagent：
  任务类型：character / scene / prop（缺哪类生成哪类）
  项目名称：{project_name}
  项目路径：projects/{project_name}/
  待生成项：{缺失资产名列表}
  执行方式：调用 generate-assets skill（进程内 MCP 工具 generate_assets）。
            不传 type 时自动扫描所有 pending；或按 --type=character|scene|prop 指定单类
  验证方式：重新读取 project.json，检查对应资产的 character_sheet / scene_sheet / prop_sheet 字段
```

subagent 返回后，将摘要展示给用户，进入阶段间确认。

#### 阶段 7：分镜图生成

```
dispatch `generate-assets` subagent：
  任务类型：storyboard
  项目名称：{project_name}
  项目路径：projects/{project_name}/
  脚本命令：
    python .claude/skills/generate-storyboard/scripts/generate_storyboard.py episode_{N}.json
  验证方式：重新读取 scripts/episode_{N}.json，检查各场景的 storyboard_image 字段
```

#### 阶段 8：视频生成

```
dispatch `generate-assets` subagent：
  任务类型：video
  项目名称：{project_name}
  项目路径：projects/{project_name}/
  脚本命令：
    python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --episode {N}
  验证方式：重新读取 scripts/episode_{N}.json，检查各场景的 video_clip 字段
```

#### 状态检测更新

修正后的状态检测清单（阶段重新编号）：

1. characters/scenes/props 均为空？ → **阶段 2**（角色/场景/道具提取）
2. 目标集 `source/episode_{N}.txt` 不存在？ → **阶段 3**（分集规划）
3. 目标集 drafts/ 中间文件不存在？ → **阶段 4**（预处理）
4. `scripts/episode_{N}.json` 不存在？ → **阶段 5**（JSON 剧本）
5. 有 character/scene/prop 缺少对应 `*_sheet`？ → **阶段 6**（角色/场景/道具设计）
6. 有场景缺少分镜图？ → **阶段 7**（分镜图）
7. 有场景缺少视频？ → **阶段 8**（视频）
8. 全部完成 → 工作流结束，引导用户在 Web 端导出剪映草稿

### 3. 信息去重

#### CLAUDE.md 改动

**删除**第 40-51 行的内容模式对比表（含错误 agent 名称），替换为一行引用：

```markdown
> 内容模式详细规格见 `.claude/references/generation-modes.md`。
```

> 注：当时该 reference 文件名为 `content-modes.md`，后续重构改名为 `generation-modes.md`；同时 `CLAUDE.md` 按 content_mode 拆为 `CLAUDE.narration.md` / `CLAUDE.drama.md` 两份变体（见 `2026-05-16-dynamic-agent-profile-design.md`），下文对单一 `CLAUDE.md` 的引用均落到对应变体。

**修正**架构图（约第 77-87 行）：
- 删除 `general-purpose subagent` 行
- 新增 `generate-assets` 行
- 删除旧的错误 agent 名称引用

修正后：

```
主 Agent（编排层 — 极轻量）
  │  只持有：项目状态摘要 + 用户对话历史
  │  职责：状态检测、流程决策、用户确认、dispatch subagent
  │
  ├─ dispatch → analyze-assets               全局角色/场景/道具提取
  ├─ dispatch → split-narration-segments     说书模式片段拆分
  ├─ dispatch → normalize-drama-script       剧集模式规范化剧本
  ├─ dispatch → create-episode-script        JSON 剧本生成（预加载 generate-script skill）
  └─ dispatch → generate-assets              资产生成（角色/场景/道具/分镜/视频）
```

**修正**可用 Skills 表：删除 compose-video 行。

**修正**工作流程概览：
- 删除原阶段 9（合成）和阶段 10
- 阶段 6 标注由统一 `generate-assets` 一次覆盖 character/scene/prop 三类
- 末尾说明视频生成后在 Web 端导出剪映草稿

#### Persona Prompt（session_manager.py）精简

删除与 CLAUDE.md 重复的内容，保留仅 Persona Prompt 独有的职责：

```python
_PERSONA_PROMPT = """\
## 身份

你是 ArcReel 智能体，一个专业的 AI 视频内容创作助手。你的职责是将小说转化为可发布的短视频内容。

## 行为准则

- 主动引导用户完成视频创作工作流，而不仅仅被动回答问题
- 遇到不确定的创作决策时，向用户提出选项并给出建议，而不是自行决定
- 涉及多步骤任务时，使用 TodoWrite 跟踪进度并向用户汇报
- 你是用户的视频制作搭档，专业、友善、高效"""
```

删除的内容：
- "回答用户必须使用中文"（CLAUDE.md 重要总则已有）
- "编排模式"整段（CLAUDE.md 架构章节已有）
- "使用 /manga-workflow skill 中的决策树"（CLAUDE.md 已有）

### 4. 路径与命名一致性

#### 统一脚本调用路径

**原则**：所有脚本调用必须使用 settings.json allow 规则允许的相对路径格式：

```bash
python .claude/skills/{skill}/scripts/{script}.py {args}
```

不使用 `cd projects/{name} && python ../../.claude/skills/...` 模式（不匹配 allow 规则）。

**需修改的文件**：

| 文件 | 改动 |
|------|------|
| `manga-workflow/SKILL.md` | 阶段 2 的 peek/split 命令去掉 `cd` 前缀 |
| `analyze-characters-clues.md` | 删除错误的 `{项目名}` 位置参数，使用 `python .claude/skills/manage-project/scripts/add_characters_clues.py --characters ...` |
| `normalize-drama-script.md` | 脚本调用去掉 `cd` 前缀 |
| `create-episode-script.md` | 脚本调用去掉 `cd` 前缀 |
| `generate-assets.md`（新建） | dispatch prompt 模板直接使用相对路径 |

#### 修正 `--segment-ids` / `--scene-ids` 歧义

在 `generate-storyboard/SKILL.md` 中添加说明：

```markdown
> `--scene-ids` 和 `--segment-ids` 是同义别名（后者为 narration 模式的习惯称呼），效果相同。以下统一使用 `--scene-ids`。
```

示例部分统一使用 `--scene-ids`。

#### 修正 reference 路径

各 SKILL.md 中引用 `references/content-modes.md` 改为完整的相对路径 `.claude/references/content-modes.md`（该文件后续更名为 `generation-modes.md`）。

涉及文件：
- `manga-workflow/SKILL.md:17`
- `generate-characters/SKILL.md`（引用了 prompt 语言章节）
- `generate-clues/SKILL.md`
- `generate-storyboard/SKILL.md`
- `generate-video/SKILL.md`

### 5. 清理 settings.json

删除 `settings.json:29` 的幽灵 skill 行：

```diff
- "Bash(python .claude/skills/edit-script-items/scripts/edit_script_items.py:*)",
```

## 变更文件汇总

| 操作 | 文件 | 对应问题 |
|------|------|---------|
| **新建** | `.claude/agents/generate-assets.md` | #4, #7 |
| **修改** | `CLAUDE.md` | #1, #8 |
| **修改** | `.claude/skills/manga-workflow/SKILL.md` | #4, #7, #8, #9, #11 |
| **修改** | `.claude/agents/analyze-characters-clues.md` | #3, #9 |
| **修改** | `.claude/agents/create-episode-script.md` | #9 |
| **修改** | `.claude/agents/normalize-drama-script.md` | #9 |
| **修改** | `.claude/skills/generate-storyboard/SKILL.md` | #10, #11 |
| **修改** | `.claude/skills/generate-characters/SKILL.md` | #11 |
| **修改** | `.claude/skills/generate-clues/SKILL.md` | #11 |
| **修改** | `.claude/skills/generate-video/SKILL.md` | #11 |
| **修改** | `.claude/settings.json` | #2 |
| **修改** | `server/agent_runtime/session_manager.py` | #6 |

## 不在范围内

- 错误恢复协议（#5）——用户明确排除
- Eval 覆盖度扩展（#12-15）——后续迭代
- compose-video skill 本身的删除——保留为独立 skill，仅从工作流中移除
