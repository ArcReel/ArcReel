---
name: manga-workflow
description: 将小说转换为短视频的端到端工作流编排器。当用户提到做视频、创建项目、继续项目、查看进度时必须使用此 skill。触发场景包括但不限于："帮我把小说做成视频"、"开个新项目"、"继续"、"下一步"、"看看项目进度"、"从头开始"、"拆集"、"自动跑完流程"等。即使用户只说了简短的"继续"或"下一步"，只要当前上下文涉及视频项目，就应该触发。不要用于单个资产生成（如只重画某张分镜图或只重新生成某个角色设计图——那些有专门的 skill）。
---
<!-- mode: drama -->

# 视频工作流编排

你（主 agent）是编排中枢。你**不直接**处理小说原文或生成剧本，而是：
1. 检测项目状态 → 2. 决定下一阶段 → 3. dispatch 合适的 subagent → 4. 展示结果 → 5. 获取用户确认 → 6. 循环

**核心约束**：
- 小说原文**永远不加载到主 agent context**，由 subagent 自行读取
- 每次 dispatch 只传**文件路径和关键参数**，不传大块内容
- 每个 subagent 完成一个聚焦任务就返回，主 agent 负责阶段间衔接

> 两种生成模式（图生视频 storyboard，含 grid_storyboard 宫格开关 / 参考生视频 reference_video）的数据路径与阶段分支详见 `.claude/references/generation-modes.md`。

---

## 阶段 0：项目设置

**重要**：项目目录的创建由 Web 端 `POST /api/v1/projects` 触发 `ProjectManager.create_project()` 完成（包括所有子目录与 `project.json`、按 content_mode 物化对应的 agent profile）。**主 agent 不创建目录、不写入 project.json 初始字段**——session 启动时 cwd 已绑定到已存在的项目根。

### 新项目

1. 提示用户在 Web 端先创建项目，**创建时指定 content_mode（narration / drama）与 generation_mode（storyboard / reference_video）**；两者创建后均不可变更，agent 无对应写入权限。session 启动后 cwd 已绑定到对应项目根
2. 使用 Read 工具读取 `project.json`，确认 `title`、`content_mode`、`generation_mode` 字段（本 session 当前 content_mode 为 `drama`，创建后不可变更）
3. 请用户将小说文本放入 `source/`
4. **上传后自动生成项目概述**（synopsis、genre、theme、world_setting）

> 标准项目子目录由 `create_project()` 自动建好：`source/`、`scripts/`、`drafts/`、`characters/`、`scenes/`、`props/`、`storyboards/`、`grids/`、`videos/`、`reference_videos/`、`thumbnails/`、`output/`。

### 现有项目

1. session cwd 已经绑定到目标项目根
2. 调用 `mcp__arcreel__get_workflow_status({})` 取得服务端权威状态
3. 按返回的 `next_action` 从上次未完成的阶段继续

---

## 状态检测

进入工作流、用户说“继续/下一步/查看进度”、以及每次工具或 subagent 完成后，都调用
`mcp__arcreel__get_workflow_status({})`。用户指定集数时传 `{"episode": N}`。返回的
`project`、`target`、`state`、`blockers`、`gates`、`artifacts` 与 `next_action` 是阶段判断的唯一真相源；
Read / Glob 只用于执行已选定动作所需的内容，不用于另建状态机。

按 `next_action.type` 路由：

| next_action.type | 阶段 |
|---|---|
| `collect_project_input` | 阶段 0 |
| `analyze_assets` | 阶段 1 |
| `reset_episode_planning` | 阶段 2（先重置） |
| `plan_episodes` | 阶段 2 |
| `prepare_step1` | 阶段 3 |
| `confirm_step1` / `generate_script` | 阶段 4 |
| `generate_asset_sheets` | 阶段 5 |
| `generate_storyboards` / `generate_grid` | 阶段 6 |
| `generate_videos` | 阶段 7 |
| `export` | 工作流完成 |
| `none` | 展示 `blockers` 并停止变更 |

调用后把 `target.episode` 作为目标集，把 `next_action.args` 与 `requested_ids` 原样带入对应阶段。
不得根据空资产 bucket、文件名、旧文件存在性或对话记忆覆盖服务端结论。

---

## 阶段间确认协议

**每个 subagent 返回后**，主 agent 执行：

1. **展示摘要**：将 subagent 返回的摘要展示给用户
2. **获取确认**：使用 AskUserQuestion 提供选项：
   - **继续下一阶段**（推荐）
   - **重做此阶段**（附加修改要求后重新 dispatch）
   - **跳过此阶段**
3. **根据用户选择行动**

---

## 阶段 1：全局角色/场景/道具提取

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

## 阶段 2：分集规划

**恢复触发**：`next_action.type` 为 `"reset_episode_planning"` 时，先按 `next_action.args` 调
`mcp__arcreel__reset_episode_planning`。工具若返回已消费集确认要求，展示影响范围并取得用户明确确认，
再追加 `confirm_consumed: true` 重试；重置成功后刷新 workflow-status，按新的权威动作继续。

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
7. 用户对本批规划满意后进入阶段 3。**用户显式授权全自主时**（如"直接跑完整个流程不用逐步确认"），可跳过批级审阅直接继续

---

## 阶段 3：单集预处理

**触发**：`next_action.type == "prepare_step1"`

根据项目 `generation_mode` 选择 subagent：

