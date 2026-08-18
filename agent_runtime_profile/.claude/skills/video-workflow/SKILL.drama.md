---
name: video-workflow
description: 将小说转换为短视频的端到端工作流编排器。当用户提到做视频、创建项目、继续项目、查看进度时必须使用此 skill。触发场景包括但不限于："帮我把小说做成视频"、"开个新项目"、"继续"、"下一步"、"看看项目进度"、"从头开始"、"拆集"、"自动跑完流程"等。即使用户只说了简短的"继续"或"下一步"，只要当前上下文涉及视频项目，就应该触发。不要用于单个资产生成（如只重画某张分镜图或只重新生成某个角色设计图——那些有专门的 skill）。
---
<!-- mode: drama -->

# 视频工作流编排

你（主 agent）是编排中枢。你**不直接**处理小说原文或生成剧本，而是：
1. 检测项目状态 → 2. 读计划的 `next_action` → 3. dispatch 合适的 subagent → 4. 展示结果 → 5. 获取用户确认 → 6. 循环

**核心约束**：
- 小说原文**永远不加载到主 agent context**，由 subagent 自行读取
- 每次 dispatch 只传**文件路径和关键参数**，不传大块内容
- 每个 subagent 完成一个聚焦任务就返回，主 agent 负责动作间衔接

> 两种生成模式（图生视频 storyboard，含 grid_storyboard 宫格开关 / 参考生视频 reference_video）的数据结构与 schema 差异详见 `.claude/references/generation-modes.md`；步骤适用性由计划表达，参考文档不重复。

---

## `collect_project_input`：项目设置

**重要**：项目目录的创建由 Web 端 `POST /api/v1/projects` 触发 `ProjectManager.create_project()` 完成（包括所有子目录与 `project.json`、按 content_mode 物化对应的 agent profile）。**主 agent 不创建目录、不写入 project.json 初始字段**——session 启动时 cwd 已绑定到已存在的项目根。

### 新项目

1. 提示用户在 Web 端先创建项目，**创建时指定 content_mode（narration / drama）与 generation_mode（storyboard / reference_video）**；两者创建后均不可变更，agent 无对应写入权限。session 启动后 cwd 已绑定到对应项目根
2. 使用 Read 工具读取 `project.json`，确认 `title`、`content_mode`、`generation_mode` 字段（本 session 当前 content_mode 为 `drama`，创建后不可变更）
3. 请用户将小说文本放入 `source/`
4. **上传后自动生成项目概述**（synopsis、genre、theme、world_setting）

> 标准项目子目录由 `create_project()` 自动建好：`source/`、`scripts/`、`drafts/`、`characters/`、`scenes/`、`props/`、`storyboards/`、`grids/`、`videos/`、`reference_videos/`、`thumbnails/`、`output/`。

### 现有项目

1. session cwd 已经绑定到目标项目根
2. 调用 `mcp__arcreel__get_workflow_plan({})` 取得服务端权威计划
3. 按返回的 `next_action` 从上次未完成的动作继续

---

## 计划查询

进入工作流、用户说“继续/下一步/查看进度”、以及每次工具或 subagent 完成后，都调用
`mcp__arcreel__get_workflow_plan({})` 取回权威计划（用户指定集数时传 `{"episode": N}`），
再按 `next_action.type` 路由到下面同名的小节。

计划的字段含义、完整受控动作表、旁白交付、批量准入、四条状态轴与 stale / 历史纪律，见
[.claude/references/workflow-plan.md](../../references/workflow-plan.md)。**本 skill 不重复一张按内容模式
或生成路线展开的步骤表**：哪些步骤适用、当前停在哪一步，一律读 `plan.steps[]` 与 `plan.next_action`，
它们是阶段判断的唯一真相源。`plan.status` 内嵌完整状态快照（`project` / `target` / `state` /
`blockers` / `gates` / `artifacts`），不需要再单独查一次状态。

调用后把 `plan.status.target.episode` 作为目标集，把 `next_action.args` 与 `requested_ids` 原样带入
对应动作。Read / Glob 只用于执行已选定动作所需的内容，不用于另建状态机；不得根据空资产 bucket、
文件名、旧文件存在性或对话记忆覆盖服务端结论。

下文各节以 `next_action.type` 为标题。`export` 表示工作流完成，`none` 表示展示 `blockers` 并停止变更。

---

## 动作间确认协议

**每个 subagent 返回后**，主 agent 执行：

