---
status: accepted
---

# 运行时状态合并为单一异步 ORM 数据库

原本 3 个独立的同步 SQLite 库（任务队列 / API 用量 / Agent 会话）无法靠单个 `DATABASE_URL` 切到 PostgreSQL，跨库也无法使用外键/事务，且同步驱动在 async FastAPI 路由里阻塞 event loop。决定硬切换合并为单一 SQLAlchemy 异步数据库，由一个 `DATABASE_URL` 决定 SQLite（开发）或 PostgreSQL（生产），提供一次性迁移、不保留旧手写 SQL 路径——硬切换避免中间双写态，换来统一事务语义与数据库可切换。

## Consequences

- 迁移不可回滚；一旦所有 Repository/Worker 都 async 化并依赖单库事务语义，退回多库或同步驱动需重写整个数据访问层。
- 存储边界明确分层：运行时状态进 DB，项目数据（project.json / 剧本 / 媒体文件）仍留文件系统。