- `generation_mode == reference_video` → dispatch `split-reference-video-units`（产出 `drafts/episode_{N}/step1_reference_units.json`）
- 否则（本项目 content_mode == drama）→ dispatch `normalize-drama-script`（产出结构化内容 `drafts/episode_{N}/step1_normalized_script.json`）

dispatch prompt 通用参数：项目名称、项目路径、集数、本集小说文件路径；可选附加说明（用户对本次生成的意见等任何需带给 subagent 的临时上下文，原文透传）。

（两个预处理 subagent 会自行读 project.json + 调用
`mcp__arcreel__get_video_capabilities({})`
拿到模型能力与用户偏好；主 agent 不需要预先注入角色/场景/道具列表或
`supported_durations` / `max_duration` / `max_reference_images` / `default_duration` 等数据。）

**中间文件变更必重生剧本 JSON**：阶段 3 的中间文件被修改或重拆后（无论哪种生成模式、无论首次还是重做），即使 `scripts/episode_{N}.json` 已存在，也必须重新执行阶段 4——剧本 JSON 不会自动跟随中间文件更新，跳过会留下"新中间文件 + 旧 JSON"的陈旧组合。

---

## 阶段 4：JSON 剧本生成

**触发**：

- `next_action.type == "confirm_step1"` → 先完成下述审核 gate，刷新 workflow-status 后再路由
- `next_action.type == "generate_script"` → dispatch 剧本生成

**step1→step2 审核 gate（阻塞）**：阶段 3 的结构化 step1 中间态须经**显式确认**才放行本阶段（三种结构化 step1 变体——drama / narration / reference_video——一律适用；`reference_video` 的 `step1_reference_units.json` 同样须确认，不要跳过。ad 无 step1，不纳入 gate）。两条等价确认路径——用户在 Web 端审阅 / 编辑后确认，或在对话中明确同意进入视觉生成后由你调用 `mcp__arcreel__confirm_script_review({"episode": N})`（全自主模式下按用户总体授权确认）。未确认（或确认后 step1 又被改）时 `generate_episode_script` 会被 gate 拒绝；**存量项目**（升级前已生成过本集剧本）已 grandfather 放行、无需再确认。

**dispatch `create-episode-script` subagent**：传入项目名称、项目路径、集数；可选附加说明（用户对本次生成的意见等任何需带给 subagent 的临时上下文，原文透传）。

---

## 阶段 5：资产设计（character / scene / prop 三类并行）

**触发**：`next_action.type == "generate_asset_sheets"`。空资产 bucket 是阶段 1 的合法完成结果，
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
所有 dispatch 的 subagent 返回后，合并摘要展示给用户，进入阶段间确认。
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

## 阶段 6：分镜图生成（仅 storyboard 模式，含 grid_storyboard）

**触发**：`next_action.type` 为 `"generate_storyboards"` 或 `"generate_grid"`；服务端不会在
reference_video 模式返回这两个动作。

按动作直接选择工具，不二次检查 `generation_mode` 或 `grid_storyboard`：

- `next_action.type == "generate_storyboards"` → dispatch `generate-assets`，调
  `mcp__arcreel__generate_storyboards({"script": target.script_filename, "segment_ids": requested_ids})`
- `next_action.type == "generate_grid"` → dispatch `generate-assets`，调
  `mcp__arcreel__generate_grid({"script": target.script_filename, "scene_ids": requested_ids})`

两条路径都把 `next_action.args` 与 `requested_ids` 原样传给 subagent，由 subagent 按上面映射调用工具。

> **切换 `grid_storyboard` 后的重做**：本阶段的常规触发条件是「缺分镜图」，而用户在设置页切换该开关不会让已有分镜图失效，剧本里也不记录分镜图由哪种装配方式产出——单看缺图会把整集判成已完成。用户在已有分镜图的项目上切换开关后要求按新方式出图时，与其确认要重做的场景范围，再显式带 ID 重生：切到宫格用 `mcp__arcreel__generate_grid({"script": target.script_filename, "scene_ids": [...]})`，切回单图用 `mcp__arcreel__generate_storyboards({"script": target.script_filename, "segment_ids": [...]})`（`script` 必填；ID 列表省略时只补缺图，达不到重做效果）。已生成的视频同样不会自动失效，重出分镜图后需按新图重跑阶段 7 对应场景。

## 阶段 7：视频生成

**触发**：`next_action.type == "generate_videos"`

**dispatch `generate-assets` subagent**：

```text
dispatch `generate-assets` subagent：
  任务类型：video
  项目名称：{project_name}
  工具调用：
    mcp__arcreel__generate_video_episode({"script": target.script_filename})
  验证方式：重新读取 target.script，检查各场景的 video_clip 字段
```

---

## 灵活入口

工作流**不强制从头开始**。根据状态检测结果，自动从正确的阶段开始：

- "分析小说角色" → 只执行阶段 1
- "创建第2集剧本" → 从阶段 2 开始（如果角色已有）
- "继续" → 状态检测找到第一个缺失项
- 指定具体阶段（如"生成分镜图"）→ 直接跳到该阶段

---

## 数据分层

- 角色 / 场景 / 道具完整定义**只存 project.json**，剧本中仅引用名称
- 统计字段（scenes_count、status、progress）**读时计算**，不存储
- 剧集元数据在剧本保存时**写时同步**
