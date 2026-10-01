# 工作流计划契约

`mcp__arcreel__get_workflow_plan` 是编排的**唯一权威入口**。它返回一份只读计划：内容现状、
各 AI 操作此刻能否执行（准入）、有序步骤、活动任务、视频整批准入判定，以及建议的下一步。

**不要在 profile 里另建一张按创作类型或生成模式展开的步骤表。** 六种模式组合（narration /
drama / ad × storyboard / reference_video）之间哪些步骤适用、顺序如何、当前停在哪一步，全部由
计划的 `steps[]` 表达；Agent 只负责执行计划交回的受控动作。

## 查询

```text
mcp__arcreel__get_workflow_plan({
  "episode_id": X,                               // 可选：用户指定某一集时传该集的集 ID
  "confirmed_request_durations": {"E1U1": 8}    // 可选：用户已确认的逐视频单元申请档位（键是 unit ID）
})
```

两个字段都只属于**这一次查询**，服务端不会持久化。因此每次重新查询都要把仍然成立的选择原样
带上；漏带等于把选择撤回。旁白交付方式是项目配置，计划直接读项目，查询里没有这个字段。

调用时机：进入工作流、用户说「继续 / 下一步 / 查看进度」、以及**每次工具或子智能体完成之后**。
`Read` / `Glob` 只用于取执行已选定动作所需的内容，不用于另建一套状态机。不得根据空资产 bucket、
文件名、旧文件存在性或对话记忆覆盖服务端结论。

## 点名与继续

准入属于 AI 操作，不属于流水线位置：一个操作能否执行只看它自己的输入是否成立，与建议的下一步
是不是它无关。据此分两种请求：

- **用户点名一个操作**（如「生成第 2 集的脚本规划」「编写提示词」）：查计划后看
  `plan.status.operations[操作]`。`state == "admitted"` 就执行，即使 `next_action` 指向别处；
  `refused` 时把 `reason` 转述给用户（缺什么、怎样补上），不执行；`not_applicable` 时说明本项目
  没有这个操作。`operations` 只陈述列出的操作；生成类操作的准入维持各入口自身判定
  （ADR 0073），被拒时转述入口返回的理由。
  工具入口用同一份判定：被拒时返回 `operation_not_admitted`，`params.reason` 与计划里的理由码相同。
- **用户说「继续 / 下一步」**：执行 `next_action`。`next_alternatives` 非空表示此处是**分岔**
  （如没有集原文时「手写脚本」与「补集原文后 AI 规划」并列）：把 `next_action` 与各备选讲给用户，
  由用户选定再执行，不替用户挑。

## 读计划

| 字段 | 含义 |
|---|---|
| `steps[]` | 有序步骤。`id` 是稳定步骤名，`state` ∈ `completed` / `ready` / `active` / `blocked` / `pending` / `skipped`，`required=false` 表示该步骤在本项目模式组合下不适用 |
| `steps[].action` | 该步骤自己的受控动作（可能为 null） |
| `steps[].artifacts` | 该步骤的产物时效快照：`current_ids` / `stale_ids` / `missing_ids` 三个 ID 桶，外加集合级 `state`（`current` / `stale` / `partial` / `missing` / `blocked` / `not_applicable`）。`blocked` 是集合级状态，**没有**逐 ID 的 blocked 桶 |
| `steps[].tasks[]` | 该步骤的活动任务观察，每条含 `task_id`、`status`、`provider_checkpoint`、`problem` |
| `steps[].admission` | 视频步骤的整批准入判定（见下） |
| `steps[].problems[]` | 逐条问题，带 `code` 与闭集 `action` |
| `blockers[]` | 项目整体不可用（数据升级失败、project.json 或产物清单读不出、创作类型或生成模式非法），含 `code` / `path` / `reason` |
| `status.issues[]` | 内容本身的数据问题（剧本读不出、字段非法、资产定义损坏等），同样含 `code` / `path` / `reason`；只阻止依赖该内容的操作 |
| `status.content` | 内容现状：集数、整本源文与集原文在不在、目标集的草稿、正式脚本（`present` / `absent` / `invalid`）与条目数、待编写条目、需重新规划的视频单元、被引用却没有资产图的资产；`episode_plan_stale` 为真表示该集集规划 stale、脚本规划待重建 |
| `status.operations` | 列出的 AI 操作的结构准入，键是动作名，值为 `state`（`admitted` / `refused` / `not_applicable`）与稳定理由码 `reason`（见「点名与继续」） |
| `next_action` | 建议的下一步。用户说「继续」时按它路由，不要自己从 `steps[]` 里挑一个更靠前的动作抢跑 |
| `next_alternatives[]` | 分岔处与 `next_action` 并列的备选；非空时由用户选 |

