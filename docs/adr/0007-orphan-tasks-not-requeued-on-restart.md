---
status: proposed
---

# 重启后孤儿任务一律不重新触发生成，提交-轮询型按 job_id 恢复轮询

`generation_worker.py` 现状在 worker 获得 lease 且本实例无在途任务时调用 `requeue_running_tasks()`，把 DB 里所有 `status='running'` 的孤儿任务（worker 内存里没有对应 asyncio.Task，唯一现实成因是服务进程重启——部署或崩溃恢复）丢回 queued 让下次 claim 重跑。这在「外部服务可重入、按时长计费」的系统里是合理的自愈策略，但 ArcReel 不是：媒体生成的费用按**供应商 API 调用次数**结算，requeue 一次就多扣一次钱；且单次 video 生成动辄数毛到数元，重启高峰（部署窗口、容器 OOM）一次能把项目里几十个 running 任务全部重跑——2026-05-24 的一次真实用户事故里，custom-11 video pool 容量 3 的池被 3 个长轮询 Sora 任务占满，结合 cancel running 不能中断 asyncio.Task 的设计缺陷（见 [[0006-cancelling-intermediate-state]]）让整个项目卡死，如果用户当时重启就会重复扣三笔。

我们决定：孤儿任务**绝不**自动重新触发生成。默认行为是把 status 标 `failed`，error_message 写明「服务重启中断」，让用户决定要不要重新提交。但对**提交-轮询型** backend（Gemini/Ark/OpenAI 的 video + Vidu/NewAPI 的 image/video——这些 backend 的「调用」是「提交一次 job + 之后轮询 job_id 拿结果」），我们提供「恢复轮询」能力：Task 表新增 `provider_job_id` 列（alembic 迁移），各 backend 在 submit 后立即把 job_id 持久化到对应 task；worker 启动扫表时，对带 job_id 的孤儿调用 backend 的新 `resume_video(job_id)` 接口继续 poll，不重复触发 submit。无 job_id（同步型 backend、或 submit 前进程就死了）、backend 不实现 resume、provider 返回 job 已过期/unknown——都标 failed，error_message 用 `[restart_lost]` / `[resume_unsupported]` / `[resume_expired]` 区分。

## Consequences

- `requeue_running_tasks()` 调用点从 worker 主循环移除，函数本体保留但仅供单元测试或将来手动救援使用。worker 启动改走新的 `_handle_orphan_tasks_on_start()`，按上述策略分发：可 resume 的恢复轮询、其余标 failed。
- 「恢复轮询」需要 backend 协作。`VideoBackend` 抽象类新增 `async def resume_video(job_id: str) -> ...` 可选方法（默认抛 `NotImplementedError`）。提交-轮询型 backend 复用各自现有的 `_poll_until_complete`，入口从「submit 后调」改为「直接用 job_id 调」即可，主体逻辑不重写。同步型 backend（Grok image/video，及 Gemini/Ark/OpenAI 的 image）不实现，遇到孤儿直接标 failed。
- 用户视角：重启后正在生成的 video 任务（绝大多数走轮询）会自动接续，体感无感；正在生成的 image 任务（绝大多数走同步）会变成 failed，用户需要手动重新提交。本次范围不做重试 UI（已确认），用户的「重新提交」走现有「点生成按钮」路径——这是 conscious trade-off，目的是把本次 PR 范围控制在「修队列瘫痪」三件事内。
- 该 ADR 与 [[project-worker-bundled-with-server]] 是配套的：单 uvicorn 进程下，唯一的孤儿成因就是重启；没有多 worker 协调时 lease/heartbeat 抖动制造的「假孤儿」（lease 失效但本进程其实活着）问题。若未来真做多 worker，该策略需要重审——具体说，需要区分「本进程刚启动的孤儿」（确实没人在跑）和「其他 worker 还活着的孤儿」（其他进程内存里 asyncio.Task 仍在）。本 ADR 不为该未来场景预留判定逻辑。
- 与 [[0006-cancelling-intermediate-state]] 互不重叠也互不冲突：0006 管「跑着的任务怎么被外部中止」，0007 管「重启后还没死的任务怎么处理」。Cancel running 走 cancelling 中间态；重启孤儿走 failed 或 resume，**不**经过 cancelling（cancelling 是用户主观意愿，重启是系统事件）。
- 不要为了「让重启更无感」回退到 requeue：那会把费用模型从「次数计费」变成「次数 × 重启次数」，用户从账单上吃亏，且会让本 ADR 的费用语义保护点失效。
