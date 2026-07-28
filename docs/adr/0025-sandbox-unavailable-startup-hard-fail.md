---
status: accepted
---

# 内核沙箱不可用时 server 启动硬失败，仅 Windows 文档化降级

Agent 工具的安全模型以内核沙箱（macOS Seatbelt / Linux bwrap）为底座，探测失败后若静默降级为无沙箱运行，文件越界与外发请求的防线会无声消失。决定把沙箱可用性检查放在 server 启动期（`check_sandbox_available`）：macOS 缺 `sandbox-exec`、Linux 缺 `bwrap`/`socat`、或 bwrap 存在但试跑失败时直接 raise，整个服务拒绝启动（fail-closed），而不是降级运行或等到创建会话时才报错；只有原生无沙箱的 Windows 例外——warning 后禁用沙箱，Agent Bash 工具改走代码级前缀白名单。

## Consequences

- 部署环境必须先装好沙箱依赖（macOS 自带 `sandbox-exec`；Linux 需 `bwrap` + `socat`），否则服务无法启动。这是刻意设计：宁可拒绝服务，也不在无沙箱状态下运行 agent。
- Windows 的前缀白名单比沙箱粗粒度（能放行的命令前缀有限），生产部署仍推荐 WSL2/Docker 以走完整沙箱。
