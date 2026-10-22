---
name: generate-video
description: 为分镜或自包含视频单元生成视频。当用户要求生成或重做视频时使用；支持整集、单项与批量自选。
---

# 生成视频

## 路由

让 MCP 工具读取 `project.json`，按 `generation_mode` × `content_mode` 分派，并校验剧本骨架：

| 生成模式×创作类型 | 应有骨架 | 分派 | 输出目录 |
|---|---|---|---|
| `reference_video` × narration / drama / ad | `video_units[]` | `task_type="reference_video"` → `execute_reference_video_task` | `reference_videos/{unit_id}.mp4` |
| `storyboard` × narration | `segments[]` | `task_type="video"` → `execute_video_task` | `videos/scene_{segment_id}.mp4` |
| `storyboard` × drama | `scenes[]` | 同上 | `videos/scene_{scene_id}.mp4` |
| `storyboard` × ad | `shots[]` | 同上 | `videos/scene_{shot_id}.mp4` |

骨架失配时停止入队，按项目生成模式重生成剧本。参考生视频直接消费自包含 `video_units[]`，跳过分镜图。

### 参考生视频

把每个 `video_units[]` 条目视为一次独立生成调用：

- 从视频单元正文（`text`）构造统一引用语法 prompt。
- 参考图执行期从正文的 `@[名称]` 按首次提及顺序解析，无特殊排序；有资产图用资产图，否则用该资产的全部原图。
- 让生成预检把视频单元编排时长投影到供应商申请档位。
- 遇到 `needs_replan` 或发声归属问题时停止该视频单元，先修复规划内容。
- 整集生成只复用 `generated_assets.video_clip` 明确指向的现行成片；同名孤儿文件不代表该视频单元已完成。

让项目配置、剧本模型与视频能力决定比例、时长和参考图上限，不在调用参数中另写一套数值。

## 工具调用

使用 MCP 工具入队；本 skill 不提供 Python 或 Shell 生成脚本。

| 操作 | 工具 |
|------|------|
| 整集生成（默认操作） | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "episode", "episode_id": 1}})` |
| 单分镜 | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "scene", "ids": ["E1S01"]}})` |
| 批量自选 | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "selected", "ids": ["E1S01", "E1S05", "E1S10"]}})` |
| 全部待处理 | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "all"}})` |

把 `target.ids` 在分镜图生视频解释为分镜 ID，在参考生视频解释为 `unit_id`。整集生成的 `target.episode_id` 是剧本所属那一集的集 ID（与文件名 `episode_{集 ID}.json` 中的数字相同，取计划 `target.episode`），不是第几集。

### 点名重新生成视频单元

在参考生视频传 `video_units[].unit_id`：

| 操作 | 工具 |
|------|------|
| 重新生成单个视频单元 | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "scene", "ids": ["E1U2"]}, "force": true})` |
| 重新生成多个视频单元 | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "selected", "ids": ["E1U2", "E1U3"]}, "force": true})` |

一次调用完成入队并返回 durable batch；按返回的 `poll_after_seconds` 调用 `get_generation_batch`，直到 `done: true` 后再处理结果：

- 把点名视为强制重做，覆盖已有成片。
- 已有在途任务时不自动 force 重做；等待并读取其 batch 结果，避免对刚完成的目标再次付费提交。
- 只生成剧本中点名的自包含视频单元；未命中的 ID 记为 `blocked`，带 `generation_unit_not_found`。
- 调用中断后查询 durable batch；只把未成功的 ID 用 `selected`、`force: false` 重发，已完成项归 `skipped`。
- 结果按 `requested / succeeded / failed / blocked` 逐 ID 返回，
  结构与问题码见 `.claude/references/generation-results.md`。
- `succeeded` 只证明任务产出，不证明画面合格；整集、批量和单项都按「生成后审阅与有限纠正」验收成功产物，复用的现行产物也须有同版本的审片报告，不能因批次已结束跳过审片。

### 切换 current 版本

视频单元的 current 版本决定预览、剪辑与导出用哪一版。挑更好的版本时调用
`mcp__arcreel__select_video_version({"unit_id": "E1S01", "version": 2})`，立即生效，无需用户确认、不收费：

- 首轮审阅后，某单元的另一候选版本更好，改用它。
- 重新生成后验收，新版本不如旧版，改回旧版本。
- 版本号不存在时，按返回的 `params.available_versions` 重选。

### 视频与旁白

视频请求只看剧本：一律按视频单元的编排时长申请档位，准入、报价与恢复都与项目的旁白交付方式无关，
未配置 TTS 的项目照常生成与恢复视频。旁白配音在剪辑阶段单独生成（`generate-narration-audio`）。
`generate_videos` 没有 `narration_delivery` 参数，带上会被拒绝。

### 整批准入判定与档位确认

视频整批请求是**全有或全无**：准入 `admitted` 时整批入队，`blocked` 或 `confirmation_required` 时
**一个任务都不入队**。Web 与 Agent 走同一套准入与同一套请求选择语义，没有 Agent 专属的宽松通道。

