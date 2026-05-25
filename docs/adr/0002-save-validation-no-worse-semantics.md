---
status: proposed
---

# 剧本保存校验采用「不更坏」语义，资产回写热路径豁免

剧本结构校验（`segment`/`scene`/`unit` 形状、`duration` 范围、必填字段）此前被抽成纯函数后只在归档边界被调用，写盘路径完全不校验——脏数据写时不报、`load_script` 只 `json.load` 也不报，要到 worker 深处解析时才炸。我们决定把这个结构校验器（一处纯函数定义）前移到 Python 写盘统一入口 `_write_script_unlocked`，但**不**采用「永远严格 raise」：写入仅在「改前合法 ∧ 改后非法」时才拒绝（本次写入引入了新的结构错误），改前就已非法的遗留旧剧本照常放行；同时只动 `generated_assets` 的资产回写热路径（`update_scene_asset` / `batch_update_scene_assets` / `update_character_sheet` / 参考视频任务回写）**完全跳过**校验。校验对象是结构层 Pydantic 模型（按 `generation_mode`/顶层键选 `Narration`/`Drama`/`ReferenceVideoScript`），**不是** FS 感知的 `DataValidator`——后者会读磁盘、并拒绝合法的半成品草稿（分镜图尚未生成）。

## Consequences

- 保存有时会接受一个已非法的剧本（改前就坏）。这是刻意的：守卫的职责是「不让本次编辑把脏数据带进系统」，而非为 schema 演进产生的历史遗留背锅；否则用户编辑一个「写入时合法、不满足现行 schema」的旧剧本会突然吃 400，且非因本次编辑非法。`load_script` 不前置 `before` 时（如直连 `save_script` 的 normalize）产出的是完整重算脚本，严格校验即可。
- 资产回写路径跳过校验，意味着 worker 给一个遗留非法剧本贴 `generated_assets` 不会被连带拖死。理由是性能 + 语义：这些是高频热路径、只写 `SkipJsonSchema` 运行时字段，结构不可能因此变坏，没必要每次校验整脚本。
- 校验失败在**写时**当场暴露（人工路径返回错误），而非排队后在执行层才炸。
- 剧本/`project.json` 共有三个写入面：Python API（`save_script` 等，本 ADR 覆盖）、Agent 生成工具（`text_generation`→`ScriptGenerator`，生成时已 Pydantic 校验）、**Agent 裸 `Write`/`Edit`**。最后一面今天只过 PreToolUse 的 JSON 语法 hook（`json.loads` + 弯引号），结构错误照样落盘、绕开本 ADR 的统一入口。已决定将其**收归 ProjectManager**：deny Agent 对 `scripts/*.json` + `project.json` 的裸 `Write`/`Edit`，改由走 `_write_script_unlocked` 的工具承接，使结构校验真正只有一个强制点。该替代 affordance（粒度/协议/profile 调整/精确 deny 范围/工具侧 raw write 的处理）作为独立功能另行设计，本 ADR 暂不展开。
- **不要把它"收紧成永远严格 raise"**：那会让编辑旧剧本回归 400、并给热路径回写加上每次全量校验的开销——这两点正是本决策刻意规避的。
- 与入队侧的校验是两条独立轴：本 ADR 管「写盘」，入队侧的结构校验（provider-agnostic）与能力校验（duration↔supported_durations，执行时解析，见 `docs/adr/0001` 原则）另行落地，依赖 provider 解析收敛（#599）先行。
