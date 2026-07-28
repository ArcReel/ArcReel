---
globs:
  - "frontend/**"
---

# 前端异步竞态防护

「await 后须检查过期」这条纪律不靠人肉复制闭包标志传播，按场景收敛到两种机制。

## 跨函数边界的异步链取消：AbortSignal

调用链跨出单个函数边界（effect 调用异步函数、异步函数内再发请求）时，取消一律用 AbortSignal 传播：

- API 层方法接受 `options?: { signal?: AbortSignal }` 并透传给 `fetch`。网络 await 断点被 abort 后自动 reject，过期检查由平台原语代劳，不再每处手写
- 非网络断点（写 store、建 SSE 连接等副作用）前复核 `signal.aborted`，拦截 abort 发生在响应已 resolve 之后的窗口
- 接管方轮换 controller：新一轮加载先 `abort()` 前任再新建。取消域按数据生命周期划分，不共用——项目级数据（如会话列表、技能列表）只随项目切换作废，会话级加载随任何会话操作作废，二者混用会把慢响应的项目级数据误判过期丢弃
- 被 abort 方的收尾（如 loading 复位）让位给接管方：`finally` 中先查 `signal.aborted`，已作废则不动共享状态，否则会踩到接管方正在进行的加载

首例范式：`frontend/src/hooks/useAssistantSession.ts`（init 自动选择 + `loadSession` 加载链）。

`cancelled` closure flag 是历史写法：只拦截所在函数自身的 await 断点，不传播到被调函数，不再新增；改动涉及处顺带迁移到 AbortSignal。

## 跨入口共享的刷新：store action 在途合并

同一份数据有多个入口触发刷新时，刷新逻辑收敛为单个 store action，在 action 内做在途合并（已有刷新在途则排队合并为「结束后再跑一轮」，各调用方分别结算），调用方不各自发请求。先例：`frontend/src/stores/projects-store.ts` 的 `refreshProject`。

适用边界：这是「多入口写同一份数据」的互斥问题，与上节的「过期响应作废」互补——前者保证并发刷新不交错，后者保证已离开的上下文不回写。取消一份数据的加载用 AbortSignal；合并多入口对同一份数据的刷新用 store action。