`blockers` 非空时：向用户展示 blockers，**停止一切变更**。`next_action.type == "none"` 且 `steps` 中
`edit` 为 `completed` 表示工作流已走完：查询范围内的每一集都至少有一条剪辑时间线。其余
`next_action.type == "none"` 而 `blockers` 为空的情况，把 `status.issues` 与 `status.content` 讲给用户
（如该集脚本读不出、集规划 stale 待重建、剪辑时间线读不出），用户点名的、准入成立的操作照常可以执行。

## 数据升级失败：修复 → 重试

项目的数据升级（含产物补录）没跑完时，该项目整体阻断。此时计划只交回一件事：

- `blockers[]` 里只有一条 `code == "project_migration_failed"`，`reason` 是升级失败的原文；
- `problems[]` 里只有一条同码问题，`action == "retry_project_migration"`，
  `params.details[]` 逐条给出 `episode`（集 ID）/ `file` / `violation` —— 哪一集、哪个文件、违了什么约；
- `next_action.type == "retry_project_migration"`，`args.details` 同上。

所有生成工具与正式写入工具在这个状态下一律返回同一条问题、不做任何事，也不计费。项目本身
仍可读：`Read` 脚本、看画布上已有的图和视频照常。

处理顺序：

1. 把 `details[]` 逐条讲给用户：哪一集（按项目详情 `episodes[]` 换成标题与播出位置）的哪个文件、违了什么约，
   不要压成一句「升级失败」。
2. 阻断期仍可用的写入工具只有 `mcp__arcreel__patch_project`、`mcp__arcreel__patch_episode_meta`、
   `mcp__arcreel__rename_asset`；`mcp__arcreel__patch_episode_script` 与所有生成工具一律被拒。按明细用
   前三个能修的先修，够不着的（如剧本正文类违约）按第 4 步如实告知用户。
   **没有裸文件写入这条路**，也不要用 `Edit` 直接改正式脚本。
3. 调用 `mcp__arcreel__retry_project_migration` 重跑升级链。它幂等，重复调用不会造成损失。
4. 成功时工具返回新的制作计划，项目解除阻断，照常按 `next_action` 继续；失败时返回新的结构化
   明细，回到第 1 步。修不动时如实告诉用户卡在哪里，不要反复空跑重试。

用户在 Web 项目页点「重试迁移」时，请求文本会被填进对话输入框由用户自己发送——收到它就走上面
这条路径。

## 受控动作表

按 `next_action.type` 路由，把 `target.episode`、`next_action.args` 与 `requested_ids` 带入对应动作。
计划模型总会序列化 `requested_ids`：非空数组表示显式点名；`[]` 表示计划未点名。映射到工具的可选
ID 参数时，前者传入，后者必须**省略该参数**，不得把 `[]` 原样传给工具（工具入参的显式空数组非法）。
`plan.status.target` 提供 `episode`（目标集的集 ID）、`script`、`script_filename`、`source`。工具的
`episode_id` 参数一律取 `next_action.args.episode_id` 或 `target.episode`。两个剧本字段不可互换：
`script` 是相对项目根的剧本路径（`scripts/episode_{集 ID}.json`），用 Read 读剧本内容时用它；
`script_filename` 是剥掉 `scripts/` 前缀的裸文件名，所有 `mcp__arcreel__*` 工具的 `script` 参数用它。

