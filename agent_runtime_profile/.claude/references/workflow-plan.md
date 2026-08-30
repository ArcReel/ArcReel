# 工作流计划契约

`mcp__arcreel__get_workflow_plan` 是编排的**唯一权威入口**。它返回一份只读计划：有序步骤、
阻断原因、活动任务、视频整批准入判定，以及唯一的下一动作。

**不要在 profile 里另建一张按创作类型或生成模式展开的步骤表。** 六种模式组合（narration /
drama / ad × storyboard / reference_video）之间哪些步骤适用、顺序如何、当前停在哪一步，全部由
计划的 `steps[]` 表达；Agent 只负责执行计划交回的受控动作。

## 查询

```text
mcp__arcreel__get_workflow_plan({
  "episode": N,                                  // 可选：用户指定集数时传
  "narration_delivery": "post_production" | "use_tts",  // 可选：本次旁白交付选择
  "confirmed_request_durations": {"E1U1": 8}    // 可选：用户已确认的逐视频单元申请档位（键是 unit ID）
})
```

三个字段都只属于**这一次查询**，服务端不会持久化。因此每次重新查询都要把仍然成立的选择原样
带上；漏带等于把选择撤回。

调用时机：进入工作流、用户说「继续 / 下一步 / 查看进度」、以及**每次工具或子智能体完成之后**。
`Read` / `Glob` 只用于取执行已选定动作所需的内容，不用于另建一套状态机。不得根据空资产 bucket、
文件名、旧文件存在性或对话记忆覆盖服务端结论。

## 读计划

| 字段 | 含义 |
|---|---|
| `steps[]` | 有序步骤。`id` 是稳定步骤名，`state` ∈ `completed` / `ready` / `active` / `blocked` / `pending` / `skipped`，`required=false` 表示该步骤在本项目模式组合下不适用 |
| `steps[].action` | 该步骤自己的受控动作（可能为 null） |
| `steps[].artifacts` | 该步骤的产物时效快照：`current_ids` / `stale_ids` / `missing_ids` 三个 ID 桶，外加集合级 `state`（`current` / `stale` / `partial` / `missing` / `blocked` / `not_applicable`）。`blocked` 是集合级状态，**没有**逐 ID 的 blocked 桶 |
| `steps[].tasks[]` | 该步骤的活动任务观察，每条含 `task_id`、`status`、`provider_checkpoint`、`problem` |
| `steps[].admission` | 视频步骤的整批准入判定（见下） |
| `steps[].problems[]` | 逐条问题，带 `code` 与闭集 `action` |
| `blockers[]` | 阻断项，含 `code` / `path` / `reason` |
| `next_action` | **唯一**下一动作。按它路由，不要自己从 `steps[]` 里挑一个更靠前的动作抢跑 |

`next_action.type == "none"` 或 `blockers` 非空时：向用户展示 blockers，**停止一切变更**。

## 数据升级失败：修复 → 重试

项目的数据升级（含产物补录）没跑完时，该项目整体阻断。此时计划只交回一件事：

- `blockers[]` 里只有一条 `code == "project_migration_failed"`，`reason` 是升级失败的原文；
- `problems[]` 里只有一条同码问题，`action == "retry_project_migration"`，
  `params.details[]` 逐条给出 `episode` / `file` / `violation` —— 哪一集、哪个文件、违了什么约；
- `next_action.type == "retry_project_migration"`，`args.details` 同上。

所有生成工具与正式写入工具在这个状态下一律返回同一条问题、不做任何事，也不计费。项目本身
仍可读：`Read` 脚本、看画布上已有的图和视频照常。

处理顺序：

1. 把 `details[]` 逐条讲给用户：哪一集的哪个文件、违了什么约，不要压成一句「升级失败」。
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
`plan.status.target` 提供 `episode`、`script`、`script_filename`、`source`。两个剧本字段不可互换：
`script` 是相对项目根的剧本路径（`scripts/episode_N.json`），用 Read 读剧本内容时用它；
`script_filename` 是剥掉 `scripts/` 前缀的裸文件名，所有 `mcp__arcreel__*` 工具的 `script` 参数用它。

