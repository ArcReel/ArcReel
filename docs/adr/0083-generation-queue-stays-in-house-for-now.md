---
status: accepted
---

# Keep the generation queue in-house for now, without a third-party task queue

ArcReel implements persistence, claiming, concurrency, and restart recovery itself (`lib/generation/generation_queue.py`, `lib/generation/generation_worker.py`, `lib/db/repositories/task_repo.py`), not on Celery, dramatiq, arq, taskiq, procrastinate, or APScheduler. We evaluated replacement and decided against it for now for two reasons, both necessary to the conclusion.

First, no mature candidate fits supported deployments: default SQLite, no extra services, native Windows, queue/business state sharing one `DATABASE_URL` (ADR 0020), supporting both SQLite and PostgreSQL. Candidates fail differently at the same boundary: extra brokers or inability to persist tasks in that same dual-dialect database.

- Celery: stable transports are external RabbitMQ, Redis, and Amazon SQS. Database transport is experimental, recommended only for local development; Windows is unsupported since 4.x.
- dramatiq: bundled brokers are RabbitMQ/Redis.
- arq: Redis only.
- taskiq: development InMemoryBroker and single-worker ZeroMQ do not persist tasks. Others (RabbitMQ/Redis/NATS and third-party PostgreSQL/SQS) are external or PostgreSQL-only.
- procrastinate: PostgreSQL only.
- APScheduler 4: the only shape match (async SQLAlchemy, SQLite/PostgreSQL, in-process), but officially prerelease and not production-ready. Per-task-ID concurrency, multiple IDs per function, and runtime limits could map provider/media capacities. Mapping and noninterrupting reload semantics are integration complexity, not blockers.

Second, replaceable generic claiming/leases/state guards/occupancy total only a few hundred lines. The rest remains domain logic: runtime provider-capacity reload without interrupting tasks (ADRs 0042/0043), never requeueing orphans and resuming provider jobs/checkpoints without double charges (0007), checking current routing and requeueing mismatches (0055/0056), accounting, and batch admission (0061). Generic defects concentrated early and converged; later problems mainly concern domain semantics that libraries do not remove.

## Consequences

- This is temporary, not permanent exclusion. Reassess for multiprocess/multi-instance support (orphan assumptions are single-process, ADR 0007), PostgreSQL-only deployment (opening procrastinate), or stable in-process dual-dialect libraries.
- Compare the few hundred generic lines' maintenance cost, not total module size. Domain logic stays in ArcReel regardless.
- Providers' RPM limits are backend throttling, not queue responsibilities.
