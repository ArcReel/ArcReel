# 部署补充说明

本文档补充 [`getting-started.md`](getting-started.md) 未覆盖的部署细节，主要面向已经能够通过 Docker / 本地启动 ArcReel 的运维与开发者。

## Agent 沙箱依赖

ArcReel 启动会进行严格的安全检查 — sandbox 工具缺失即拒绝启动。

| 环境 | 工具 | 安装 |
|---|---|---|
| macOS | `sandbox-exec` | 系统自带，无需额外安装 |
| Linux 本地开发 | `bwrap` | `sudo apt install bubblewrap` (Ubuntu/Debian) / `sudo pacman -S bubblewrap` (Arch) |
| Docker | `bwrap` | Dockerfile 已包含 |

启动失败时 server 会输出明确错误信息，按提示安装即可。

**.env 迁移说明**：sandbox 设计要求父进程 `os.environ` 不含任何 provider 密钥。
请把 `.env` 中的下列 key 移到 WebUI 系统配置页：

- `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` 等 ANTHROPIC_*
- `ARK_API_KEY` / `XAI_API_KEY` / `GEMINI_API_KEY` / `VIDU_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS`（vertex 凭据继续放 `vertex_keys/` 目录）

启动检测发现这些 key 仍存在于 env 时，server 会拒绝启动并提示需要清理。
