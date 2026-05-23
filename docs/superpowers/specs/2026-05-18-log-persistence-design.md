# 日志持久化设计

**日期**: 2026-05-18
**分支**: feature/log-persistence

## 背景

当前日志系统（`lib/logging_config.py`）只挂了一个 `StreamHandler` 到 stdout：

- 进程结束 / 容器重启后日志全部丢失
- `LOG_LEVEL` env 可调，默认 INFO
- 请求日志由 `server/app.py:472` middleware 自定义输出（已对轮询接口降级到 DEBUG）
- `lib/logging_utils.py` 已实现 secret 脱敏 + 大字段截断

业务侧已有结构化持久化（`Task` / `ApiCall` 表）覆盖任务和 API 调用维度，但应用层日志（异常栈、Worker 状态、provider 探测、profile 同步等）一旦进程退出就无从查证，对单机 / 桌面端用户尤其痛。

## 目标

1. **保留 stdout 行为**：现有部署（Docker / journald / `docker logs`）零影响。
2. **额外落盘**：所有 Python `logging` 输出按天写入文件，保留 7 份。
3. **用户友好的诊断包**：单机用户能通过 WebUI 一键下载「日志 + 系统诊断信息」zip，发给开发者反馈 bug。

## 非目标（YAGNI）

- JSON 行 / 结构化日志格式（未来对接 loki / datadog 时再做）
- WebUI 日志查看页面（带级别过滤、时间筛选）
- 按日期范围参数化下载
- 多进程 / 多 worker 日志锁（生产推荐外部收集，单 worker 不需要）
- 业务事件入库（`Task` / `ApiCall` 已覆盖）
- gzip 压缩历史日志

## 架构

### 数据流

```
应用代码 logger.info(...)
   │
   ├─ StreamHandler → stdout（既有，不变）
   └─ TimedRotatingFileHandler → PROJECT_ROOT/logs/arcreel.log
            │
            └─ 每日 midnight 切到 arcreel.log.YYYY-MM-DD，最多 7 份

用户在 Settings 点「下载诊断日志」
   │
   GET /api/v1/system/logs/download
   │
   └─ ZipFile 流式打包 logs/arcreel.log* + diagnostics.txt
      → 返回 application/zip
```

### 组件改动一览

| 文件 | 类型 | 改动 |
|-----|-----|-----|
| `lib/logging_config.py` | 改 | 增挂 `TimedRotatingFileHandler` + 目录解析逻辑 |
| `server/services/diagnostics.py` | 新 | `collect_diagnostics()` 收集脱敏的系统信息 |
| `server/routers/system.py` | 新 | `GET /api/v1/system/logs/download` 端点（与现有 `system_config.py` 用途不同，独立新文件） |
| `server/app.py` | 改 | 注册 `system` router |
| `frontend/src/components/pages/settings/AboutSection.tsx` | 改 | 新增「下载诊断日志」按钮 |
| `frontend/src/i18n/{zh,en,vi}/dashboard.ts` | 改 | 三语 i18n key |
| `.env.example` | 改 | 注释化新 env 变量 |
| `tests/test_logging_persistence.py` | 新 | 文件 handler 行为测试 |
| `tests/test_system_logs_router.py` | 新 | 端点行为测试 |

## 详细设计

### 1. `lib/logging_config.py` — 增挂文件 handler

在现有 `setup_logging()` 流程中，stream handler 之后挂 file handler：

- 类型：`logging.handlers.TimedRotatingFileHandler`
- 参数：`when="midnight"`, `backupCount=7`, `encoding="utf-8"`, `utc=False`
- 复用现有 formatter（与 stdout 完全一致）
- 标记 `_HANDLER_ATTR = True` 保证幂等
- 日志目录由新增函数 `resolve_log_dir()` 决定：
  1. `ARCREEL_LOG_DIR` env（绝对路径或相对 `PROJECT_ROOT`）
  2. 默认 `PROJECT_ROOT / "logs"`
- 不放在 `app_data_dir()` 下：那一层同时是 `projects_root`，目录被枚举为视频项目列表，无前缀的兄弟目录会被错认为项目（容器部署用 `./logs:/app/logs` 单独挂出来）
- 升级路径：`lib.logging_config.migrate_legacy_log_dir()` 在 `setup_logging()` 之前调用，把旧 `app_data_dir()/logs` 平移到新位置；新旧并存时告警保留
- 首次启动 `mkdir(parents=True, exist_ok=True)`
- 逃生口：`ARCREEL_LOG_FILE_DISABLED` 取值 `1` / `true` / `yes`（大小写不敏感）时跳过 file handler 注册
- **容错**：mkdir 或 FileHandler 构造异常时 catch + `logging.getLogger(__name__).warning("file logging disabled: %s", exc)`，stdout 继续工作 — 日志辅助逻辑不阻塞主流程

