---
status: accepted
---

# 运行时状态合并为单一异步 ORM 数据库

原本 3 个独立的同步 SQLite 数据库（任务队列、API 用量与 Agent 会话）无法通过单个 `DATABASE_URL` 切换到 PostgreSQL，跨库也无法使用外键和事务，且同步驱动会在异步 FastAPI 路由中阻塞事件循环。决定硬切换合并为单一 SQLAlchemy 异步数据库，由一个 `DATABASE_URL` 决定 SQLite（开发）或 PostgreSQL（生产），提供一次性迁移、不保留旧手写 SQL 路径——硬切换避免中间双写态，换来统一事务语义与数据库可切换。

## Consequences

- 迁移不可回滚。一旦所有 Repository 和 Worker 完成异步化改造并依赖单库事务语义，退回多库或同步驱动将意味着重写整个数据访问层。
- 存储边界明确分层：运行时状态存入数据库，项目元数据与剧本仍保存在项目目录中。图片内容版本可以落入对象存储，见 [ADR 0116](0116-remote-content-versions-saved-by-formal-commit-with-object-references.md)。

## 用户归属

任务、用量记录、API 密钥与 Agent 会话等运行时数据带有用户归属。单操作者版本沿用同一个默认用户，[仓储基类](../../lib/db/repositories/base.py) 的 `_scope_query` 提供可见性过滤接缝。底层任务认领等原生 SQL 不经过这个接缝。预留字段避免日后为已使用的大表补加归属列与回填历史数据，尚不足以构成多租户授权。安全部署支持范围见 [安全政策](../../SECURITY.md)。
