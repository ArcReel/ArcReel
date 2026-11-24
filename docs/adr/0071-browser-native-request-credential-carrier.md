---
status: accepted
---

# 浏览器原生请求的凭证载体：SSE 走带 header 的 fetch，媒体匿名可读，导出用下载 token

浏览器原生请求带不了 `Authorization` header，ArcReel 曾对三处场景各给一个答案：SSE 把长效会话 JWT 拼进 `?token=`，项目导出用短时效的下载 token，静态媒体不设认证。这三处的安全强度与暴露面正好倒挂：SSE 携带的是全权限长效凭证，却通过 query param 暴露在访问日志中。本决策把三处按成因分开处理，而不是统一到同一种载体。

**SSE 不再视为浏览器原生请求**。事件流由前端以 `fetch` 消费并带 `Authorization` header，服务端把三个流式端点挂回普通的受保护 router，`get_current_user_flexible` 与 `?token=` 形态一并删除，认证执行测试对带 `?token=` 的请求断言 401。`EventSource` 的原生重连会原样复用 URL，任何走 URL 的凭证都要在每次重连前重新铸造，等于是在给 `EventSource` 的先天缺陷补票据生命周期；改用 `fetch` 是把缺陷的根源拿掉，后端只减不增。代价是前端自行负责事件解析（`eventsource-parser`）、`Last-Event-ID` 续传与退避重连，项目事件流与助手流统一为断线后自动重建。

**静态媒体维持匿名可读，是有意决策**。ArcReel 是单管理员自托管应用，威胁模型不含未授权读取媒体的攻击者；媒体加认证意味着约五十处 `<img>` / `<video>` src 都要携带凭证，内嵌 Agent 与 MCP 客户端取图要额外带 Bearer，复制链接与新标签页打开会失效，换来的只是「猜不到路径就看不到」。若未来需要分享或外部嵌入，应显式签发分享凭证，不在此决策范围内。

公开文件路由只提供白名单内的媒体。项目文件要同时通过路径限制、目录检查与扩展名检查。全局资产文件也受媒体扩展名限制。公开文件响应带 `X-Content-Type-Options: nosniff`。项目元数据、脚本、字幕和归档等文件应经受保护 API 获取。目录与扩展名清单以 [公开文件路由](../../server/routers/files.py) 为准，信任边界见 [安全威胁模型](../security/threat-model.md)。

导出下载维持短期、用途和项目绑定的下载 token。普通会话认证拒绝带用途声明的 token。路由注册规则见 [ADR 0059](0059-router-level-auth-single-truth-source.md)。

## 其他方案

- **Cookie 认证**可以随浏览器原生请求发送凭证，但需要改造登录和退出，引入 CSRF 防护与新的本地免认证约定。当前部署要求浏览器访问同源。
- **为 SSE 签发短期票据**会保留 URL 中的凭证。每次重连要重新签发，增加票据生命周期管理。
