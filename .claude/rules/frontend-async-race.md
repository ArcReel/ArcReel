---
paths:
  - "frontend/**"
---

# 前端异步竞态防护

「await 后须检查过期」这条纪律不依赖手工复制闭包标志传播，按场景收敛到两种机制。

## 跨函数边界的异步链取消：AbortSignal

调用链跨出单个函数边界（effect 调用异步函数、异步函数内再发请求）时，取消一律用 AbortSignal 传播：

- API 层方法接受 `options?: { signal?: AbortSignal }` 并透传给 `fetch`。网络 await 断点被 abort 后自动 reject，过期检查由平台原语完成，无需逐处手写
- 非网络断点（写 store、建 SSE 连接等副作用）前复核 `signal.aborted`，覆盖 abort 发生在响应已 resolve 之后的窗口
- 接管方轮换 controller：新一轮加载先 `abort()` 上一个 controller 再新建。取消域按数据生命周期划分，不共用——项目级数据（如会话列表、技能列表）只随项目切换作废，会话级加载随任何会话操作作废，二者混用会把慢响应的项目级数据误判过期丢弃
- 被 abort 方的收尾（如 loading 复位）让位给接管方：`finally` 中先查 `signal.aborted`，已作废则不修改共享状态，否则会干扰接管方正在进行的加载
- 作废由一次性事件触发的在途请求后必须补拉：事件已被消费，那份数据不会自行再来，作废方（或接管方）在 abort 后自行发起一轮拉取补上缺口，否则界面停留在旧数据——「取消」与「补偿」成对出现

参考实现：`frontend/src/hooks/useAssistantSession.ts`（init 自动选择 + `loadSession` 加载链）、`frontend/src/hooks/useScriptReviewDraft.ts`（采纳写入时作废在途拉取并补拉）。

`cancelled` closure flag 是历史写法：只拦截所在函数自身的 await 断点，不传播到被调函数，不再新增；改动涉及处一并迁移到 AbortSignal。

## hook 的函数型 option：显式依赖，不收进 ref

自定义 hook 接受函数型 option（selector / 回调）时，把它原样列入内部 effect/callback 的依赖数组，并在类型与 JSDoc 上要求调用方传稳定引用（`useCallback` / 模块级函数）；不在 hook 内用 ref 存最新值来豁免依赖。显式依赖下，调用方传了不稳定引用会表现为 effect 反复重跑，当场暴露；ref 豁免则把同一错误静默吸收——行为看似正常，回调却可能闭包过期状态，无从发现。先例：`frontend/src/hooks/useScriptReviewDraft.ts`。

## 跨入口共享的刷新：store action 在途合并

同一份数据有多个入口触发刷新时，刷新逻辑收敛为单个 store action，在 action 内做在途合并（已有刷新在途则排队合并为「结束后再执行一轮」，各调用方各自 resolve；排队目标被后续不同目标覆盖时，被覆盖的调用方立即以 cancelled 结算，不共享新目标的结果），调用方不各自发请求。先例：`frontend/src/stores/projects-store.ts` 的 `refreshProject`。

适用边界：这是「多入口写同一份数据」的互斥问题，与上节的「过期响应作废」互补——前者保证并发刷新不交错，后者保证已离开的上下文不回写。取消一份数据的加载用 AbortSignal；合并多入口对同一份数据的刷新用 store action。
