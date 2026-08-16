# 工作流计划契约

`mcp__arcreel__get_workflow_plan` 是编排的**唯一权威入口**。它返回一份只读计划：有序步骤、
阻断原因、活动任务、视频批量准入结论，以及唯一的下一动作。

**不要在 profile 里另建一张按内容模式或生成路线展开的步骤表。** 六种模式组合（narration /
drama / ad × storyboard / reference_video）之间哪些步骤适用、顺序如何、当前停在哪一步，全部由
计划的 `steps[]` 表达；agent 只负责执行计划交回的受控动作。

## 查询

```json
mcp__arcreel__get_workflow_plan({
  "episode": N,                                  // 可选：用户指定集数时传
  "narration_delivery": "post_production" | "use_tts",  // 可选：本次旁白交付选择
  "confirmed_request_durations": {"unit_id": 8}  // 可选：用户已确认的逐 unit 申请档位
})
```

三个字段都只属于**这一次查询**，服务端不会持久化。因此每次重新查询都要把仍然成立的选择原样
带上；漏带等于把选择撤回。

调用时机：进入工作流、用户说「继续 / 下一步 / 查看进度」、以及**每次工具或 subagent 完成之后**。
`Read` / `Glob` 只用于取执行已选定动作所需的内容，不用于另建一套状态机。不得根据空资产 bucket、
文件名、旧文件存在性或对话记忆覆盖服务端结论。

## 读计划

| 字段 | 含义 |
|---|---|
| `steps[]` | 有序步骤。`id` 是稳定步骤名，`state` ∈ `completed` / `ready` / `active` / `blocked` / `pending` / `skipped`，`required=false` 表示该步骤在本项目模式组合下不适用 |
| `steps[].action` | 该步骤自己的受控动作（可能为 null） |
| `steps[].artifacts` | 该步骤的产物时效快照（`current` / `stale` / `missing` / `blocked` 分账） |
| `steps[].tasks[]` | 该步骤的活动任务观察，每条含 `task_id`、`status`、`provider_checkpoint`、`problem` |
| `steps[].admission` | 视频步骤的批量准入结论（见下） |
| `steps[].problems[]` | 逐条问题，带 `code` 与闭集 `action` |
| `blockers[]` | 阻断项，含 `code` / `path` / `reason` |
| `next_action` | **唯一**下一动作。按它路由，不要自己从 `steps[]` 里挑一个更靠前的动作抢跑 |

`next_action.type == "none"` 或 `blockers` 非空时：向用户展示 blockers，**停止一切变更**。

## 受控动作表

按 `next_action.type` 路由，把 `target.episode`、`next_action.args` 与 `requested_ids` 原样带入。
`plan.status.target` 提供 `episode` 与 `script_filename`。

| `next_action.type` | 执行入口 |
|---|---|
| `collect_project_input` | 引导用户在 Web 端补齐项目输入 |
| `draft_selling_points` | 起草卖点后经 `mcp__arcreel__patch_project` 写回（ad） |
| `analyze_assets` | dispatch `analyze-assets` subagent |
| `reset_episode_planning` | `mcp__arcreel__reset_episode_planning`，按 `next_action.args` 传参 |
| `plan_episodes` | `mcp__arcreel__plan_episodes` |
| `prepare_step1` | dispatch `next_action.args.preprocessor` 指名的 subagent |
| `confirm_step1` | `mcp__arcreel__confirm_script_review` |
| `generate_script` | dispatch `create-episode-script` subagent（ad 直接调 `mcp__arcreel__generate_episode_script`） |
| `generate_asset_sheets` | `mcp__arcreel__generate_assets`，逐类型传 `names` |
| `generate_storyboards` | `mcp__arcreel__generate_storyboards`，传 `segment_ids` |
| `generate_grid` | `mcp__arcreel__generate_grid`，传 `scene_ids` |
| `repair_video_units` | `mcp__arcreel__get_episode_script_revision` + `mcp__arcreel__patch_episode_script` 一次改完，再点名重做 |
| `patch_episode_script` | 计划注入：`next_action.args` 已给 `expected_revision` 与逐条 `problems`，一次批量改完 |
| `choose_narration_delivery` | 计划注入：见「旁白交付」 |
| `confirm_request_duration` | 计划注入：见「批量准入」 |
| `generate_videos` | 视频生成工具（见 `generate-video` skill） |
| `wait_for_task` | 计划注入：有活动任务，不入队新任务；等待并复查计划 |
| `export` | 引导用户在 Web 端导出 |
| `none` | 展示 `blockers` 并停止变更 |

`next_action.args.preprocessor` 是权威的预处理 subagent 名，**不要自己按 `content_mode` ×
`generation_mode` 反推**：服务端在同一张规则表上得出它，profile 侧再推一遍只会造出第二个真相源。

## 旁白交付