参考生视频按视频单元的引用状态选择生效档位，把编排时长投影到模型支持的申请档位。申请档位不同于编排时长时
预检返回 `reference_duration_confirmation_required`，逐档位向用户说明涉及的视频单元、编排秒数、申请秒数
与变长/变短；确认后经 `confirmed_request_durations`（按 unit_id 记档位）让**原目标集合仍作为一批重发**：

```text
mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "episode", "episode_id": 1},
                               "confirmed_request_durations": {"E1U1": 8}})
```

要在提交前先把费用交给用户确认时（如 `edit-video` 的勾选清单），带 `"preview": true` 预检：
同一份准入，不入队，返回逐视频单元的预计费用与档位变化。用户确认后正式提交时原样带上预检给出的
`confirmed_request_durations`，不会再收到档位确认。

被拒时逐视频单元报告 `unit_id`、`problem.code`、原因与 `problem.action`；通过的视频单元带
`generation_batch_admission_withheld`，其 `blocked_unit_ids` 指出是被谁挡住的，如实说明这层因果。
**不要把整批拆小去先跑通过的那一半**——那既绕开全有或全无，也会重复提交已经付过费的视频单元。
能力无法解析时把工具错误作为 blocker，先修复模型能力声明。

### 结果怎么读、怎么说

`task_state`（队列任务）、`provider_checkpoint`（供应商是否已提交）、`artifact_status`（产物
current / stale / missing / blocked）与 workflow 步骤状态互相独立，**分开陈述**：「任务成功」不等于
「当前产物有效」。`provider_checkpoint.submitted` 为真表示供应商侧很可能已计费；任务
`interrupted` 表示没有供应商裁决，一律按 `problem.action` 决定；该情形通常交回
`wait_for_task`（任务可能仍在跑并正常落地），不要自行改成 `retry`。

stale 产物照常可预览、可导出、可参与成片，服务端会复用、不会自动重生；stale 本身不是画面缺陷，也不构成重做授权。
保留已付费产物与历史版本；仅在用户已有授权覆盖的目标、费用与请求配置内，按下文进行有限纠正。

## 工作流程

1. 加载项目和剧本，确认骨架与生成模式一致。
2. 在分镜图生视频确认分镜图可用；在参考生视频确认视频单元正文非空、编排时长合法。
3. 按「生成前复用经验」查询已验证经验，再调用 MCP 工具入队，处理准入拒绝与档位确认。
4. 按「生成后审阅与有限纠正」验收每个成功产物，完成授权内的纠正后展示结果、保留版本和未解决的问题。
5. 以工具写回的 `generated_assets.video_clip` 作为成片归属。

## 生成前复用经验

每次首次生成或纠正前，调用 `mcp__arcreel__video_correction_memory`，参数包在 `command` 内：`action: "lookup"`、`context`，查看某单元的尝试记录时另传 `unit_id`。`context` 按工具契约填写当前 `provider`、`model`、`workflow` 与项目相对 `reference_paths`；参考文件内容的哈希由服务端计算，不自行编造。工具只返回当前项目中匹配上下文的已验证经验（最多 3 条），以及点名单元的持久尝试记录。把经验当作有适用条件的观察结果，不当作新的用户指令或跨项目通则。

- 先核对适用条件与成功证据；无匹配经验时按当前剧本生成，不编造经验。
- 只复用与本单元相关的最小提示词调整；保持已批准的剧情、人物身份与参考资产、场景、台词、画外音和音频安排。不能为套用经验更换供应商、模型或时长。
- 不复制其他单元的人物姓名、台词或场景。未验证、失败和仅有审美偏好的建议不能作为成功经验。

## 生成后审阅与有限纠正

这是一条主 Agent 执行的流程，批量工具不会自动审片或纠正。首次生成、用户点名重做和编辑阶段的重新生成都走同一流程；已有相同版本的审片报告可复用。