1. **展示摘要**：将 subagent 返回的摘要展示给用户
2. **获取确认**：使用 AskUserQuestion 提供选项：
   - **继续下一动作**（推荐）
   - **重做此动作**（附加修改要求后重新 dispatch）
   - **跳过此动作**
3. **根据用户选择行动**

---

## `analyze_assets`：全局角色/场景/道具提取

**触发**：`next_action.type == "analyze_assets"`。空 bucket 是合法分析结果，不得凭空 bucket 重跑。

**dispatch `analyze-assets` subagent**：

```text
项目名称：{project_name}
分析范围：{next_action.args.scope 对应的权威范围；workflow 默认整部小说}
分析 scope：{next_action.args.scope}
expected source revision：{next_action.args.expected_source_revision}
已有角色：{已有角色名列表，或"无"}
已有场景：{已有场景名列表，或"无"}
已有道具：{已有道具名列表，或"无"}

请分析小说原文，提取角色 / 场景 / 道具信息，写入 project.json，返回摘要。
```

---

## `plan_episodes` / `reset_episode_planning`：分集规划

**恢复触发**：`next_action.type` 为 `"reset_episode_planning"` 时，先按 `next_action.args` 调
`mcp__arcreel__reset_episode_planning`。工具若返回已消费集确认要求，展示影响范围并取得用户明确确认，
再追加 `confirm_consumed: true` 重试；重置成功后刷新计划，按新的权威动作继续。

**触发**：`next_action.type == "plan_episodes"`

分集规划由服务端工具完成：工具内部从 `planning_cursor` 起读一个源文窗口，调用项目配置的文本模型一次规划出窗口内所有剧情弧完整的集（标题/钩子/原文范围 + 分集大纲：故事节点与下集预告），在同一把项目锁内写账本、派生 `source/episode_{N}.txt` 并清理残留派生文件。**主 agent 只调一次工具、只收摘要**——不读小说原文、不自行选切分点：

1. 规划前快速核对 `project.json`：
   - `source_language` 是否与源文实际语言一致。优先级：**用户显式配置 > 自动推断**（正常路径由 overview 生成自动落盘）；发现不一致时**提醒用户（WARN）、说明后果并建议修正**（错误配置会使规划的体量度量与语言前提失真），用户未修正时按显式配置继续，不阻塞流程。字段缺失或经用户确认有误时，走 `mcp__arcreel__patch_project({"settings": {"source_language": "en"|"vi"|"zh"}})` 写入
   - `episode_target_units`（每集目标体量，按 `source_language` 解读为阅读单位）：已设置则直接沿用；缺失且用户在对话中明确给过字数 → 经 `mcp__arcreel__patch_project({"settings": {"episode_target_units": N}})` 写入；都没有也可直接规划（工具会按短视频节奏自行把握体量），无需强制询问
2. 调用 `mcp__arcreel__plan_episodes({})`。窗口字数与每批集数上限为工具内部默认，项目设置 `planning_window_chars` / `planning_max_episodes` 可覆盖（经 patch_project settings 写入）。**用户在规划前给出常驻分集偏好时**（如"严格按章节切分，一章一集""每集在某处收尾"），把偏好原文经 `instructions` 传入：`mcp__arcreel__plan_episodes({"instructions": "用户意见原文"})`；意见原样注入规划 prompt 的「用户意见」分节，遵循强度由正文表达——用户明确要求硬性遵循时，把强度措辞一并写进正文（如「必须全部落实：一章一集」）。长篇会分多批规划（每批一次工具调用），该偏好**不持久化**，须在规划完成前**每一批调用都重复带上同一 `instructions`**
3. **批级审阅**：把工具返回的账本摘要（每集标题+钩子+体量）展示给用户，征求意见
4. 用户提出意见（一句话可同时包含任意多处意见，含全局偏好）→ 走「重置 + 重新规划」：先调用 `mcp__arcreel__reset_episode_planning({"from_episode": N})`，`from_episode` 取意见中最早受影响的集，保留其前的集不受影响
5. **已消费集警告确认**：重置会波及已消费集（已有 step1/剧本/媒体产物）时，工具会返回受影响集清单而不执行——把影响范围告知用户、获得明确确认后，追加 `"confirm_consumed": true` 重新调用；确认执行后这些集的账本条目被清除，产物本身不删除
6. 重置完成后，全局性意见（如每集体量）先经 `mcp__arcreel__patch_project({"settings": {"episode_target_units": N}})` 显式写入，再带调整后的 `instructions` 重新调用 `mcp__arcreel__plan_episodes` 从 `from_episode` 起分批规划、结果再次展示审阅；若新提交的集号与原消费范围重叠，工具会自动标 stale（产物不删除，需重做下游产物），无需额外确认。**规划完毕后返回会附全局核对材料**（累计集数、体量最小几集、体量中位数、目标体量）：若用户给过总集数、按章节对齐等结构性偏好，须对照核对，有偏差须向用户明确说明（可引导用户重新走「重置 + 重新规划」修正）
7. 用户对本批规划满意后刷新计划继续。**用户显式授权全自主时**（如"直接跑完整个流程不用逐步确认"），可跳过批级审阅直接继续

