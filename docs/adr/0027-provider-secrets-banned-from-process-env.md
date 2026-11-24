---
status: accepted
---

# 供应商密钥按调用注入并限制子进程继承

供应商密钥存于应用的凭证存储。全局环境既会向子进程传播，也无法表达会话间不同的生效凭证。保护分三层。

1. 服务启动时拒绝父进程环境中已知的供应商密钥。清单见 [环境变量约定](../../lib/config/env_keys.py)。
2. SDK 子进程获得本会话的 Anthropic 凭证。其余供应商变量由会话装配覆盖为空值。
3. macOS 和 Linux 上的 Bash hook 清除已知的密钥变量及匹配敏感命名规则的变量。原生 Windows 由 [访问策略](../../server/agent_runtime/agent_access_policy.py) 按受限脚本入口进行命令裁决，启动检查与 SDK 环境覆盖仍然有效。

Bash 保留用于 ArcReel HTTP API 的短时凭证。此环境清理限制直接继承，API 权限的信任边界见 [ADR 0069](0069-embedded-agent-http-api-access.md)。

## Agent 凭证

Claude Agent SDK 的 Anthropic 兼容凭证独立于媒体供应商和调用端点注册表。每次启动 Agent 客户端时，[会话装配](../../server/agent_runtime/options_assembler.py) 从数据库读取生效凭证，通过 SDK 会话环境注入。切换凭证只影响之后启动的客户端，已运行的客户端继续使用既有环境。该连接的计费也以其启动快照为准，见 [ADR 0102](0102-agent-cost-computed-per-request-from-tokens.md)。
