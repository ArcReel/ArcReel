# 部署补充说明

本文档补充 [`getting-started.md`](getting-started.md) 未覆盖的部署细节，主要面向已经能够通过 Docker / 本地启动 ArcReel 的运维与开发者。

## Agent 沙箱依赖

ArcReel 在 Linux 和 macOS 启动时会严格检查 Agent 沙箱，所需工具缺失或不可用时会拒绝启动。Windows 原生环境没有 bwrap，会自动降级为受限的 Bash 命令白名单；该模式只保证项目创建与基础流程，生产部署建议使用 WSL2 或 Docker Desktop。

| 环境 | 工具 | 安装 |
|---|---|---|
| macOS | `sandbox-exec` | 系统自带，无需额外安装 |
| Linux 本地开发 | `bwrap` + `socat` | `sudo apt install bubblewrap socat` (Ubuntu/Debian) / `sudo dnf install bubblewrap socat` (Fedora) / `sudo pacman -S bubblewrap socat` (Arch) |
| Docker | `bwrap` + `socat` | Dockerfile 已包含 |
| Windows 原生 | 无 bwrap 沙箱 | 自动降级为 Bash 命令白名单；推荐改用 WSL2 / Docker Desktop |

Docker 镜像虽然已包含 `bwrap` 和 `socat`，宿主机的 user namespace 或 AppArmor 策略仍可能阻止沙箱启动。启动失败时按 server 输出的 `SANDBOX_*` 诊断修复，不要直接把容器改成特权模式。

**.env 迁移说明**：sandbox 设计要求父进程 `os.environ` 不含 provider 密钥，供应商配置应迁移到 WebUI 系统配置页。

以下凭据环境变量存在非空值时，server 会拒绝启动并提示清理：

- `ANTHROPIC_API_KEY`
- `ARK_API_KEY` / `XAI_API_KEY` / `GEMINI_API_KEY` / `VIDU_API_KEY`
- `DASHSCOPE_API_KEY` / `MINIMAX_API_KEY` / `AGNES_API_KEY` / `OPENAI_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS`（Vertex 凭据继续放 `vertex_keys/` 目录）

`ANTHROPIC_BASE_URL`、模型名等非密钥配置不会单独触发上述启动拒绝，但仍建议与对应凭据一起在 WebUI 中管理。
