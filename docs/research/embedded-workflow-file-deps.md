# 内嵌智能体工作流的裸文件读写依赖面盘点

> Research ticket: [盘点内嵌工作流的裸文件读写依赖面与结构化工具缺口](https://github.com/ArcReel/ArcReel/issues/1703)，属于 [Wayfinder: 外部智能体全流程接入（远程 MCP + Skill 手册）与 chat 对接退场](https://github.com/ArcReel/ArcReel/issues/1702)。
>
> 调查基线：`main@2b057ae7052c569c08d20b775cd436749c2b76b6`，2026-08-20。本文只描述该基线的当前事实。

## 结论摘要

Spec [#1669](https://github.com/ArcReel/ArcReel/issues/1669) 已显著缩小裸文件依赖，但没有把它消除：

- **阶段判断已经结构化**。`get_workflow_plan` 现在是工作流的唯一权威入口，返回步骤、阻断、任务、产物状态、准入结论与唯一下一动作；`Read` / `Glob` 不再承担状态机职责（`agent_runtime_profile/.claude/references/workflow-plan.md:3-8,23-25,29-40`）。
- **生成完成性已经结构化**。生成工具统一返回逐 ID 的 `succeeded` / `failed` / `blocked` / `skipped` 与稳定问题码，理论上无需靠重读文件推断任务结果（`agent_runtime_profile/.claude/references/generation-results.md:3-5,21-47`）。但当前 profile 仍明确要求多个子任务生成后重新读取 `project.json` 或剧本 JSON 验证，所以这是**当前真实依赖**，不是已经删除的遗留说明。
- **正式写入大多已收归事务工具**。`project.json` 与 `scripts/` 整子树禁止裸写；drama 与 reference-video 的正式 step1 也禁止裸写，改动经 `open_step1_for_edit` → 隔离草稿 → `validate_and_promote_draft` 晋升（`server/agent_runtime/agent_access_policy.py:715-750`）。
- **仍有一个正式数据裸写面**：narration 的 `drafts/episode_N/step1_segments.json` 继续由子任务直接 `Edit`，没有 revision 检测、项目锁或结构化 patch 工具；运行时也刻意没有把它列入正式 step1 写禁，因为没有替代通道（`agent_runtime_profile/.claude/agents/split-narration-segments.md:68-85`；`lib/episode_paths.py:52-57`）。
- **隔离草稿仍是设计内裸编辑工位**。drama、reference step1 与 reference step2 的 `.invalid.json` 都需要内置 `Read` / `Edit`，结构化工具只负责取回和晋升；远程 MCP 客户端无法访问项目盘时，这仍是最硬的闭环缺口。
- **内容读取仍几乎没有结构化覆盖**。当前注册 32 个进程内 MCP 工具（`server/agent_runtime/sdk_tools/__init__.py:75-108`），有权威计划、资产 pending、视频能力和剧本 revision 等读模型，却没有项目内容、源文、剧本正文、step1 正文或隔离草稿正文的读取接口。
- **compose-video 仍是本地 Bash/Python 文件工作流**：读剧本与片段/BGM，写 `output/`，没有 MCP 等价物。

因此，外部 agent 仅靠现有远程 MCP 工具面仍不能等价执行内嵌工作流。最低闭环需要同时解决：**内容读取、隔离草稿读取/patch、narration step1 事务编辑**；compose-video 则应在本次 Spec 中明确排除或另行服务端化。

## 调查范围与口径

调查对象是内嵌 profile 的 9 个有效 Skill（`video-workflow` 三个物化变体按运行时三选一计一个）、6 个 Subagent、三份 `CLAUDE.*.md`、`server/agent_runtime/sdk_tools/` 工具目录与运行时访问策略。

本文所称：

- **裸读写**：工作流要求 agent 使用内置 `Read` / `Glob` / `Grep` / `Write` / `Edit`，或通过 `Bash` 在项目目录直接读写文件。
- **结构化覆盖**：MCP 工具直接返回所需事实或在服务端持锁完成正式写入；仅返回路径、revision 或状态投影，不算正文读取覆盖。
- **远程缺口**：外部 agent 只有远程 MCP，没有与 ArcReel 服务端共享的项目文件系统，也不能调用内嵌 harness 的文件工具。

## 当前运行时写边界

| 路径 | 内置 `Write` / `Edit` | Bash 子进程 | 合法写入通道 |
|---|---|---|---|
| `project.json` | 拒绝 | 拒绝 | `patch_project`、`rename_asset`、规划/清单完成工具与 worker 回写 |
| `scripts/**` | 拒绝整子树 | 拒绝整子树 | `generate_episode_script`、revision-checked `patch_episode_script` 及其便捷编辑工具 |
| `drafts/episode_N/step1_normalized_script.json` | 拒绝 | `drafts/` 整体拒绝 | `open_step1_for_edit` → 裸改 `.invalid.json` → `validate_and_promote_draft` |
| `drafts/episode_N/step1_reference_units.json` | 拒绝 | `drafts/` 整体拒绝 | 同上 |
| `drafts/episode_N/step1_segments.json` | **允许** | `drafts/` 整体拒绝 | 内置 `Edit`；当前没有事务式替代 |
| `drafts/episode_N/*.invalid.json` | **允许** | `drafts/` 整体拒绝 | 内置 `Edit`；晋升由 `validate_and_promote_draft` 持锁完成 |
| `output/**` | 允许非代码产物 | 允许 | `compose-video` 脚本 |

正式 step1 写禁集合明确只含 drama 与 reference-video；代码注释同时说明 narration 因仍由 subagent 直接编辑、无草稿通道而不在集合内（`lib/episode_paths.py:52-57`）。访问策略对 `project.json`、`scripts/`、正式 step1 与隔离草稿的边界投影见 `server/agent_runtime/agent_access_policy.py:719-750`。

## 文件依赖 → 结构化工具覆盖状态

### 1. `project.json`

| 工作流依赖 | 读/写 | 当前结构化覆盖 | 外部 MCP 缺口 |
|---|---|---|---|
| 阶段、目标集、阻断、产物 current/stale/missing、活动任务、下一动作 | 读 | **已覆盖**：`get_workflow_plan` 是唯一权威入口，并返回 `status.project` / `target` / `gates` / `artifacts`（`agent_runtime_profile/.claude/references/workflow-plan.md:27-40,67-98`） | 无需补通用文件读取来承担状态机 |
| 资产名称与 description、overview/style/settings、ad 的 brief/products/selling_points | 读 | **未覆盖正文**：计划只给状态投影；`list_pending_assets` 给 pending，不给完整项目内容 | 需要受限项目内容 read model，或白名单只读文件工具 |
| analyze-assets 读取已有名称、背景与 `source_kind` | 读 | 未覆盖；子任务明确 `Read project.json`（`agent_runtime_profile/.claude/agents/analyze-assets.md:26-40`） | 没有正文读取即无法做增量资产分析 |
| 各预处理器核对已登记资产；ad 起草卖点；生成子任务前后验证字段 | 读 | 未覆盖；例如通用生成子任务仍要求前读、后重读（`agent_runtime_profile/.claude/agents/generate-assets.md:20-36`） | 外部工作流无法复刻当前验证与创作输入读取 |
| 资产/settings/overview/账本/sheet 等正式写入 | 写 | **已覆盖并强制**：`patch_project`、`rename_asset`、`plan_episodes`、`reset_episode_planning`、`complete_asset_inventory` 与 worker 回写；裸写被双层拒绝 | 无通用写文件需求 |

### 2. `source/*.txt` / `source/*.md` 与 `source/episode_N.txt`

| 工作流依赖 | 读/写 | 当前结构化覆盖 | 外部 MCP 缺口 |
|---|---|---|---|
| 全局资产分析：列出权威 scope 内源文件并读取正文 | `Glob` + `Read` | **未覆盖**。analyze-assets 明确按 scope 枚举并读取文本（`agent_runtime_profile/.claude/agents/analyze-assets.md:33-40`）；`complete_asset_inventory` 只原子提交结果与 source revision，不提供正文 | 需要 scope-aware 源文读取，或把资产分析整体服务端化 |
| 分集规划与首次 step1 生成 | 服务端内部读 | **已覆盖 agent 侧需求**：`plan_episodes`、`normalize_drama_script`、`split_narration_segments`、`split_reference_video_units` 接收相对 source 路径或由计划给出目标，正文不必进入主 agent | 远程调用者需要稳定获得可传入的 source 标识；计划已在 `target.source` 提供（`agent_runtime_profile/.claude/references/workflow-plan.md:69-72`） |
| 派生 `source/episode_N.txt` | 写 | **已覆盖**：规划工具持锁派生，profile 明令主 agent 不自行切分（`agent_runtime_profile/.claude/skills/video-workflow/SKILL.narration.md:99-117`） | 无 |

### 3. narration 正式 step1：`drafts/episode_N/step1_segments.json`

| 工作流依赖 | 读/写 | 当前结构化覆盖 | 外部 MCP 缺口 |
|---|---|---|---|
| 首次生成 | 写 | `split_narration_segments` 服务端生成并校验 | 无 |
| 生成后结构验证、向用户呈现、修改前读取 | 读 | **未覆盖正文**；子任务明确 `Read`（`agent_runtime_profile/.claude/agents/split-narration-segments.md:68-79`） | 需要 step1 内容读取 |
| 修改已有拆分或修结构 | **裸 `Edit` 正式文件** | **未覆盖**；当前直接修改并以存在性分情况（`agent_runtime_profile/.claude/agents/split-narration-segments.md:54-85`） | 需要与 Web 保存同锁、revision-checked 的 narration step1 patch/open-promote 接口；否则远程 agent 无法修改，内嵌 agent 仍有丢失更新窗口 |

这是当前唯一仍由 agent 裸写的正式结构化项目数据。

### 4. drama 正式 step1 与隔离草稿

路径：

- 正式：`drafts/episode_N/step1_normalized_script.json`
- 草稿：`drafts/episode_N/step1_normalized_script.invalid.json`

| 工作流依赖 | 读/写 | 当前结构化覆盖 | 外部 MCP 缺口 |
|---|---|---|---|
| 首次生成与正式写盘 | 写 | `normalize_drama_script` 服务端生成、校验、写盘 | 无 |
| 正式内容验证与查看 | 读 | **未覆盖正文**；首次生成后仍 `Read` 正式 JSON（`agent_runtime_profile/.claude/agents/normalize-drama-script.md:55-82`） | 需要 step1 内容读取 |
| 修改已有内容 | 正式写结构化；草稿裸读写 | `open_step1_for_edit` 持锁取回、`validate_and_promote_draft` 持锁晋升；中间仍用 `Edit content.scenes[i]`（`agent_runtime_profile/.claude/agents/normalize-drama-script.md:84-123`） | 远程侧既读不到草稿正文，也不能 patch；需要 `get_draft` + revisioned `patch_draft`，或让 open 工具返回结构化正文并提供结构化改稿 |

`open_step1_for_edit` 当前明确只支持有隔离草稿位的变体；narration/ad 会返回“没有隔离草稿编辑通道”（`server/agent_runtime/sdk_tools/text_generation.py:1334-1382`）。

### 5. reference-video 正式 step1 与隔离草稿

路径：

- 正式：`drafts/episode_N/step1_reference_units.json`
- step1 草稿：`drafts/episode_N/step1_reference_units.invalid.json`
- step2 草稿：`drafts/episode_N/step2_reference_script.invalid.json`

| 工作流依赖 | 读/写 | 当前结构化覆盖 | 外部 MCP 缺口 |
|---|---|---|---|
| 首次 step1 生成与正式写盘 | 写 | `split_reference_video_units` 服务端生成；通过才写正式文件，违约则隔离 | 无 |
| 正式 step1 验证与查看 | 读 | **未覆盖正文**；成功后仍裸 `Read`（`agent_runtime_profile/.claude/agents/split-reference-video-units.md:55-78`） | 需要 step1 内容读取 |
| 修改已有 step1 | 正式写结构化；草稿裸读写 | `open_step1_for_edit` → `Read/Edit content.units[i]` → `validate_and_promote_draft`（`agent_runtime_profile/.claude/agents/split-reference-video-units.md:80-102`） | 同 drama：草稿正文与 patch 不可达 |
| step1/step2 违约修复循环 | 草稿裸读写 | 仅晋升结构化；`create-episode-script` 要求 Read/Edit 草稿后反复晋升（`agent_runtime_profile/.claude/agents/create-episode-script.md:44-54`） | **硬阻断**：没有草稿访问即无法保留已付费产物并修复，重抽既违背当前语义又可能重复计费 |

晋升工具确实支持 drama step1、reference step1 与 reference step2 三类草稿，但它读取的是服务端磁盘上的已修改草稿，并不提供正文传输或 patch API（`server/agent_runtime/sdk_tools/text_generation.py:1645-1725`）。

### 6. `scripts/episode_N.json`

| 工作流依赖 | 读/写 | 当前结构化覆盖 | 外部 MCP 缺口 |
|---|---|---|---|
| 前置检查与生成后 schema/统计验证 | 读 | **未覆盖正文**；create-episode-script 仍要求前读 `project.json`/Glob step1、后读脚本（`agent_runtime_profile/.claude/agents/create-episode-script.md:24-38,56-64`） | 需要剧本内容 read model，或清理 profile 中已被计划/结果契约替代的验证步骤 |
| 按计划修复 `requested_ids`、人工改 prompt/正文 | 读 | 计划给路径、问题和部分动作参数；`get_episode_script_revision` 只返回 revision，不返回条目正文（`server/agent_runtime/sdk_tools/patch_script.py:139-162`） | 远程 agent 无法在不知道当前条目内容时构造安全 update |
| 正式写入 | 写 | **已覆盖并强制**：生成工具与 revision-checked `patch_episode_script`；批量操作全量预检、原子提交（`server/agent_runtime/sdk_tools/patch_script.py:165-230`） | 无裸写需求，但读取接口必须与 revision 同快照或带 revision，避免 read→patch TOCTOU |
| 生成后字段验证 | 读 | 逐 ID 结果契约已经给出权威结果，但当前 video-workflow 仍把“重新读取 target.script / project.json”传给生成子任务（例如 `agent_runtime_profile/.claude/skills/video-workflow/SKILL.narration.md:182-221,262-284,307-320`） | 两条选择：远程手册删除冗余验证，或提供读取接口；不能假定当前内嵌 profile 已经不读 |

### 7. 媒体目录与产物状态

路径包括 `characters/`、`scenes/`、`props/`、`products/`、`storyboards/`、`grids/`、`videos/`、`reference_videos/`、`audio/`、`thumbnails/`。

工作流的“是否缺失/是否陈旧/是否有任务在跑”已经由 Artifact Manifest、`get_workflow_plan` 与逐 ID 生成结果结构化覆盖，agent 不应再靠文件存在性建立状态机（`agent_runtime_profile/.claude/references/workflow-plan.md:23-40`；`agent_runtime_profile/.claude/references/generation-results.md:49-63`）。媒体质量审阅与导出本来就在 WebUI；远程 MCP 如需展示，只需稳定的受鉴权产物 URL/read model，不需要开放任意文件读取。

例外是 compose-video，它直接消费本地视频与可选 BGM，见下一节。

### 8. `output/` 与 compose-video

drama 的 `compose-video` 仍通过：

```text
python .claude/skills/compose-video/scripts/compose_video.py scripts/episode_1.json
```

脚本从 `scripts/` 读取剧本、按剧本顺序读取 `videos/*.mp4` 与可选 BGM，并把成片写入 `output/`；skill 明确要求共享项目 cwd（`agent_runtime_profile/.claude/skills/compose-video/SKILL.md:8-18,31-58`）。输出被强制约束在 `output/`（`agent_runtime_profile/.claude/skills/compose-video/scripts/compose_video.py:652-669`）。当前没有 MCP 等价物，外部 agent 无共享文件系统时不能调用。

### 9. 通用 Bash 文件浏览

三份系统 prompt 仍允许 Bash 用于 `ls` / `cat` / `jq` / `python` / `curl` 等通用排查与文件浏览；drama 另外允许 compose-video（例如 `agent_runtime_profile/CLAUDE.drama.md:34-48`）。这不对应单一业务阶段，但意味着内嵌 agent 在诊断异常时仍可依赖共享文件系统。权威计划压缩了日常使用频率，不等于给远程 agent 提供了同等诊断能力。

## 现有 MCP 读面的准确边界

当前 32 个工具中，与本报告的读需求直接相关的是：

| 工具 | 返回什么 | 不返回什么 |
|---|---|---|
| `get_workflow_plan` | 工作流步骤、目标、阻断、任务、产物状态、准入、下一动作 | 项目/剧本/step1/源文/草稿正文 |
| `list_pending_assets` | 待生成资产名单及有限描述 | 完整 project 内容与任意字段查询 |
| `get_video_capabilities` | 当前项目的视频能力与时长约束 | 项目创作内容 |
| `get_episode_script_revision` | 剧本 canonical revision | 剧本正文 |
| `open_step1_for_edit` | 在服务端创建隔离草稿，返回路径与摘要 | 草稿正文；且不支持 narration |
| `validate_and_promote_draft` | 校验/晋升服务端已有草稿 | 远程 patch 草稿的能力 |

所以“已有 32 个工具”不能解释为“远程端已有只读内容面”。它解决的是工作流决策、受控写入和生成执行，不是创作正文传输。

## 外部 agent 工具面的最小决策清单

按阻断程度排序：

1. **隔离草稿远程闭环**：为 drama/reference 三类 `.invalid.json` 提供受限读取与 revision-checked patch；工具必须保持“正式文件只由持锁晋升写入”的不变量。推荐专用 `get_editable_draft` / `patch_editable_draft`，不把任意文件写暴露给远程客户端。
2. **narration step1 事务化**：把 `step1_segments.json` 纳入与 drama/reference 相同的受控编辑边界，随后再加入 `AGENT_PROTECTED_STEP1_FILENAMES`。在此之前，远程 agent 无法等价修改，内嵌 agent 也仍有并发覆盖风险。
3. **内容读取面**：至少覆盖项目创作内容、目标集剧本、step1 与源文 scope。若采用“结构化专用工具为主、只读文件工具兜底”，只读工具必须白名单项目内业务文件、阻止敏感文件和跨项目路径，并返回稳定 revision/etag 供后续 patch 使用。
4. **清理冗余验证依赖**：远程 skill 手册应以 `get_workflow_plan` 与逐 ID 结果为完成性真相源，删除“生成后重读 JSON 推断成功”的步骤；内容审阅仍走专用读取接口，不能与任务验证混为一谈。
5. **compose-video 定界**：本次外部接入 Spec 明确不支持，或另立服务端合成/导出任务。不要把服务端本地 Python 路径写进远程 skill 手册。
6. **媒体呈现**：优先暴露受鉴权的产物 URL/元数据，不开放媒体目录任意读取。

## 对 Wayfinder 地图的输入

- [外部工具面清单、项目参数化与 Spec #1669 依赖时序](https://github.com/ArcReel/ArcReel/issues/1707) 应以本报告的当前边界为事实输入：权威计划和逐 ID 结果已经可直接复用；工具面决策必须补上内容读取、隔离草稿闭环与 narration step1 事务编辑，并对 compose-video 作显式范围裁决。
- 地图的“Decisions so far”应把旧的“narration/drama step1 裸 Edit”改为：**drama 正式 step1 已收归草稿晋升；仅 narration 正式 step1 仍裸 Edit；三类隔离草稿仍裸读写。**
- 不需要新增或毕业新的 fog：这些问题已经由“外部工具面清单、项目参数化与 Spec #1669 依赖时序”精确承接；“多文件 skill 包分发”“用量/费用归因”“项目事件流”“API Key 细粒度作用域”四块现有 fog 不因本次文件依赖盘点而改变。