---

## `prepare_step1`：单集预处理

**触发**：`next_action.type == "prepare_step1"`

dispatch `next_action.args.preprocessor` 指名的 subagent，产出 `drafts/episode_{N}/` 下对应的 step1
中间文件。**不要自己按 `generation_mode` × `content_mode` 反推该选谁**：服务端在同一张规则表上得出
`preprocessor`，profile 侧再推一遍只会造出第二个真相源。各 step1 文件与 schema 的对应关系见
`.claude/references/generation-modes.md`。

dispatch prompt 通用参数：项目名称、项目路径、集数、本集小说文件路径；可选附加说明（用户对本次生成的意见等任何需带给 subagent 的临时上下文，原文透传）。

若 `next_action.args` 含 `expected_stale_step1_revision`，subagent 成功产出正式 step1 后必须调用
`mcp__arcreel__complete_step1_rebuild({"episode": N, "expected_stale_step1_revision": next_action.args.expected_stale_step1_revision})`。
该完成事实不可用“文件内容是否变化”推断：确定性重建可能产出完全相同的 JSON。工具报冲突时刷新计划，
不得用旧参数重试。

（两个预处理 subagent 会自行读 project.json + 调用
`mcp__arcreel__get_video_capabilities({})`
拿到模型能力与用户偏好；主 agent 不需要预先注入角色/场景/道具列表或
`supported_durations` / `max_duration` / `max_reference_images` / `default_duration` 等数据。）

**中间文件变更必重生剧本 JSON**：`prepare_step1` 的中间文件被修改或重拆后（无论哪种生成模式、无论首次还是重做），即使 `scripts/episode_{N}.json` 已存在，也必须重新执行 `generate_script`——剧本 JSON 不会自动跟随中间文件更新，跳过会留下"新中间文件 + 旧 JSON"的陈旧组合。

---

## `confirm_step1` / `generate_script`：JSON 剧本生成

**触发**：

- `next_action.type == "confirm_step1"` → 先完成下述审核 gate，刷新计划后再路由
- `next_action.type == "generate_script"` → dispatch 剧本生成

**step1→step2 审核 gate（阻塞）**：`prepare_step1` 的结构化 step1 中间态须经**显式确认**才放行剧本生成（三种结构化 step1 变体——drama / narration / reference_video——一律适用；`reference_video` 的 `step1_reference_units.json` 同样须确认，不要跳过。ad 无 step1，不纳入 gate）。两条等价确认路径——用户在 Web 端审阅 / 编辑后确认，或在对话中明确同意进入视觉生成后由你调用 `mcp__arcreel__confirm_script_review({"episode": N})`（全自主模式下按用户总体授权确认）。未确认（或确认后 step1 又被改）时 `generate_episode_script` 会被 gate 拒绝；**存量项目**（升级前已生成过本集剧本）已 grandfather 放行、无需再确认。

**dispatch `create-episode-script` subagent**：传入项目名称、项目路径、集数；可选附加说明（用户对本次生成的意见等任何需带给 subagent 的临时上下文，原文透传）。

---

## `generate_asset_sheets`：资产设计（character / scene / prop 三类并行）

**触发**：`next_action.type == "generate_asset_sheets"`。空资产 bucket 是 `analyze_assets` 的合法完成结果，
不得据此回退；对每个资产类型，取 `artifacts.asset_sheets[type].missing_ids` 与 `requested_ids` 的交集作为
该类型的 `names`，同时传给 subagent 和工具：
- character 缺 character_sheet
- scene 缺 scene_sheet
- prop 缺 prop_sheet

**调度规则（显式条件判断，按类型独立决定）**：

