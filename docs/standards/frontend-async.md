---
paths:
  - "frontend/src/**"
---

# 前端异步竞态

处理「await 之后数据可能已过期」时，按场景采用下面几种机制，不靠手工传播闭包标志。

## 取消与过期

### 跨函数边界的异步链用 AbortSignal 取消

调用链一旦跨出单个函数（effect 调用异步函数、异步函数里再发请求），取消就通过 AbortSignal 传播：

- API 层方法接受 `options?: { signal?: AbortSignal }` 并原样传给 `fetch`。网络 await 点被 abort 后自动 reject，过期检查由平台原语完成。
- 非网络 await 点之后、执行副作用（写 store、建 SSE 连接等）之前复核 `signal.aborted`，覆盖「abort 发生在响应已 resolve 之后」的窗口。
- 接管方轮换 controller：新一轮加载先 `abort()` 上一个 controller 再新建。
- 收尾（如 loading 复位）由接管方负责。被 abort 方在 `finally` 中先检查 `signal.aborted`，已作废就保持共享状态不变，否则会打断接管方正在进行的加载。

`cancelled` 闭包标志只拦得住所在函数自己的 await，传不到被调函数里。diff 中新增的 `cancelled` 标志是违规；改动触及的旧写法一并迁到 AbortSignal。

参考实现：`frontend/src/hooks/useAssistantSession.ts`（init 自动选择与 `loadSession` 加载链）。

### 取消域按数据生命周期划分

项目级数据（会话列表、技能列表）只随项目切换作废；会话级加载随任何会话操作作废。两者共用一个 controller 时，一次会话操作就会把慢响应的项目级数据当成过期丢掉，界面停在旧列表上。判定：两份数据作废的时机不同，就不共用 controller。

### 作废一次性事件触发的请求，必须补拉

由一次性事件（SSE 通知、用户确认）触发的在途请求被 abort 后，这个事件已经消费掉，数据不会再来一次。作废方（或接管方）在 abort 之后自己发起一轮拉取补上缺口，否则界面永远停在旧数据。「取消」与「补偿」成对出现：diff 里只有 abort 没有补拉，就要问这份数据之后由谁带回来。

参考实现：`frontend/src/hooks/useScriptReviewDraft.ts`（采纳写入时作废在途拉取并补拉）。

## Hook 与 store

### Hook 的函数型 option 列入依赖，不存进 ref

自定义 hook 接受函数型 option（selector、回调）时，把它原样列进内部 effect / callback 的依赖数组，并在类型与 JSDoc 上要求调用方传稳定引用（`useCallback` 或模块级函数）。显式依赖下，不稳定的引用表现为 effect 反复重跑，问题立刻暴露；用 ref 保存最新值来豁免依赖，会把同一个错误静默吸收：行为看似正常，回调却可能捕获过期状态，无从发现。

参考实现：`frontend/src/hooks/useScriptReviewDraft.ts`。

### 多入口触发的刷新统一为一个 store action，在途合并

同一份数据有多个入口触发刷新时，刷新逻辑统一为单个 store action，在 action 内做在途合并：已有刷新在途就排队，结束后再执行一轮，各调用方各自 resolve。排队中的刷新目标（如项目）被后续不同目标覆盖时，被覆盖的调用方立即以 cancelled 结算，不分享新目标的结果。调用方各自发请求会让并发刷新交错写回。

这条规则处理「多入口写同一份数据」的互斥，与「跨函数边界的异步链用 AbortSignal 取消」互补：取消一份数据的加载用 AbortSignal，合并多入口的刷新用 store action。

参考实现：`frontend/src/stores/projects-store.ts` 的 `refreshProject`。

## 写入后的刷新

### 写入成功后的刷新经 `refreshAfterWrite`，后续动作只在刷新成功后提供

`refreshProject` 以结算值报告失败而不 reject。写入已提交、随后的刷新失败时，界面停在旧数据上，看着像这次写入没生效，创作者容易重复操作。写入成功后的刷新一律调用 `frontend/src/components/canvas/shared/refreshAfterWrite`：

- 结算为 `failed` 时由它提示 `common:write_refresh_failed`；`cancelled` 是项目已切走，静默。
- 结算值原样返回。撤销、切到新条目、定位到新建的集、「成功」提示这类依赖新数据的后续动作，只在 `success` 后提供。刷新失败时只留刷新失败的提示，不同时报告成功。
- 已有专属提示的调用点（分镜保存经 `PartialSaveError`、时间线增删分镜、改名、合并、删除）可以直接消费结算值，自行提示。

加载与同步路径（路由首屏加载、项目事件 SSE）不是写入后的刷新，传 `onError` 或按结算值处理。ESLint 的 `no-restricted-syntax`（`RESTRICT_DISCARDED_REFRESH`）拦截丢弃 `refreshProject` 结算值、又没有传 `onError` 的调用。

参考实现：`frontend/src/components/canvas/episodes/CreateEpisodeDialog.tsx`（刷新成功才定位到新集）。

## 主动跳过的补偿

### 跳过的补偿要记账，保护窗口关闭时补做

保护条件成立时主动跳过一次补偿动作（补拉、对账、收尾切换），被跳过的补偿不会自己再发生。跳过方按触发它的 key 记下来，保护窗口关闭的一侧负责补做；补做放在 `finally` 这类同时覆盖成功与失败的位置，否则失败路径上保护窗口永不关闭，记下的补偿永远不会执行。

### 保护状态按完整作用域分桶，可叠加的用计数

跨请求存活的保护状态（在途计数、跳过标志）按语义上的完整作用域（请求、项目、会话）分桶。少一个维度，不同操作就共享一个桶：并行的另一次操作解除了本次的保护，或者别的作用域上迟迟不结束的操作一直挡住本该发生的补偿。会并发叠加的状态用计数，不用布尔。

参考实现：`frontend/src/hooks/useAssistantSession.ts` 的 `deletingCurrentRef`（项目 × 会话计数分桶）与 `deferredRefreshRef`（记录跳过的列表补拉，删除收尾后补做）。
