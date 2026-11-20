---
status: accepted
---

# 生成任务队列由 ArcReel 维护

队列状态与业务数据写入同一个异步数据库。支持的部署既可以使用 SQLite 启动单机实例，也可以使用 PostgreSQL，不必另起消息服务。曾评估进程外 broker、仅支持 PostgreSQL 的数据库队列和进程内调度库。决策时没有同时支持两种数据库、具备任务持久化能力且可用于生产的成熟替代方案。数据库部署边界见 [ADR 0020](0020-runtime-state-single-async-orm-db.md)。

换库主要能接手认领、租约和状态转移等通用底层。并发容量随供应商配置重载、已提交任务只续查、按生成路线准入与记账等领域规则仍由 ArcReel 维护。队列入口见 [GenerationQueue](../../lib/generation/generation_queue.py) 和 [GenerationWorker](../../lib/generation/generation_worker.py)。

如果部署改为多进程或多实例、只需支持 PostgreSQL，或出现符合上述约束的成熟队列库，可以重新比较通用底层的维护成本。多进程部署还须重审 [任务所有权与重启恢复](0007-orphan-tasks-not-requeued-on-restart.md)。
