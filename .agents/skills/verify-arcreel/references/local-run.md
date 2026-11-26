# 本地隔离运行与排障

在目标 worktree 根目录运行 `uv run python scripts/local_instance.py start`。就绪后，用输出的 `base_url` 打开实例，保存 `data_dir` 供查询与回收。登录验证使用 `start --auth`。其余选项见各子命令的 `--help`。

启动器在同一回环端口提供前端构建产物和 API。直接使用 Vite 开发服务器可能通过固定代理访问其他 worktree 的后端。本地验证使用独立的数据根与 SDK 配置目录，跳过旧工作区日志迁移，并隔离父环境的凭据和数据库配置。

- **后端就绪或请求失败**。用 `uv run python scripts/local_instance.py status --data-dir <data_dir>` 复核健康状态，并查看启动输出中 `log_file` 指向的日志。启动阶段失败时，错误输出包含日志末尾。
- **界面与改动不符**。重新执行默认构建，确认浏览器打开的是本次启动输出的地址。
- **结束验证**。运行 `uv run python scripts/local_instance.py stop --data-dir <data_dir>`，确认输出报告端口已释放、临时目录已回收。
