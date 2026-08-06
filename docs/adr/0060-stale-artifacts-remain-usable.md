---
status: accepted
---

# Stale 产物继续可用且不阻断工作流

Stale 只提示已有产物与当前直接内容依赖存在差异，不等同 missing，也不成为自动生成指令。工作流可以带着 stale 产物继续并到达 `EXPORT_READY`，兼容字段 `completed` 统计 current 与 stale 的可用文件总数，stale 另行展示；省略资源 ID 的生成调用只补 missing，用户显式选择或明确要求重生时才处理 stale。这样保留用户选择继续采用旧版本的权利，并避免状态查询或普通“继续”隐式触发费用。
