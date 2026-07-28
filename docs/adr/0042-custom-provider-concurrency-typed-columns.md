---
status: accepted
---

# 自定义供应商并发上限用按 lane 命名的定型列存储，不用 JSON / kv / 子表

内置供应商的并发上限已走 `provider_credential` / `ProviderConfig` 的 `image_max_workers` / `video_max_workers` / `audio_max_workers` 配置键，由 `CapacityTable.from_db` 装载、按 lane 投影成容量表。自定义供应商（`custom_provider` 表，运行时 id 形如 `custom-{id}`）此前在 `from_db` 里一律套全局默认，用户无法为某个自定义供应商单独限并发——与内置供应商不对称。

我们决定**给 `custom_provider` 表新增按 lane 命名的定型列** `image_max_workers` / `video_max_workers` / `audio_max_workers`（nullable Integer），`NULL` 表示「未设置 → 走全局默认」。`CapacityTable.from_db` 装载自定义供应商时逐列取值，`NULL` 即回退全局默认（替代原先一律套全局默认的逻辑）；投影仍走 `_lane_limits`，复用三层回退切片建立的语义。自定义供应商不在内置注册表 `PROVIDER_REGISTRY`，没有「供应商声明默认」中间层，故其回退为两层（用户列值 → 全局默认），而非内置供应商的三层。**明确不采用**：① 通用 JSON `extra` 列（要补逐字段读改写与校验管线，单字段并发读改写复杂，ADR 0016 已嫌弃，本场景仅三个标量上限不划算）；② KV 配置表（自定义供应商配置不进 `ProviderConfig` 的预置供应商 KV 体系，复用要给 resolver 开特例、绕过本表的单一真相）；③ 并发字段子表（为三个标量上限引入一张表 + join，机械量最大）。

这是 YAGNI 取舍：当前只需三条固定 lane 的整数上限，字段集稳定、与内置供应商对称，定型列直接、可索引、迁移最简，与 ADR 0037（内置 provider 多 secret 定型列）同构——同样是「字段集已知且稳定时优先定型列、把 JSON / 子表留给真正动态的场景」。

## Consequences

- **列稀疏显式接受**：三列对未配并发的自定义供应商恒为 `NULL`（多数行如此）；靠本 ADR 与 `from_db` 的两层回退注释解释，不视作待清理的死字段。
- **重构触发点**：当自定义供应商需要 per-model 并发、或 lane 维度从固定三条变为动态可扩展时，应重新评估升级到 JSON `extra` 列或并发字段子表——届时是有依据的重构，本 ADR 转 superseded。
- **改动收敛**：模型 + 一条 additive nullable 加列迁移（无 backfill，三列不进任何 `WHERE`）、`CustomProviderRepository.create_provider` / `update_provider` 透传、CRUD schema（POST / PUT 请求体 + 响应回显）、`CapacityTable.from_db` 自定义供应商接线、设置页表单三个可空 number 输入。不改 SlotTable 占用台账、lane 隔离、reload 机制；不引入 per-model 并发。
- **守住既有边界**：上承三层回退切片（自定义供应商为其两层特例）与 ADR 0037（字段集稳定时用定型列）。任何后续 PR 想改用 JSON / kv / 子表须先 deprecate 本 ADR。
