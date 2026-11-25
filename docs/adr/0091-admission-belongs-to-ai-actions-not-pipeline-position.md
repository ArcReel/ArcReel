---
status: accepted
---

# AI 操作按各自输入准入，制作状态报告内容与可执行动作

早期的固定流水线会按第一个缺失项推断整集所处的阶段，导致只要缺少源文，连手写脚本也必须绕过一连串与自身无关的限制。现在每个 AI 操作有自己的输入准入，制作状态与执行入口共用结构判据。空的合法内容允许保存，只有具体操作需要的输入才构成它的阻断条件。

制作状态展示已有内容、当下各操作能否执行及建议的下一步。分岔处给出选项。用户点名操作时按该动作的准入执行，只说「继续」时使用建议。整体数据不可用仍由迁移和产物清单的阻断机制处理。过期产物仍可用，更新需显式选择。

制作状态使用无外部 I/O 的结构准入。报价、供应商档位和实际请求投影留在预览与提交时核对。每项目标的准入和整批准入规则见 [ADR 0073](0073-generation-entry-requires-registered-assets-with-sheets.md) 和 [ADR 0061](0061-batch-video-admission-all-or-nothing.md)。

逐集清单 `GET /projects/{name}/workflow-status/episodes` 按账本顺序返回。重复传 `episodes` 可指定 1～32 个互异正集号。省略时每页最多 32 集，用响应的 `next_cursor` 作为下一次请求的 `cursor`，末页游标为 `null`。账本变更后需重新读取首页。未知集号、过期游标或同时指定集号和游标返回 HTTP 400。项目数据不可用时返回空页。

每行包含 `episode`、`plan_stale`、`episode_complete` 和 `next_action`。已完成集的动作是 `null`；待重规划的未完成集标记 `plan_stale=true`，动作类型为 `none`。

工作流契约 v3（`WorkflowStatus` 与 `WorkflowPlan` 的 `schema_version=3`）将 `artifacts.asset_sheets`、引用资产统计及规划器观察的资产任务收窄为目标集直接引用的资产；为判断衍生图准入而读取的未引用本体不混入 `artifacts.asset_sheets`。全项目资产图状态由 `GET /projects/{project_name}/asset-sheets/status` 单独提供。
