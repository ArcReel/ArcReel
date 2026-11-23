---
status: accepted
---

# 只读引导演示复用真实工作台组件，由路由与传输闸门共同隔离写入

首次使用引导在工作台内展示完整创作流程时，直接复用真实工作台组件渲染内存中的演示项目数据，而不是另建一套静态演示页面或由服务端托管沙盒项目。这样工作台布局、控件和引导锚点随产品自然演进，省去维护双份界面的成本，代价是由标识命名空间、路由分支和传输层闸门三处共同守住只读隔离边界。

演示项目使用固定标识 `onboarding_demo`，其中刻意包含下划线。后端 `PROJECT_NAME_PATTERN`（`^[A-Za-z0-9-]+$`，见 [data_root_layout.py](../../lib/infra/data_root_layout.py)）拒绝含下划线的项目名，确保磁盘上不会出现同名真实项目遮蔽演示路由。路由匹配不区分大小写，因此 `isDemoProject` 按小写归一化比较，避免大小写变体落入真实项目加载路径；演示项目没有后端项目级设置，其设置路由在 [router.tsx](../../frontend/src/router.tsx) 中排在通用项目设置路由之前并重定向至全局设置。

界面在演示态下隐藏写操作入口，但传输层仍保留结构性兜底：进入演示路由时经 `setApiReadOnly(true)` 开启只读闸门，在 [transport.ts](../../frontend/src/api/transport.ts) 的 `withAuth` 中于请求发出前拒绝非 `GET` / `HEAD` 请求并抛出 `ReadOnlyModeError`，避免漏掉某个写入口时将演示操作静默发往服务端。闸门按请求目标区分放行范围：指向演示项目本身、无项目归属（含 `/projects/import` 等保留端点与全局资产库）的写请求一律拦截；指向其他具体真实项目的写请求放行，以免用户在先前真实项目发起的操作因切入演示态而被中途阻断；引导完成状态上报端点 `/onboarding/seen` 作为窄豁免放行。演示数据构造见 [demo-project.ts](../../frontend/src/onboarding/demo-project.ts)。
