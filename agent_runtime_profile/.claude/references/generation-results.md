# 生成结果契约

所有生成工具（分镜图、宫格、视频、资产图、旁白配音、图片编辑）返回同一个结构，
读结构、不要读文本：文本只是这个结构的一份人读投影。

## 选择：显式 ID 还是补缺

| 传法 | 语义 |
|---|---|
| 传 ID 列表（`segment_ids` / `scene_ids` / `names` / `edits[].id`） | 点名即强制：这些 ID 一律重做，哪怕产物是最新的 |
| 不传 ID | 只补缺（missing-only）：只做产物缺失的单元 |
| 传空数组 `[]` | 非法：不等于「全部」，请求会被拒绝；想补缺就别传这个参数 |

`generate_video_episode` 是整集补缺入口（含 `resume`），它从不强制重做已有片段；
要重做点名的镜头用 `generate_video_scene` / `generate_video_selected`。

**已失效但仍可用的旧产物（stale）会被复用，不会自动重生**——它照样能看、能导出。
用户要求更新时才用显式 ID 点名重做。产物状态读不出来（blocked）的单元报为独立缺口，
绝不当作缺失去重新生成，因为那会把一次损坏变成一次重复计费。

## 结果：逐 ID 分账

```
requested = succeeded ∪ failed ∪ blocked   （三者互斥）
skipped                                     （复用旧产物，不在 requested 内）
```

- **succeeded** — 已做成
- **failed** — 已入队执行过（可能已计费）后失败；也包括入队本身没成的目标，它们的
  `task_state` 是 `not_queued`，从未创建任务、也从未计费
- **blocked** — 请求根本没走到入队（不计费），例如缺依赖、请求不合法、产物状态不可读

批量入队中途中断时，**已经创建的任务不会被撤销**：它们是准入通过的完整付费单元，照常
执行、照常出成片。没轮到的目标带「入队中断」问题码报 `failed`，下次补缺正好只做这些——
不要建议用户重跑整批，那会为已经在跑的单元重复付费。

`items` 里每个 `failed` / `blocked` 项都带：

- `problem.code` — 稳定问题码
- `problem.action` — 闭集的下一步动作（`retry` / `fix_input` / `generate_dependency` /
  `replan_unit` / `configure_provider` / `confirm_request_duration` / …）
- `unit_id` 与（若有）`artifact_key`、`artifact_path`

宫格按**场景 ID** 记账：同一分组的场景共享一张宫格联合图，这张图的入队、任务、
切分结果会投影到它覆盖的每个场景，某一格没落盘只算那一个场景失败。

**按 `code` 与 `action` 决定下一步，不要解析文本判断能不能重试。**

## 四条状态轴分开读

workflow 步骤状态（`plan.steps[].state`）、`task_state`（队列任务）、
`provider_checkpoint`（供应商是否已提交）、`artifact_status`（产物 current/stale/missing/blocked）
互相独立，**分开陈述，不要互相翻译**：

- 任务成功 ≠ 产物匹配当前依据
- 产物缺失 ≠ 任务失败：可能根本没入队（`blocked`，不计费）
- `provider_checkpoint.submitted` 为真表示供应商侧已提交、很可能已计费；`task_state` 为
  `interrupted` 表示没有供应商裁决，盲目重试可能重复计费——一律按 `problem.action` 决定，
  该情形通常交回 `wait_for_task`（任务可能仍在跑并正常落地），不要自行改成 `retry`
- 产物落盘失败时该 ID 记为 `failed`，旧的付费产物原样保留，绝不在文件真正落定前标成就位

产物历史另成一轴：`current` 是当前选中的产物，`stale` 是依据已变但仍在的旧产物，历史版本是此前
付费产出的其它版本。Agent 不得自动删除、覆盖或重生任何已付费产物与历史版本——重做由用户明确决定。

用户问「做完了没有」时，回答落在这四轴上，而不是压成一句「成功了」。计划整体的读法见
[workflow-plan.md](workflow-plan.md)。