| `next_action.type` | 执行入口 |
|---|---|
| `collect_project_input` | 引导用户在 Web 端补齐项目输入（上传整本源文；ad 填创作灵感或登记商品） |
| `create_episode` | 引导用户在 Web 端新建一集 |
| `draft_selling_points` | 用户要求时起草卖点，经 `mcp__arcreel__patch_project` 写回（ad） |
| `analyze_assets` | 用户要求时 dispatch `analyze-assets` 子智能体 |
| `reset_episode_planning` | `mcp__arcreel__reset_episode_planning`，按 `next_action.args` 原样传参（无 `episode_id` 即全量重置） |
| `plan_episodes` | `mcp__arcreel__plan_episodes` |
| `resolve_draft` | 目标集上有草稿（`args.draft_kind`）：`args.needs_repair` 为真时是写入失败留下的待修复草稿，向用户说明并按其对应操作重跑；为假时是 Agent 的可编辑草稿，按对应 skill 接着完成 |
| `prepare_script_plan` | dispatch `next_action.args.preprocessor` 指名的子智能体 |
| `start_blank_script` | 引导用户在 Web 集页点「从空白开始」，在时间线上逐条手写这一集的正式脚本 |
| `provide_episode_source` | 引导用户为这一集补上集原文（`source/episode_{集 ID}.txt`），之后即可 AI 规划脚本 |
| `confirm_script_plan` | `mcp__arcreel__confirm_script_review` |
| `generate_script` | dispatch `create-episode-script` 子智能体（ad 直接调 `mcp__arcreel__generate_episode_script`） |
| `add_script_items` | 正式脚本为空：引导用户在 Web 端添加条目，或经 `mcp__arcreel__patch_episode_script` 插入 |
| `author_prompts` | 正式剧本里有待编写条目，`requested_ids` 列出这些条目：调 `mcp__arcreel__generate_episode_script`，不传 `entry_ids`，即编写全部待编写条目（见 generate-script skill） |
| `generate_asset_sheets` | dispatch `generate-assets` 子智能体，调用 `mcp__arcreel__generate_assets({"episode_id": <args.episode_id>})` 一次生成本集引用、仍缺资产图的资产。因缺少 description 记为 `blocked` 的资产（`fix_input`）：依据原文写好描述，经 `mcp__arcreel__patch_project` 补上后重跑同一调用；原文没有依据时向用户确认描述，或请用户在 Web 端上传资产图。衍生报 `generation_dependency_failed` / `derivative_owner_sheet_missing` 时先解决本体资产图 |
| `generate_storyboards` | dispatch `generate-assets` 子智能体，调用 `mcp__arcreel__generate_storyboards` 并传 `segment_ids` |
| `generate_grid` | dispatch `generate-assets` 子智能体，调用 `mcp__arcreel__generate_grid`，不传 `scene_ids`（缺失即生成）；联合图就绪后经用户审阅同意，再调 `mcp__arcreel__split_grids` |
| `repair_video_units` | `mcp__arcreel__get_episode_script` + `mcp__arcreel__patch_episode_script` 一次改完，再点名重做 |
| `patch_episode_script` | 计划注入：`next_action.args` 已给 `base_revision` 与逐条 `problems`，一次批量改完 |
| `confirm_request_duration` | 计划注入：见「整批准入判定」 |
| `generate_videos` | 视频生成工具（见 `generate-video` skill） |
| `wait_for_task` | 计划注入：有活动任务，不入队新任务；等待并复查计划 |
| `create_edit_timeline` | 本集视频已齐、还没有剪辑时间线：`mcp__arcreel__create_timeline`（`episode` 取 `next_action.args.episode_id`，`from: "script"`）按脚本机械新建一条，至少有一条剪辑时间线，「剪辑」一步即完成；之后按 `edit-video` skill 剪辑 |
| `retry_project_migration` | 项目数据升级未完成：按明细修复后 `mcp__arcreel__retry_project_migration`（见「数据升级失败」） |
| `none` | `blockers` 非空时展示并停止变更；`steps` 中 `edit` 为 `completed` 时工作流已走完；其余情况讲 `status.issues` 与 `status.content` |

`next_action.args.preprocessor` 是权威的脚本规划子智能体名，**不要自己按创作类型×
`generation_mode` 反推**：服务端在同一张规则表上得出它，profile 侧再推一遍只会造出第二个真相源。

### 整批被拒时交回的逐问题动作

视频整批准入判定被拒时，计划把**第一个问题的 `action`** 直接当成 `next_action.type` 交回，
`next_action.args.admission` 带完整准入结论。因此上表之外还可能收到下面这些动作——它们与
`problems[].action` 同一个闭集，逐视频单元的处理方式一律读各自的 `problems[].action`，不要按
`code` 自己猜：