| `next_action.type` | 执行入口 |
|---|---|
| `collect_project_input` | 引导用户在 Web 端补齐项目输入 |
| `draft_selling_points` | 起草卖点后经 `mcp__arcreel__patch_project` 写回（ad） |
| `analyze_assets` | dispatch `analyze-assets` 子智能体 |
| `reset_episode_planning` | `mcp__arcreel__reset_episode_planning`，按 `next_action.args` 传参 |
| `plan_episodes` | `mcp__arcreel__plan_episodes` |
| `prepare_script_plan` | dispatch `next_action.args.preprocessor` 指名的子智能体 |
| `confirm_script_plan` | `mcp__arcreel__confirm_script_review` |
| `generate_script` | dispatch `create-episode-script` 子智能体（ad 直接调 `mcp__arcreel__generate_episode_script`） |
| `generate_asset_sheets` | dispatch `generate-assets` 子智能体，逐类型调用 `mcp__arcreel__generate_assets` 并传 `names` |
| `generate_storyboards` | dispatch `generate-assets` 子智能体，调用 `mcp__arcreel__generate_storyboards` 并传 `segment_ids` |
| `generate_grid` | dispatch `generate-assets` 子智能体，调用 `mcp__arcreel__generate_grid` 并传 `scene_ids` |
| `repair_video_units` | `mcp__arcreel__get_episode_script` + `mcp__arcreel__patch_episode_script` 一次改完，再点名重做 |
| `patch_episode_script` | 计划注入：`next_action.args` 已给 `base_revision` 与逐条 `problems`，一次批量改完 |
| `choose_narration_delivery` | 计划注入：见「旁白交付」 |
| `confirm_request_duration` | 计划注入：见「整批准入判定」 |
| `generate_videos` | 视频生成工具（见 `generate-video` skill） |
| `wait_for_task` | 计划注入：有活动任务，不入队新任务；等待并复查计划 |
| `export` | 引导用户在 Web 端导出 |
| `retry_project_migration` | 项目数据升级未完成：按明细修复后 `mcp__arcreel__retry_project_migration`（见「数据升级失败」） |
| `none` | 展示 `blockers` 并停止变更 |

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
| `generate_tts` / `regenerate_tts` | 缺旁白音频 / 依据已变：经 `generate-narration-audio` 合成后重查 |
| `configure_provider` | 当前供应商或档位不支持这次请求：告知用户要改哪项配置，**重试同一请求只会被同样拒绝** |
| `repair_artifact_state` | 产物状态读不出来：报为独立缺口，绝不当作缺失去重生 |
| `retry` | 可安全重发同一请求 |
| `retry_artifact_download` | 产物已在供应商侧生成、只是没取回来：调 `POST /tasks/{id}/retry-download` 接续取件，**不要重发生成请求**——那会再建一个付费任务 |

`retry` 与 `configure_provider` 在不入队新批次之前，先把动作原因说给用户；
凡是会产生新费用的动作，取得用户明确同意再执行。

## 旁白交付

叙述旁白有两种交付方式，**每次视频请求逐次选择、从不持久化**：

| 选项 | 含义 |
|---|---|
| `post_production` | 后期配音：视频照常生成，旁白留到剪映等后期工具里补 |
| `use_tts` | 使用当前 TTS：把已生成的旁白音频作为本次请求的依据 |

参考生视频同样要做交付选择：两种生成模式跳过哪些步骤见
[generation-modes.md](generation-modes.md)。

计划给出 `next_action.type == "choose_narration_delivery"` 时：

1. 向用户**显式说明**这次要发起的是叙述旁白视频请求，列出两个选项及各自后果，请其选择。
2. 用户选 `post_production` → 带 `narration_delivery: "post_production"` 重查计划，继续。
3. 用户选 `use_tts` → 先**显式生成并让用户试听**旁白音频（`generate-narration-audio` skill），
   再带 `narration_delivery: "use_tts"` 重查计划，按返回的问题码处理：

本字段在计划查询上可选，在 `generate_videos` 上**必填**：省略或写错值一律返回工具错误、
不入队任何任务，也不退回后期配音。凑够必填项不等于做过选择——没问过用户就不要自己填一个值。

每条问题的 `action` 是权威处理方式，下表只是常见码的说明；**照 `problems[].action` 执行，
不要按 `code` 自己推**：

| `code` | `action` | 处理 |
|---|---|---|
| `tts_missing` | `generate_tts` | 先生成旁白配音，再重查 |
| `tts_stale` | `regenerate_tts` | 依据已变，重新合成该段再重查；旧音频保留 |
| `tts_duration_unavailable` | `regenerate_tts` | 时长读不出来，按重新合成处理 |
| `tts_generating` | `wait_for_task` | 已有旁白任务在跑，**不要再提交一次**，等待后重查 |
| `tts_conflicts_with_active_narrated_video` | `wait_for_task` | 该视频单元有带旁白的视频任务在跑，等待后重查 |
| `tts_not_applicable` | `fix_input` | 该视频单元没有叙述旁白，改选 `post_production` |
| `tts_state_unavailable` | `repair_artifact_state` | 产物状态读不出来，报告缺口，不当作缺失去重生 |
| `tts_not_configured` | `configure_provider` | 见下 |

**未配置 TTS 时默认走后期配音。** `tts_not_configured` 只是「这次选了 TTS 但没有可用供应商」
的事实，不是工作流缺口，也不拦导出。此时告诉用户后期配音方式照常可用、视频不受影响，
**不要建议用户为了继续做视频去配置 TTS 供应商**；只有用户主动想要 in-app 旁白时才说明去哪配。

## 整批准入判定

视频整批请求是**全有或全无**：`steps[].admission.decision` 为 `admitted` 时整批入队；为
`blocked` 或 `confirmation_required` 时**一个任务都不入队**。Web 与 Agent 走同一套准入和同一套
请求选择语义（视频点名须另传 `force: true` 才强制重做 / 不传即只补缺 / 空数组非法），不存在 Agent 专属的宽松通道。

`decision != "admitted"` 时：

- 逐视频单元报告 `admission.units[]`：`unit_id`、是否 `admitted`、`problems[].code`、
  `problems[].action`（下一步动作）。通过的视频单元会带 `generation_batch_admission_withheld`，
  其 `blocked_unit_ids` 指出是被谁挡住的——把这层因果如实说给用户，不要报成它们自己有问题。
- `decision == "confirmation_required"` 时 `admission.confirmation.tiers[]` 给出按申请档位分组的
  视频单元与费用。取得用户确认后，把确认过的档位填进 `confirmed_request_durations`、连同仍成立的
  `narration_delivery` 一起重查计划；同一对参数在 `generate_videos` 重发时同样要带全，
  后者漏带 `narration_delivery` 会直接失败。
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
