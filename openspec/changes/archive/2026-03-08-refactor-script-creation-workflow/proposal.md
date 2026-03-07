## Why

当前剧本创建体系有三个结构性问题：

**1. 作用域错配——全局任务被锁死在单集流程里**

角色/线索设计本质上是**全剧维度**的操作（分析整部小说、建立跨集复用的角色体系），但被嵌入在 per-episode 的 subagent 工作流中（narration Step 2、drama Step 3）。后果：
- 第一集创建时注册角色，后续集数靠"已存在自动跳过"——这是隐式副作用而非显式的全局设计
- 用户无法先做一次完整的角色/线索规划再逐集创建剧本
- 如果用户只想处理小说的某一章，仍被迫走完整流程

**2. Subagent 内部塞了多步交互确认——违反了 subagent 的使用模式**

Subagent 的核心价值是**保护主 agent 的上下文空间**：把大量原始素材（整部小说）的处理和 skill 调用都卸载到 subagent 中，主 agent 只接收精炼的结果。但当前两个 subagent 内部包含 3-4 个需要用户确认的步骤，导致：
- Subagent 长时间占据执行状态，中间产物（step1、step2...）堆积在 subagent context 中
- 用户确认通过 AskUserQuestion 在 subagent 内部完成，但用户需要的审核能力（查看项目文件、对比修改）在主 agent 更自然
- 一旦 subagent 上下文接近窗口限制或出错，整个多步流程需要从头开始

正确的模式：**每个 subagent 接受一个聚焦任务、独立完成（可以内部调用 skill/脚本）、返回结果**。多步之间的确认和编排由主 agent 在 subagent 之间完成。

**3. 编排层缺失——skill 和 agent 职责划分不合理**

- `manga-workflow` 本应是编排中枢，但只是一份静态 Markdown 文档
- 两个 agent 把编排（步骤控制）、推理（文本分析）、执行（调用 generate-script skill）混在一起
- 没有清晰的 skill/agent 边界原则：什么该用 subagent（需要推理+保护主 context）、什么该在主 agent 直接调用

## What Changes

遵循 subagent-driven-development 设计哲学——**每个 subagent 一个聚焦任务、subagent 内部可调用 skill、主 agent 只做编排和用户确认**——重塑整个 skill/agent 体系。

### 架构分层原则

```
主 Agent（编排层 — 极轻量）
  │  只持有：项目状态摘要 + 用户对话历史
  │  职责：状态检测、流程决策、用户确认、dispatch subagent
  │
  ├─ dispatch via Agent tool ──→  Subagent（执行层 — 聚焦任务）
  │                                 持有：任务所需的原始素材（小说原文等）
  │                                 职责：推理分析 + 调用 skill/脚本
  │                                 │
  │                                 ├─ 预加载 skills（via frontmatter `skills` 字段）
  │                                 ├─ invoke Skill tool / Bash ──→  脚本执行
  │                                 │    generate-script, generate-characters...
  │                                 │    确定性操作，调 API / 跑 ffmpeg
  │                                 │
  │                                 ├─ ⚠️ 不能再 spawn 子 subagent（SDK 约束）
  │                                 │
  │                                 └─ 返回精炼结果给主 Agent
  │
  └─ 接收结果摘要，展示给用户，获取确认
```

**关键约束**（来自 Claude Code 官方文档）：
- Subagent **不能** spawn 其他 subagent——只有主 agent 能 dispatch subagent
- Skill 可通过 `skills` 字段**预加载**到 subagent 中（内容直接注入 subagent context）
- Skill 也可通过 `context: fork` 机制在 subagent 中运行
- Skill 由 subagent 调用而非主 agent——skill 执行的大量 prompt/日志留在 subagent context 中，主 agent 只看到摘要

### 核心变更

- **拆解两个大 agent 为多个聚焦 subagent 模板**（`agents/` 目录）：
  - `analyze-characters-clues` — 全局角色/线索提取（分析整部小说或指定范围），内部调用 ProjectManager 写入 project.json
  - `split-narration-segments` — 说书模式片段拆分（per-episode），返回 step1 中间文件
  - `normalize-drama-script` — 剧集模式规范化 + 镜头预算（per-episode），返回 step1+step2 中间文件
  - `create-episode-script` — 统一的 JSON 剧本生成（per-episode），内部调用 generate-script skill，返回生成结果
  - 每个模板定义清晰的**输入/输出契约**：接收什么、返回什么、内部调用什么
  - **BREAKING**：删除 `novel-to-narration-script.md` 和 `novel-to-storyboard-script.md`

- **升级 manga-workflow 为真正的编排 skill**：
  - 具备状态检测能力（读取 project.json + 检查文件系统）
  - 定义清晰的阶段流转和 dispatch 策略
  - 每个 subagent 返回后，主 agent 审核摘要、展示给用户、获取确认、再 dispatch
  - 支持灵活入口：可只做全局角色设计、可只做单集剧本、可从任意阶段继续
  - 后续资产生成阶段（generate-characters/storyboard/video）也通过 dispatch subagent 执行，而非主 agent 直接调用 skill

