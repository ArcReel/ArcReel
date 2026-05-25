# 图片 capability（t2i/i2i）仅在执行时解析，worker 限流路由使用近似 capability

一个分镜镜头走文生图（t2i）还是图生图（i2i），取决于它**有没有参考图**；而参考图集合（引用的角色/场景/道具立绘、以及为画面连贯而引用的**上一张分镜**）是「开画那一刻」项目状态的快照——尤其批量重生成时，第 N 镜依赖的第 N-1 镜在入队时往往尚未画出。因此 capability 是执行时才能确定的派生事实，入队和 worker 认领（claim）这两个执行前的环节**在结构上都无法知道它**。我们据此决定：capability 只在执行层解析，入队不携带任何 provider 信息（已删除入队时的 backend snapshot）。

## Consequences

- worker 认领任务时需要一个 provider 仅用于并发限流（rate-limit 池路由），但它拿不到真实 capability，故统一按 `capability="t2i"` 取一个**代表性** provider。
- 仅当用户给同一项目的 t2i 与 i2i **配置了不同 provider** 时，限流记账会落在 t2i 那个池上而非实际执行的池——这是不影响生成正确性的已知近似（执行层会独立再精确解析一次真正使用的 provider/model）。
- 这点偏差**无法消除**：只要「t2i/i2i 可配不同 provider」与「capability 执行时才定」同时成立，任何执行前的限流都只能近似。**不要试图在入队/认领时记录 needs_i2i 来"修复"它**——那需要把执行层的参考图收集逻辑复制进调度层，且在批量场景下入队时的答案本就是错的。
