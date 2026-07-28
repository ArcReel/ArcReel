---
status: accepted
---

# 供应商并发上限禁止 0 作用户输入（要求 ≥1 或留空），0 仅保留为内部不支持-lane 哨兵

`0` 在并发体系里身兼两职、彼此冲突：CapacityTable 内部把某 lane 容量 `0` 当作「该供应商不支持此媒体」哨兵（`_lane_limits` 按 `media_types` 把未支持的 lane 投影为 `0`，worker `if cap <= 0` 即 `mark_failed("provider_unsupported_media")` fast-fail）；而用户可配的并发上限字段（内置经 `_validate_value`、自定义经 API `ge=0` + DB `CHECK >= 0`）此前一律**接受** `0`。用户填 `0`（误读「默认」占位符、或以为 `0`=不限）会静默把该 lane 关死，之后每个任务报「供应商不支持此媒体」——一个无声且失败信息答非所问的 footgun。注册表出厂默认 `ProviderMeta.default_concurrency` 早已要求 `>= 1`，唯独用户值放行 `0`，两侧不对称。

我们决定**把 `0` 从合法用户输入中移除**：所有用户输入层（前端表单、自定义供应商 API `Field(ge=1)`、内置 `_validate_value` 的 `parsed < 1`）与 DB CHECK（`custom_provider` 三列 `>= 1`）统一要求 `>= 1`，留空 / `NULL` 仍表示「回退默认」。`0` 仅保留为 CapacityTable 内部「不支持该 lane」哨兵，不对用户开放。承自定义供应商并发上线（ADR 0042）尚未发版，DB CHECK 的收紧**直接改写其加列迁移**（`>= 0` → `>= 1`、约束名 `_non_negative` → `_positive`），不新增迁移、无数据回填（`0` 无自动来源，唯一来源是用户输入，堵在门口即不再产生）。运行时 `if cap <= 0` / `_lane_limits` / `provider_unsupported_media` 一概不动——`0` 此后只可能源自「该供应商本就不支持此媒体」，失败码语义恰好正确。

**明确不采用**：把 `0` 立为「禁用该 lane」一等公民。provider 在入队时即绑定，`0` 不会把任务改道到别的供应商、只会 fast-fail，实现不了「优雅禁用」；三层回退已用留空 / `NULL` 干净表达「用默认」，`0` 无须承担第二语义；不想用某供应商跑某 lane 的诚实做法是不为该 lane 选它。若将来「禁用某供应商的某 lane」成为真实需求，应另设显式开关并配诚实的失败码，而非重载 `0`。

## Consequences

- **两个 `0` 角色彻底分离**：用户契约层「并发上限是 ≥1 整数或留空」与内部「容量 `0` = 不支持该 lane」不再混用同一个用户可达的值；CONTEXT.md「容量」词条同步补记用户契约面。
- **改写已合入 main 的迁移**：并进的其他 worktree / dev DB 若已 `alembic upgrade head` 跑过旧（`>= 0`）版本，CHECK 不会自动收紧（alembic 不重跑已应用 revision），需 `downgrade -1 && upgrade head` 或重建 dev DB。因未发版且 `0` 无自动来源，实践无害；这是「未发布迁移可就地改写」的有意取舍，发布后不可再用此手法。
- **改动收敛**：三处输入层校验 + 三列 DB CHECK + 原迁移就地收紧 + i18n 文案（后端 `max_workers_must_be_positive_integer`、前端「正整数」与帮助文案）+ 相应测试。运行时调度、SlotTable、lane 隔离、reload 一概不动。
- **守住边界**：与注册表 `default_concurrency >= 1` 规则统一；上承 ADR 0042（自定义供应商并发定型列）。