| `next_action.type` | 执行入口 |
|---|---|
| `fix_input` | 剧本/声明本身不合法：按 `problems[].detail` 定位，经 `mcp__arcreel__patch_episode_script` 改对再重查 |
| `replan_unit` | 视频单元需要重新规划：走 `repair_video_units` 那一行的改法 |
| `generate_dependency` | 缺上游产物（资产图等参考图）：先补齐依赖再重查 |
| `configure_provider` | 当前供应商或档位不支持这次请求：告知用户要改哪项配置，**重试同一请求只会被同样拒绝** |
| `repair_artifact_state` | 产物状态读不出来：报为独立缺口，绝不当作缺失去重生 |
| `retry` | 可安全重发同一请求 |
| `retry_artifact_download` | 产物已在供应商侧生成、只是没取回来：调 `POST /tasks/{id}/retry-download` 接续取件，**不要重发生成请求**——那会再建一个付费任务 |

`retry` 与 `configure_provider` 在不入队新批次之前，先把动作原因说给用户；
凡是会产生新费用的动作，取得用户明确同意再执行。

## 旁白交付

旁白交付方式（`post_production` 后期配音 / `use_tts` TTS 配音）是项目配置，在项目设置里修改；工作流
里没有交付选择这一步。视频请求与它无关：两种方式下视频的申请档位、准入与费用完全相同。

旁白配音不是视频生成的前置条件。`plan.status.artifacts.audio` 只在 TTS 配音项目里报告哪些旁白
配音缺失或 stale；后期配音项目为 `not_applicable`。TTS 项目在首轮自动剪辑时按缺失补齐配音，
项目选择 TTS 即已授权，无需另行确认；用户也可随时要求用 `generate-narration-audio` 生成。
stale 配音仅在用户要求重合成时重新生成。

## 整批准入判定

视频整批请求是**全有或全无**：`steps[].admission.decision` 为 `admitted` 时整批入队；为
`blocked` 或 `confirmation_required` 时**一个任务都不入队**。Web 与 Agent 走同一套准入和同一套
请求选择语义（视频点名须另传 `force: true` 才强制重做 / 不传即只补缺 / 空数组非法），不存在 Agent 专属的宽松通道。

`decision != "admitted"` 时：

- 逐视频单元报告 `admission.units[]`：`unit_id`、是否 `admitted`、`problems[].code`、
  `problems[].action`（下一步动作）。通过的视频单元会带 `generation_batch_admission_withheld`，
  其 `blocked_unit_ids` 指出是被谁挡住的——把这层因果如实说给用户，不要报成它们自己有问题。
- `decision == "confirmation_required"` 时 `admission.confirmation.tiers[]` 给出按申请档位分组的
  视频单元与费用。取得用户确认后，把确认过的档位填进 `confirmed_request_durations` 重查计划；`generate_videos` 重发时带上同一份 `confirmed_request_durations`。
- **不要把整批拆成小批去「先跑通过的那半批」。** 那既绕开了全有或全无，也会在补齐后重复提交
  已经付过费的视频单元。修掉被拒的视频单元，整批重来。

## 四条状态轴分开报告

计划里这四轴的字段分别是 `steps[].state`（步骤进度）、`steps[].tasks[].status`（队列任务）、
`steps[].tasks[].provider_checkpoint`（供应商是否已提交）与 `steps[].artifacts`
（`current_ids` / `stale_ids` / `missing_ids` 与集合级 `state`）。四轴互相独立、分开陈述、
不要互相翻译——读法与逐轴含义见
[generation-results.md](generation-results.md)。

## stale 与历史

- **stale 产物照常可预览、可导出、可参与成片**，服务端会复用它，不会自动重生。
- 是否重做由**用户明确决定**。Agent 不得自动删除、覆盖或重生任何已付费产物，也不得因为
  「看起来旧」就点名重做——视频必须同时点名并传 `force: true` 才强制重做且必然产生费用。
- 产物状态读不出来（`blocked`）的单元报为独立缺口，绝不当作缺失去重新生成：那会把一次损坏
  变成一次重复计费。
- 恢复中断的任务由服务端接回原请求，不重新提交已在供应商侧落定的请求。

逐 ID 结果结构、选择语义与问题码清单见 [generation-results.md](generation-results.md)。