```text
对于 type ∈ {character, scene, prop}:
  names = artifacts.asset_sheets[type].missing_ids ∩ requested_ids
  若 names 非空 → dispatch 对应的 `generate-assets` subagent，并把 names 原样传给 subagent 和工具
  若 names 为空 → 跳过，不 dispatch；不得回退到整类 missing_ids

三类判断彼此独立，结果可能 dispatch 0~3 个 subagent。
所有 dispatch 的 subagent 返回后，合并摘要展示给用户，进入动作间确认。
```

下面三个 dispatch 块是模板，只实例化满足上述条件的那几个：

### subagent — 角色设计

**触发**：该类 `names` 交集非空

```text
dispatch `generate-assets` subagent：
  任务类型：character
  项目名称：{project_name}
  待生成项：{names 交集}
  工具调用：
    mcp__arcreel__generate_assets({"type": "character", "names": [该类型 requested_ids]})
  验证方式：重新读取 project.json，检查对应角色的 character_sheet 字段
```

### subagent — 场景设计

**触发**：该类 `names` 交集非空

```text
dispatch `generate-assets` subagent：
  任务类型：scene
  项目名称：{project_name}
  待生成项：{names 交集}
  工具调用：
    mcp__arcreel__generate_assets({"type": "scene", "names": [该类型 requested_ids]})
  验证方式：重新读取 project.json，检查对应场景的 scene_sheet 字段
```

### subagent — 道具设计

**触发**：该类 `names` 交集非空

```text
dispatch `generate-assets` subagent：
  任务类型：prop
  项目名称：{project_name}
  待生成项：{names 交集}
  工具调用：
    mcp__arcreel__generate_assets({"type": "prop", "names": [该类型 requested_ids]})
  验证方式：重新读取 project.json，检查对应道具的 prop_sheet 字段
```

---

## `generate_storyboards` / `generate_grid`：分镜图生成

**触发**：`next_action.type` 为 `"generate_storyboards"` 或 `"generate_grid"`；服务端不会在
reference_video 模式返回这两个动作。

按动作直接选择工具，不二次检查 `generation_mode` 或 `grid_storyboard`：

- `next_action.type == "generate_storyboards"` → dispatch `generate-assets`，调
  `mcp__arcreel__generate_storyboards({"script": target.script_filename, "segment_ids": requested_ids})`
- `next_action.type == "generate_grid"` → dispatch `generate-assets`，调
  `mcp__arcreel__generate_grid({"script": target.script_filename, "scene_ids": requested_ids})`

两条路径都把 `next_action.args` 与 `requested_ids` 原样传给 subagent，由 subagent 按上面映射调用工具。

> **切换 `grid_storyboard` 后的重做**：本动作的常规触发条件是「缺分镜图」，而用户在设置页切换该开关不会让已有分镜图失效，剧本里也不记录分镜图由哪种装配方式产出——单看缺图会把整集判成已完成。用户在已有分镜图的项目上切换开关后要求按新方式出图时，与其确认要重做的场景范围，再显式带 ID 重生：切到宫格用 `mcp__arcreel__generate_grid({"script": target.script_filename, "scene_ids": [...]})`，切回单图用 `mcp__arcreel__generate_storyboards({"script": target.script_filename, "segment_ids": [...]})`（`script` 必填；ID 列表省略时只补缺图，达不到重做效果）。已生成的视频同样不会自动失效，重出分镜图后需按新图重跑 `generate_videos` 对应场景。

## `generate_videos`：视频生成

**触发**：`next_action.type == "generate_videos"`

入队前计划可能先交回两个受控动作，按 [workflow-plan](../../references/workflow-plan.md) 处理完再重查计划：

- `choose_narration_delivery` — 本次请求含叙述旁白。向用户**显式说明**这次要发起的是叙述旁白视频
  请求，并在「使用当前 TTS」与「后期配音」之间二选一；选择经 `narration_delivery` 带进下一次
  `mcp__arcreel__get_workflow_plan`，不持久化，之后每次查询都要重新带上。未配置 TTS 时默认后期配音，
  不要为了让视频继续而建议用户去配置 TTS 供应商；选 TTS 时先显式生成并让用户试听，再按
  预检返回的 `problems[].action` 处理（action 是权威，不要按 `code` 自己推）
- `confirm_request_duration` — 批量准入要求确认申请档位。按 `admission.confirmation.tiers[]` 逐档位
  展示涉及的 unit 与费用，取得确认后经 `confirmed_request_durations` 连同仍成立的 `narration_delivery` 一起带回