### 2. `server/services/diagnostics.py` — 新文件

```python
def collect_diagnostics() -> str:
    """返回 plain-text 诊断报告，敏感字段脱敏。"""
```

报告字段（每行 `key: value`）：

- App 版本（读 `pyproject.toml` 的 `[project] version`；失败时 `<unknown>`）
- Python 版本（`sys.version` 单行版）
- OS（`platform.platform()`）
- 应用数据目录（`app_data_dir()`）
- 日志目录（`resolve_log_dir()`）
- DB URL（脱敏 `user:password@` 形式 + query 参数中的敏感键如 `password` / `token` / `secret` / `api_key`）
- `LOG_LEVEL`
- 启用的 provider 列表（仅 `id` + `type`，不含 key）
- Sandbox 状态（`check_sandbox_available()` 返回值）
- 报告生成时间（ISO 8601）

敏感字段统一经 `lib.logging_utils._mask_secret` 处理。任一字段查询抛异常 → 替换为 `<unavailable: {exc}>`，整个函数不抛。

### 3. `server/routers/system.py` — 新 router

端点：`GET /api/v1/system/logs/download`

依赖：现有 auth dependency（沿用 `server/routers/auth.py` 的 `get_current_user`，单用户模型 = admin-only）。

实现：

1. 用 `tempfile.SpooledTemporaryFile(max_size=50 * 1024 * 1024)` 作为 zip 缓冲：< 50 MB 时全在内存；超出自动溢出到磁盘临时文件，避免极端情况下 8 × 100 MB ≈ 800 MB 全在堆里
2. 遍历 `resolve_log_dir()` 下所有 `arcreel.log*` 文件
   - **跳过符号链接**（`path.is_symlink()`）—— 防止有人在 logs/ 下放 symlink 指向目录外敏感文件经诊断包外泄
   - 单文件 > 100 MB 跳过，把 `[skipped: too large: <name> ({size} bytes)]` 追加进诊断文本
3. 调用 `collect_diagnostics()` 写入 `diagnostics.txt`
4. zip 写完后 `seek(0)`，包装成 `StreamingResponse(spooled_file, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="arcreel-diagnostics-{ts}.zip"'})`；StreamingResponse 消费完后 SpooledTemporaryFile 自动关闭并删除磁盘 backing file

文件名格式：`arcreel-diagnostics-YYYY-MM-DD-HHMM.zip`（本地时区即可）。

注册：`server/app.py` 中 `app.include_router(system.router, prefix="/api/v1", tags=["系统"])`。

### 4. 前端 Settings 按钮

位置：`frontend/src/components/pages/settings/AboutSection.tsx`（实施时由 plan 确认）。

UI：一行带说明文字的按钮，i18n 文案：

- `settings.diagnostics.title` → "诊断日志" / "Diagnostic logs" / "Nhật ký chẩn đoán"
- `settings.diagnostics.description` → 一句解释（含最多 7 天 + 已脱敏密钥的承诺）
- `settings.diagnostics.downloadButton` → "下载诊断日志" / "Download diagnostic logs" / 越南语
- `settings.diagnostics.downloadError` → "下载失败：{error}" / ...

行为（伪代码）：

```ts
async function downloadDiagnostics() {
  const res = await fetch("/api/v1/system/logs/download", {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(await res.text());
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match?.[1] ?? "diagnostics.zip";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
```

**注意**：不能用 `window.location.href = ...` —— 那种方式不会带 Authorization header，会 401。

### 5. `.env.example` 补充

在现有 `# Logging` 区块下追加注释化变量：

```bash
# Log file directory (default: $PROJECT_ROOT/logs)
# Relative paths resolve against PROJECT_ROOT. Logs intentionally live OUTSIDE
# the projects root: that root is enumerated as a list of video projects, so any
# stray sibling directory would surface as a fake project in the UI.
# 日志文件目录（默认 $PROJECT_ROOT/logs），相对路径基于 PROJECT_ROOT。
# 刻意不放在项目根下：项目根会被枚举为视频项目列表，旁系目录会被错认为项目。
# ARCREEL_LOG_DIR=

# Disable file logging (default: false). When set to 1/true/yes, logs go only to stdout.
# 关闭文件日志（默认 false）。设为 1/true/yes 时日志仅输出到 stdout。
# ARCREEL_LOG_FILE_DISABLED=
```

