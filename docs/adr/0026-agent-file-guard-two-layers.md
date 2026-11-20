---
status: accepted
---

# Agent 文件访问采用内核沙箱与应用权限检查

Agent 访问规则由 [AgentAccessPolicy](../../server/agent_runtime/agent_access_policy.py) 统一维护。Bash 及其子进程受内核沙箱的文件规则约束，SDK 内置 Read、Write、Edit、Glob、Grep 在 SDK 进程内执行，由 `PreToolUse` hook 检查。SDK 的预授权工具可能跳过普通权限回调，因此钩子必须在工具执行前挂载。两层都保护项目边界、敏感文件和受保护写入。项目元数据写入见 [ADR 0003](0003-agent-project-json-edits-via-tools.md)。

## 启动检查与平台回退

macOS 和 Linux 须启用内核沙箱。macOS 检查 `sandbox-exec`，Linux 检查 `bwrap` 和 `socat` 并试跑 `bwrap`。依赖缺失或试跑失败时整个服务拒绝启动，避免无声丢失文件约束。

原生 Windows 不具备这层内核沙箱，启动时会输出告警，且 Bash 仅放行受限的内置 Python 脚本入口。命令白名单只能约束命令形态，无法提供同等强度的文件隔离。需要完整沙箱时使用 WSL2 或受支持的 Linux、macOS 环境。

实际检查由 [应用启动](../../server/app.py) 执行。命令环境清理见 [ADR 0027](0027-provider-secrets-banned-from-process-env.md)，网络权限见 [ADR 0069](0069-embedded-agent-http-api-access.md)。
