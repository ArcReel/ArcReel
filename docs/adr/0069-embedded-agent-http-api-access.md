---
status: accepted
---

# 内嵌 Agent 以短期凭证调用 ArcReel HTTP API

内嵌创作 Agent 需要读取供应商文档，并通过现有 HTTP API 验证、测试和保存调用端点。固定域名清单覆盖不了用户选择的文档站。为这些功能复制一套常驻 MCP 工具会引入第二份 Agent 接口。

Agent 沙箱允许访问任意出站域名和宿主 loopback。SDK 将 `allowedDomains` 用作无人值守会话的预放行清单，`allowLocalBinding` 单独开放 loopback。省略任一设置会使对应请求无法访问。文件权限继续由 [ADR 0026](0026-agent-file-guard-two-layers.md) 确定。

认证开启时，会话装配签发管理员 JWT，15 分钟有效且不续期。Agent 的脚本通过 `ARCREEL_API_TOKEN` 和 `ARCREEL_API_BASE` 使用它。认证关闭时 token 为空。长会话收到 401 后须重开会话获取新凭证。相关行为由 [会话装配](../../server/agent_runtime/options_assembler.py) 和 [访问策略](../../server/agent_runtime/agent_access_policy.py) 定义。

这一决策明确接受两项风险暴露：一是提示词注入可能导致 JWT 外泄，持有者可在有效期内调用管理员 API；二是开放 loopback 会让 Bash 能够访问同一宿主机上的其他服务。轮换签名密钥可使已签发的 JWT 提前失效，但也会同时让所有网页登录态失效。环境清理只防止 Bash 直接继承供应商密钥，见 [ADR 0027](0027-provider-secrets-banned-from-process-env.md)。计费或覆盖等操作前的创作确认属于 Agent 操作约束，不构成安全隔离。