## 错误处理

| 场景 | 行为 |
|-----|-----|
| 日志目录无写权限 / mkdir 失败 | catch + warning，stdout 继续，file handler 不挂 |
| `TimedRotatingFileHandler` 构造异常 | 同上 |
| 下载请求时 logs 目录为空 | 返回仅含 `diagnostics.txt` 的 zip，不 404 |
| 单个日志文件 > 100 MB | 跳过并在 `diagnostics.txt` 标注，避免 OOM |
| `collect_diagnostics()` 内部某字段抛 | 该字段替换为 `<unavailable: {exc}>`，不阻塞下载 |
| 下载端点本身异常 | FastAPI 默认 500 + 异常被现有 `request_logging_middleware` 捕获记录 |

## 平台兼容

- **Windows**：`TimedRotatingFileHandler` 单进程写无锁问题（uvicorn 默认 worker=1）；路径用 pathlib，IO 显式 `encoding="utf-8"`；zip 用标准 `ZipFile`（自带 UTF-8 文件名支持）
- **Sandbox**：日志写在 `PROJECT_ROOT/logs` 下，作为 server 进程写路径需在 bwrap 白名单中（默认 PROJECT_ROOT 整树可写已涵盖）；同时该路径在 `SessionManager._compute_sensitive_paths` 里登记为 sensitive prefix，agent 工具无法 Read/Grep 全局日志
- **Docker / journald**：stdout 不变，外部收集器零感知

## 测试

### `tests/test_logging_persistence.py`

| 用例 | 覆盖 |
|-----|-----|
| `test_file_handler_registered_by_default` | `setup_logging()` 后 root.handlers 含 `TimedRotatingFileHandler` |
| `test_file_handler_disabled_by_env` | `ARCREEL_LOG_FILE_DISABLED=1` 时不注册 |
| `test_logs_written_to_file` | `logger.info("hello")` 后日志文件包含 `hello` |
| `test_mkdir_failure_graceful` | monkeypatch `Path.mkdir` 抛 PermissionError，`setup_logging()` 不抛、stdout handler 仍存在 |
| `test_custom_log_dir` | `ARCREEL_LOG_DIR=tmp_path/foo` 生效 |
| `test_idempotent` | 多次调用 `setup_logging()` 不重复挂 handler |

### `tests/test_system_logs_router.py`

| 用例 | 覆盖 |
|-----|-----|
| `test_download_requires_auth` | 未鉴权 → 401 |
| `test_download_returns_zip` | 已鉴权 → 200 + `Content-Type: application/zip` |
| `test_zip_contains_diagnostics` | 解 zip 后含 `diagnostics.txt` 且非空 |
| `test_zip_includes_log_files` | 预先写入日志后，zip 内含对应 `.log*` 文件 |
| `test_diagnostics_masks_secrets` | 注入带 API key 的 provider，zip 内 `diagnostics.txt` 不含明文 |
| `test_empty_logs_dir` | logs 目录为空时仍返回 zip（只含 `diagnostics.txt`） |
| `test_oversized_file_skipped` | 单文件 > 100 MB 时被跳过且 `diagnostics.txt` 标注 |

## 实施顺序

1. `lib/logging_config.py` 改造 + `tests/test_logging_persistence.py`
2. `server/services/diagnostics.py` + 单元测试（独立于 router）
3. `server/routers/system.py` 端点 + `tests/test_system_logs_router.py`
4. `server/app.py` 注册 router
5. 前端 Settings 按钮 + i18n 三语 key
6. `.env.example` 补充注释
7. CHANGELOG 记一行

## 风险与缓解

| 风险 | 缓解 |
|-----|-----|
| 日志文件意外写满磁盘 | `backupCount=7` 严格上限；INFO 级别下日常 < 50 MB/天 |
| 日志文件含敏感信息（用户 prompt、provider key） | 现有 `lib/logging_utils.py` 已截断 + 脱敏；新代码继续走 `format_kwargs_for_log` |
| 下载端点被恶意爬取 | 沿用 auth dependency；单用户模型 = admin-only |
| 老用户磁盘空间不足 | 提供 `ARCREEL_LOG_FILE_DISABLED=1` 逃生口 |
| 多 worker 部署日志互写 | 文档明确「目前支持 worker=1」；未来若多 worker，再换 `concurrent-log-handler` |
