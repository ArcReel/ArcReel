---
status: accepted
---

# Agent Anthropic 凭证独立存储，每会话从 DB 注入 env 而非写全局 os.environ

全局 env 是进程级单值，与「多凭证 + 每会话可用不同 active」冲突，且 provider 密钥已全面禁入 `os.environ`。决定 Claude Agent SDK 的 Anthropic 网关凭证存于独立表（与自定义 provider 凭证分离、不进 `ENDPOINT_REGISTRY`、不参与媒体生成），生效方式为每次新建 Agent 会话时由 `build_anthropic_env_dict`（入参是 DB session）从 DB 读 active 凭证返回 dict 注入 `ClaudeAgentOptions.env`、**不写全局 os.environ**；activate 端点只 set_active、不做 env 同步。

## Consequences

- 已运行的 session 仍持有 spawn 时的 env，切换 active 只对**新**会话生效（仅 toast 提示，不强制终止）。
- 与 `docs/adr/0008`（自定义 provider 凭证模型）互补：两者都把「凭证存储不被协议形态/进程全局污染」「运行时确定 vs 启动时声明」的边界讲清楚。