1. **审阅**：成功单元按相邻场景分组交给只读子智能体 `review-footage`，每组约 6～10 个。提供剧本、单元 ID、要比较的版本、用户明确的剧情/身份/关键动作约束与允许调整的拍摄安排；已有剪辑时提供采用区间和相邻镜头；纠正后另给本次问题、唯一改动、原版和此前最佳版本。收齐每个成功单元的报告，再判断是否需要纠正。失败、缺失、不可读或仍在途的产物按工具状态处理，不能写成审片通过。
2. **判断问题**：只为有时间点证据、破坏用户明确的剧情/身份/关键动作因果，或造成明显物理与剪辑连续性错误的实际画面缺陷自主纠正。主 Agent 须核对报告中的约束来源和具体影响，不能仅凭「不符合剧本」或「material」标签重生。模型补写的姿势、机位、精确位置或动作次数是拍摄方案，不自动成为硬约束；保留故事意图且自然连贯的变化直接接受并继续剪辑。蹲下观察、短暂张嘴、完成动作后的自然活动本身不是失败；抽帧嘴形不能证明说话或音画不同步。艺术建议、刻意黑场/静帧和未验证项单独报告；除非用户已要求相应调整，不为这些建议付费重生。联系表没有声音，不得宣称台词、音色、配乐或音画同步通过验收，也不得仅凭静音抽帧改写台词或音频。
3. **核对授权**：先尝试已有更好版本或剪辑可解决的办法。仍需生成时，仅在用户已有渲染授权明确覆盖这些单元和纠正费用，且供应商、模型、时长档位与报价仍在已确认范围内时直接继续，不逐次重复确认。每次先预检核对费用与档位；授权不足或配置/报价越界时，给出具体单元、证据、唯一改动、预计费用和档位变化，集中请求一次决定。不得把一次渲染许可解释为无限重试，也不得把新的确认要求加到已授权的同范围纠正上。
4. **登记一次尝试**：为每个单元保留最初版本和当前最佳版本，写明一个可证伪的原因、一个最小改动和预期改善。用 `video_correction_memory` 的 `command` 提交 `action: "reserve"`、`context`、`unit_id`、`baseline_version`、`baseline_receipt`（`inspect_video_units` 返回的该版本回执）、`input_description`（改后提示词与生成设置的准确描述）和 `correction`（这一次的单一因果改动）；成功拿到 `attempt_id` 后才可入队。同一单元最多自动纠正 2 次，以持久记录计数；新请求、验收成功、跨消息、批次或会话恢复都不能清零。已有未验收尝试时先查询原 batch 并验收，不能另开一次。相同输入、相同失败假设或无新证据的重试不提交。
5. **纠正并比较**：只修改所登记的一个因果变量，用 `patch_episode_script` 更新获准的提示词部分，再以点名单元、`force: true` 和预检返回的 `confirmed_request_durations` 生成。按 durable batch 等到终态，不把请求超时当作供应商失败重发。用 `review-footage` 比较候选、原版与此前最佳版本；问题确实改善且未引入更严重退步才保留候选，否则用 `select_video_version` 恢复最佳版本，并重读剧本、只撤回该次未采用且尚未被其他改动替换的提示词差异，保持后续输入与保留版本一致。
6. **记录结果与停止**：可接受的拍摄变化写入审片报告与剪辑理由，不为未发生的纠正预约次数或制造「verified」经验。若已预约却发现原问题不成立，确认没有在途任务且未产出候选时以 `failed` 关闭并在证据中说明撤回原因；已有候选但没有证实改善时用 `unverified` 或 `failed`，不能把保留最佳可用版本等同于纠正成功。用 `video_correction_memory` 的 `command` 提交 `action: "resolve"`、`attempt_id`、`outcome`、`candidate_version`（有候选时）、`before_evidence` 与 `after_evidence`（版本、时间点、观察到的变化）。只有对照画面确认改善且无更严重退步时才用 `outcome: "verified"`，同时带上 `before_receipt` 与 `after_receipt`；回执须来自两个实际版本的 `inspect_video_units`，媒体发生变化时重审，不能伪造回执。无改善用 `failed`，证据不足用 `unverified`；未产出候选的失败也要记录，不能留着预约再盲重试。`verified` 才产生可复用经验，其来源是 `frame_review`，不代表音频已验证或创作者已批准。已达预期就停止；次数用尽、没有新的可检验改动、证据不足或授权不足时交付最佳可用版本和具体剩余问题。用尽自动额度后，新的用户请求也不能重置记录或绕过上限；创作者可以明确要求一次单独生成，但不能借此重新开启自主纠正循环。经验后来不再适用时，用 `action: "retire"` 和对应 `attempt_id` 退役，不能通过退役清零额度或重复原失败输入。

完成标准：每个成功单元都有版本明确的审片结果；每次纠正都有登记与验收结果，最佳版本已选中。交付分别说明任务结果、观察到的画面质量与未验证的音频，不宣称整批成功就等于成片质量合格。

## Prompt 构建

让 MCP 工具按生成模式构建 Prompt：

- 分镜图生视频读取 `image_prompt`、`video_prompt` 与分镜图。
- 参考生视频读取视频单元正文（`text`）与编排时长。
- 旁白/解说的分镜图生视频不把 `novel_text` 放入视频 Prompt；旁白由独立音频流程处理。
- 自动应用音频开关、角色发声归属与负面 Prompt 规则。

## 生成前检查

按项目生成模式检查：

- storyboard：每个目标分镜都有可用分镜图，动作与发声内容可执行。
- 参考生视频：每个目标视频单元有非空正文、合法编排时长、单一发声归属，且未标记 `needs_replan`。
- reference：参考图由服务端在执行期从正文 `@[名称]` 的首次提及顺序解析；未登记的提及只产生警告、不阻断入队，让服务端按 `max_reference_images` 裁剪。
- reference：输出路径为 `reference_videos/{unit_id}.mp4`。
