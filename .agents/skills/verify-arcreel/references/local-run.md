# 本地隔离运行与排障

本地真机验证统一通过 `scripts/local_instance.py` 启动、查询与回收实例。子命令与全部参数以 `uv run python scripts/local_instance.py --help` 及各子命令 `--help` 为准。

## 为什么不用 Vite dev server 或主仓环境

- **同端口托管前端产物**：Vite dev server 的 API 代理固定指向 `1241` 端口，多 worktree 并行时会串流到其他实例。启动器先构建或校验当前 worktree 的 `frontend/dist`，再由后端在同一隔离端口直接托管静态产物与 API。
- **凭据与数据隔离**：worktree 内启动的进程若开启 dotenv，会向上查到主仓 `.env` 的真实供应商密钥与数据库配置。启动器自动设置 `PYTHON_DOTENV_DISABLED=1`、剥离父进程环境中的供应商密钥与数据库变量，并将 `ARCREEL_DATA_DIR` 指向带 `.arcreel-local-instance` 哨兵的独立临时目录。

## 启动与就绪检查

1. 在目标 worktree 根目录运行 `uv run python scripts/local_instance.py start`（当前 `frontend/dist` 已是最新构建时可加 `--skip-build`，需要验证登录流程时加 `--auth`）。
2. 启动器轮询 `http://<host>:<port>/health` 返回 `status=ok` 后，向标准输出打印实例 JSON（含 `base_url`、`pid`、`port`、`data_dir`、`log_file`、`state_file`），并将状态持久化到 `<data_dir>/instance.json`。
3. 验证期间可运行 `uv run python scripts/local_instance.py status --data-dir <data_dir>` 复核进程存活与健康检查状态。

## 故障定位与资源回收

- **启动失败或接口报错**：查看实例 JSON 中 `log_file` 指向的后端日志（默认 `<data_dir>/backend.log`）。若 `start` 阶段就绪超时或进程提前退出，启动器会自动输出日志末尾；需要保留失败现场时在 `start` 传入 `--keep-on-failure`。
- **界面未反映最新前端代码**：确认未误传 `--skip-build`，或重跑不带 `--skip-build` 的 `start` 重新生成 `frontend/dist`。
- **停止与清理**：验证完成后运行 `uv run python scripts/local_instance.py stop --data-dir <data_dir>`。启动器会发送 `SIGTERM`（超时后升级为 `SIGKILL`）、校验监听端口已释放，并在核对 `.arcreel-local-instance` 哨兵后删除临时数据目录；需保留日志归档时加 `--keep-data-dir`。
