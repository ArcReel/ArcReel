---
status: accepted
---

# 开源版预埋多用户数据模型与 `_scope_query` 缝隙，但不实装多租户

给在用的表后加 NOT NULL 外键列 + 回填历史行，比一开始就带 `server_default` 的预埋代价大得多，且会让开源/商业版 schema 分叉。决定在开源版即给 Task/ApiCall/ApiKey/AgentSession 加 `user_id`（恒为 `"default"`）、建 users 表（默认 admin）、Repository 基类提供 no-op 的 `_scope_query` 改写点；商业版通过 migration 扩字段 + 子类覆盖 `_scope_query` 注入 user_id 过滤实现可见性隔离。不做目录隔离、登录流程、配额、管理后台。

## Consequences

- 开源版携带一批看似无用的 no-op 与恒定 `"default"`，**读者勿当死代码删除**。
- `claim_next` 用原生 SQL、`_scope_query` 无法拦截，是已知需商业版 override 的特例。
