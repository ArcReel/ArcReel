---
status: accepted
---

# 产品 sheet 与其他设计图共用产物生命周期

产品原图继续作为保真锚点，标准化 product sheet 继续作为可选派生参考，但 product sheet 不再设置独立人工确认门、审核状态或确认工具。独立门禁会让产品资产偏离 character、scene、prop 的统一产物模型，并增加工作流、持久化与 UI 分支；产品 sheet 改为与其他设计图共用 current、stale、missing、blocked 生命周期，生成质量由现有查看、重生与版本操作处理。本决策仅取代 ADR 0034 的“人工闸门”，保留其双层参考、注入顺序与路线边界。