叙述旁白有两条交付路线，**每次视频请求逐次选择、从不持久化**：

| 选项 | 含义 |
|---|---|
| `post_production` | 后期配音：视频照常生成，旁白留到剪映等后期工具里补 |
| `use_tts` | 使用当前 TTS：把已生成的旁白音频作为本次请求的依据 |

`generation_mode == "reference_video"` **只跳过分镜图**这一步（计划里 `storyboard` 步骤
`required=false`），**不跳过 audio**：`narration_delivery` 步骤在两条路线上都适用。参考路线没有
按段批量 TTS 的入口，但每个叙述旁白 unit 的交付选择照样要做。

计划给出 `next_action.type == "choose_narration_delivery"` 时：

1. 向用户**显式说明**这次要发起的是叙述旁白视频请求，列出两个选项及各自后果，请其选择。
2. 用户选 `post_production` → 带 `narration_delivery: "post_production"` 重查计划，继续。
3. 用户选 `use_tts` → 先**显式生成并让用户试听**旁白音频（`generate-narration-audio` skill），
   再带 `narration_delivery: "use_tts"` 重查计划，按返回的问题码处理：

| `code` | 处理 |
|---|---|
| `tts_missing` | 先生成旁白音频，再重查 |
| `tts_stale` | 依据已变，重新合成该段再重查；旧音频保留 |
| `tts_not_applicable` | 该 unit 没有叙述旁白，改选 `post_production` |
| `tts_duration_unavailable` / `tts_state_unavailable` | 产物读不出来，报告缺口，不当作缺失去重生 |
| `tts_not_configured` | 见下 |

**未配置 TTS 时默认走后期配音。** `tts_not_configured` 只是「这次选了 TTS 但没有可用供应商」
的事实，不是工作流缺口，也不拦导出。此时告诉用户后期配音这条路照常可用、视频不受影响，
**不要建议用户为了继续做视频去配置 TTS 供应商**；只有用户主动想要 in-app 旁白时才说明去哪配。

## 批量准入

视频批量请求是**全有或全无**：`steps[].admission.decision` 为 `admitted` 时整批入队；为
`blocked` 或 `confirmation_required` 时**一个任务都不入队**。Web 与 agent 走同一套准入和同一套
请求选择语义（点名即强制重做 / 不传即只补缺 / 空数组非法），不存在 agent 专属的宽松通道。

`decision != "admitted"` 时：

- 逐 unit 报告 `admission.units[]`：`unit_id`、是否 `admitted`、`problems[].code`、
  `problems[].action`（下一步动作）。通过的 unit 会带 `generation_batch_admission_withheld`，
  其 `blocked_unit_ids` 指出是被谁挡住的——把这层因果如实说给用户，不要报成它们自己有问题。
- `decision == "confirmation_required"` 时 `admission.confirmation.tiers[]` 给出按申请档位分组的
  unit 与费用。取得用户确认后，把确认过的档位填进 `confirmed_request_durations` 重查计划。
- **不要把整批拆成小批去「先跑通过的那半批」。** 那既绕开了全有或全无，也会在补齐后重复提交
  已经付过费的 unit。修掉被拒的 unit，整批重来。

## 四条状态轴分开报告

`workflow`（步骤进度）、`task`（队列任务）、`provider_checkpoint`（供应商是否已提交）、
`artifact`（产物 current / stale / missing / blocked）互相独立，**分开陈述，不要互相翻译**：

- 「任务成功」不等于「当前产物有效」。任务成功而产物 `stale`，说明依据变了、产物还在。
- 「产物缺失」不等于「任务失败」。可能根本没入队（`blocked`，不计费）。
- `provider_checkpoint.submitted == true` 表示供应商侧已提交、很可能已计费；任务状态
  `interrupted` 表示没有供应商裁决，盲目重试可能重复计费——按 `problem.action` 决定，
  `resume` 与 `retry` 不可互换。
- 产物历史另成一轴：`current` 是当前选中的产物，`stale` 是依据已变但仍在的旧产物，
  历史版本是此前付费产出的其它版本。

用户问「做完了没有」时，回答要落在这四轴上，而不是压成一句「成功了」。

## stale 与历史

- **stale 产物照常可预览、可导出、可参与成片**，服务端会复用它，不会自动重生。
- 是否重做由**用户明确决定**。agent 不得自动删除、覆盖或重生任何已付费产物，也不得因为
  「看起来旧」就点名重做——点名即强制重做且必然产生费用。
- 产物状态读不出来（`blocked`）的单元报为独立缺口，绝不当作缺失去重新生成：那会把一次损坏
  变成一次重复计费。
- 恢复中断的任务走 `resume` 语义，不重新提交已在供应商侧落定的请求。

逐 ID 结果结构、选择语义与问题码清单见 [generation-results.md](generation-results.md)。