只有 `plan.steps[].admission.decision == "admitted"` 才入队；`blocked` 或 `confirmation_required` 时
**一个任务都不入队**。此时逐 unit 报告 `admission.units[]` 的 `unit_id`、`problems[].code`、原因与
`problems[].action`；被别人挡住的 unit 带 `generation_batch_admission_withheld`，如实说明是被
`blocked_unit_ids` 连累而非自身有问题。修掉被拒 unit 后**整批重来**，不拆批先跑通过的那一半，
否则会重复提交已经付过费的 unit。

**dispatch `generate-assets` subagent**：请求选择语义与 Web 完全一致——**点名即强制重做（必然计费）/
不传即只补缺 / 空数组非法**，所以按 `requested_ids` 是否为空二选一，不要两个工具都试：

```text
dispatch `generate-assets` subagent：
  任务类型：video
  项目名称：{project_name}
  工具调用（两个工具的 narration_delivery 均为必填，填本次已向用户确认的那个值）：
    requested_ids 非空 →
      mcp__arcreel__generate_video_selected({"script": target.script_filename, "scene_ids": requested_ids,
                                             "narration_delivery": chosen_narration_delivery})
    requested_ids 为空 →
      mcp__arcreel__generate_video_episode({"script": target.script_filename,
                                            "narration_delivery": chosen_narration_delivery})
  验证方式：重新读取 target.script，检查各场景的 video_clip 字段
```

`narration_delivery` 省略或写错值一律返回工具错误、不入队任何任务，也不退回后期配音。凑够必填项
不等于做过选择：没和用户确认过就先走 `choose_narration_delivery`，不要自己填一个值。

返回后按逐 ID 分账陈述结果（`succeeded` / `failed` / `blocked` / `skipped`），并把 workflow 步骤状态、
队列任务、provider checkpoint、产物时效四轴**分开说**——「任务成功」不等于「当前产物有效」。
stale 产物照常可预览、可导出、可参与成片，是否重做由用户明确决定；不自动删除、覆盖或重生已付费产物。

---

## `repair_video_units` / `patch_episode_script`：改剧本再重做

**触发**：`next_action.type` 为 `"repair_video_units"` 或 `"patch_episode_script"`。

Read `target.script`，**只处理 `requested_ids` 对应的条目**。revision 按动作取：
`patch_episode_script` 的 `next_action.args` 已直接给出 `expected_revision` 与逐条 `problems`，
直接用，不必再查；`repair_video_units` 的 args 里没有，先调
`mcp__arcreel__get_episode_script_revision({"script": target.script_filename})` 取。
再用**一次** `mcp__arcreel__patch_episode_script({"script": target.script_filename,
"expected_revision": <上面取到的 revision>, "operations": [...]})` 把全部条目改完——每条一个有序 `update`。
`needs_replan` 之类的标记由工具重算，不要手写。工具报 revision 冲突时刷新计划重来，不得用旧
revision 重试。改完后按上面的请求选择语义点名重做这些 ID，再刷新计划。

---

## `wait_for_task`：有任务在跑

**触发**：`next_action.type == "wait_for_task"`。

已有任务在队列或供应商侧执行中。**不入队任何新任务**，把 `steps[].tasks[]` 的 `task_id`、`status`
与 `provider_checkpoint` 如实说给用户，等待后重新调 `mcp__arcreel__get_workflow_plan` 复查。
`provider_checkpoint.submitted == true` 表示供应商侧已提交、很可能已计费，此时重新提交等于重复付费。

---

## 灵活入口

工作流**不强制从头开始**。根据计划结果，自动从正确的动作开始：

- "分析小说角色" → 只执行 `analyze_assets`
- "创建第2集剧本" → 从 `plan_episodes` 开始（如果角色已有）
- "继续" → 计划给出第一个未完成动作
- 指定具体动作（如"生成分镜图"）→ 该动作只是用户意图，仍先查计划：与 `next_action.type` 一致才执行；
  不一致或有 blockers 时不入队，改为说明计划当前要求的动作与原因

---

## 数据分层

- 角色 / 场景 / 道具完整定义**只存 project.json**，剧本中仅引用名称
- 统计字段（item_count、status、progress）**读时计算**，不存储
- 剧集元数据（episode/title/script_file）在剧本保存时**写时同步**