- **建立 skill/agent 边界原则**：
  - **Subagent（Task）**= 需要大量上下文或推理的任务 → 保护主 agent context
  - **Skill（在 subagent 内部调用）**= 确定性脚本执行 → API 调用、文件生成
  - **主 Agent 直接调用**= 仅限轻量操作（读项目状态、简单文件操作）

### 新的工作流时序

```
主 Agent (编排 — 极轻量)              Subagents (聚焦执行)
───────────────────────              ─────────────────────

[阶段 0] 检测项目状态
├─ 读 project.json 摘要
├─ 判断缺什么
└─ 决定入口阶段

[阶段 1: 全局角色/线索设计]
dispatch → ──────────────── → analyze-characters-clues
  传入：小说原文 + 已有角色        分析整部小说
                                   提取角色表 + 线索表
                                   调用 ProjectManager 写入 project.json
                                   返回：角色/线索摘要
← ────────────────────────── ←
展示摘要，用户确认 ✓

[阶段 2: 单集预处理]
dispatch → ──────────────── → split-narration-segments
  传入：本集小说文本                 (或 normalize-drama-script)
  传入：角色/线索名称列表             拆分/规范化 + 镜头预算
                                   保存 drafts/ 中间文件
                                   返回：片段/场景摘要
← ────────────────────────── ←
展示摘要，用户确认 ✓

[阶段 3: JSON 剧本生成]
dispatch → ──────────────── → create-episode-script
  传入：集数 + 模式参数              内部调用 generate-script skill
                                   验证输出
                                   返回：生成结果摘要
← ────────────────────────── ←
展示结果，用户确认 ✓

[阶段 4+: 资产生成]
dispatch → ──────────────── → subagent 调用 /generate-characters
dispatch → ──────────────── → subagent 调用 /generate-clues
dispatch → ──────────────── → subagent 调用 /generate-storyboard
dispatch → ──────────────── → subagent 调用 /generate-video
  每个 subagent 内部调用对应 skill
  返回摘要给主 agent
```

### 关键设计决策

1. **上下文隔离**：小说原文只进入 subagent context，主 agent 永远不加载原始小说。subagent 返回精炼的摘要（表格、统计、状态），保护主 agent 上下文空间。

2. **全局设计与单集创建解耦**：角色/线索提取是独立阶段，可单独执行。用户可以先做一次全书角色规划，再逐集创建剧本。也支持增量模式——新集发现新角色时追加。

3. **Skill 调用下沉到 subagent**：生成类 skill（generate-characters、generate-storyboard 等）由 subagent 调用而非主 agent。skill 执行产生的大量 prompt 文本和生成日志留在 subagent context 中，主 agent 只收到 "已生成 N 个角色设计图" 这样的摘要。

4. **两种模式共享全局步骤**：角色/线索提取和 JSON 生成在两种模式中共用同一个 subagent 模板。只有预处理步骤因模式不同使用不同模板。

## Capabilities

### New Capabilities
- `workflow-orchestration`: 编排 skill 机制——manga-workflow 升级为状态感知的编排 skill，定义阶段流转、subagent dispatch 策略、上下文传递协议（只传必要信息）、中断恢复、灵活入口点
- `focused-subagent-tasks`: 聚焦 subagent 任务模板体系——将原多步 agent 拆解为独立的 task prompt 模板，每个模板定义输入/输出契约、内部可调用的 skill 列表、执行约束
- `global-character-clue-extraction`: 全局角色/线索提取——独立于单集流程，支持全书分析和增量追加两种模式，内部调用 ProjectManager 完成数据写入

### Modified Capabilities
（无现有 spec 需修改）

## Impact

- **Agent 定义**：删除 2 个大 agent，新增 3-4 个聚焦 task prompt 模板（`agent_runtime_profile/.claude/agents/`）
- **Skill 文件**：`manga-workflow/SKILL.md` 从静态文档重写为编排 skill，具备状态检测和 dispatch 逻辑
- **主 agent prompt**：`session_manager.py` 中 `_PERSONA_PROMPT` 增加编排意识和工作流阶段理解
- **agent_runtime_profile CLAUDE.md**：更新工作流文档和 skill/agent 边界说明
- **现有生成 skill 不受影响**：`generate-script`、`generate-characters`、`generate-clues`、`generate-storyboard`、`generate-video`、`compose-video` 保持不变（只是调用方从主 agent 变为 subagent）
- **后端服务不受影响**：`server/agent_runtime/` 中 subagent 仍通过 Task 工具 dispatch
- **前端不受影响**
- **数据模型不变**：`project.json` 结构不变
