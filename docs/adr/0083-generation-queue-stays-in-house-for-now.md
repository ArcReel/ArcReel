---
status: accepted
---

# 生成任务队列现阶段保持自研，不引入第三方任务队列库

生成任务的持久化、认领、并发与重启自愈由 ArcReel 自己实现（`lib/generation_queue.py`、`lib/generation_worker.py`、`lib/db/repositories/task_repo.py`），没有建立在 Celery、dramatiq、arq、taskiq、procrastinate、APScheduler 之类的任务队列库之上。我们评估过换库，决定现阶段不换，理由有两条，缺一条结论都会不同。

第一，受支持的部署形态里找不到成熟的候选。ArcReel 要能以「默认 SQLite、不依赖任何额外服务、Windows 原生可跑」的单机形态运行，队列状态与业务状态同在一个 `DATABASE_URL` 指向的库里（ADR 0020），SQLite 与 PostgreSQL 两种方言都要支持。Celery、dramatiq、arq、taskiq 都要求独立的 broker（Redis 或 RabbitMQ），Celery 的 prefork 模型另不支持 Windows；procrastinate 只支持 PostgreSQL；形态上唯一对得上的 APScheduler 4（SQLAlchemy 异步数据存储、进程内运行）官方文档写明是 pre-release、不得用于生产，且它的并发上限按任务函数静态配置，表达不了「按供应商与媒体通道、运行期可改」的容量。

第二，能被队列库替掉的部分很小。三个文件里属于通用队列底层的——认领、租约、状态转移守卫、占用台账——只有几百行；其余是队列库不会提供、换库后仍得自己写的领域逻辑：容量随用户的供应商配置在运行期重载且不打断在途任务（ADR 0042、0043）、孤儿任务绝不重新入队而是凭供应商任务 id 与执行检查点续跑以免重复扣费（ADR 0007）、认领期按当前项目重新投影限流路由键（ADR 0055、0056）、供应商调用记账的收口、整批准入（ADR 0061）。自研底层带来的缺陷集中在早期并已收敛，此后的缺陷几乎都落在这些领域语义上，换库不会让它们消失。

## Consequences

- 这是「现阶段」的结论而非永久排除。出现下面任一变化时应重新评估：决定支持多进程或多实例部署（进程内的任务句柄不再够用，见 ADR 0006）；队列只需要运行在 PostgreSQL 上（届时 procrastinate 是第一候选）；出现了同时支持 SQLite 与 PostgreSQL、可进程内运行且已发布稳定版的队列库。
- 重新评估时要比较的是那几百行通用底层的维护成本，而不是整个队列模块的体量；领域逻辑无论如何留在 ArcReel。
- 供应商的 RPM 限制不属于队列，由各调用通道自行节流。
