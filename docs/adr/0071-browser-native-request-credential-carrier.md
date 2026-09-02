---
status: accepted
---

# 浏览器原生请求的凭证载体：SSE 走带 header 的 fetch，媒体匿名可读，导出用下载 token

浏览器原生请求带不了 `Authorization` header，ArcReel 曾对三处场景各给一个答案：SSE 把长效会话 JWT 拼进 `?token=`，项目导出用短时效的下载 token，静态媒体不设认证。三处强度与暴露面倒挂：SSE 携带的是全权限长效凭证，却经 query param 落进访问日志。本决策把三处按成因分开处理，而不是统一到同一种载体。

**SSE 不再视为浏览器原生请求**。事件流由前端以 `fetch` 消费并带 `Authorization` header，服务端把三个流式端点挂回普通的受保护 router，`get_current_user_flexible` 与 `?token=` 形态一并删除，认证执行测试对带 `?token=` 的请求断言 401。`EventSource` 的原生重连会原样复用 URL，任何走 URL 的凭证都要在每次重连前重新铸造，等于是在给 `EventSource` 的先天缺陷补票据生命周期；改用 `fetch` 是把缺陷的根源拿掉，后端只减不增。代价是前端自行负责事件解析（`eventsource-parser`）、`Last-Event-ID` 续传与退避重连，项目事件流与助手流统一为断线后自动重建。

**静态媒体维持匿名可读，是有意决策**。ArcReel 是单管理员自托管应用，威胁模型不含未授权读取媒体的攻击者；媒体加认证意味着约五十处 `<img>` / `<video>` src 都要携带凭证，内嵌 Agent 与 MCP 客户端取图要额外带 Bearer，复制链接与新标签页打开会失效，换来的只是「猜不到路径就看不到」。若未来需要分享或外部嵌入，应显式签发分享凭证，不在此决策范围内。

**导出下载维持下载 token**，它是自带认证端点如今唯一的凭证形态。ADR `docs/adr/0059` 末尾留作另议的「统一三处」在此收口。

## 明确不采用

- **Cookie 认证**。它能让 `<img>`、原生下载与 SSE 一次性免费带凭证，但价值只在媒体要求登录或必须把凭证彻底移出 URL 时成立；媒体既然维持匿名，SSE 又能改走 `fetch`，剩下的只有登录/登出流程改造、CSRF 面、`AUTH_ENABLED=false` 行为定义与威胁模型重审这些成本。
- **短时效目的绑定票据（`purpose=stream`）**。沿用下载 token 的铸造模式改动小，但票据仍在 URL 里，且 `EventSource` 每次重连都要先换票，引入一套运行时票据生命周期只为绕过 `EventSource` 带不了 header 这一点。
- **前端分离部署**。`CORS_ORIGINS` 白名单只服务非浏览器的跨域 API 调用（它们走 Bearer），浏览器访问要求与后端同源；这是不需要 Cookie 方案的前提之一，也是本决策的约束。

## Consequences

- 词汇表的「浏览器原生请求」只剩 `<img>` / `<video>` src 与原生下载导航两类，「自带认证端点」只剩导出下载端点。
- `docs/security/threat-model.md` 中关于 SSE query param 泄漏的条目随实现一并更新；Cookie、refresh token 仍属「需重审」的改动。
- 前端不再有 `EventSource` 与 `fakeEventSource` 测试替身，流式客户端的测试替身改为可控的 `fetch` 响应流。
