---
paths:
  - "lib/**"
  - "server/**"
  - "packages/*/src/**"
---

# Windows 兼容

主开发平台是 macOS / Linux，server（连同它依赖的 workspace 子包）同时要在 Windows 原生环境完成项目创建与基础流程。CI 只跑 Linux，下面这些问题在 Linux 上全部静默通过，只会在用户的 Windows 机器上出现。

### POSIX 专属的 `os` 常量用 `getattr` 取值，不可信路径要校验文件身份

`O_NOFOLLOW`、`O_DIRECTORY` 等常量逐个用 `getattr(os, "<常量名>", 0)` 取值，直接引用会让模块在 Windows 上 import 失败。常量缺失时只能靠 `is_symlink()` 预检，而预检与随后的 `os.open()` 之间有 TOCTOU 窗口，提供不了 `O_NOFOLLOW` 的原子保证。按路径是否可信区分处理：可信路径（如 `lib/agent/profile_manifest.py` 的项目锁）可以只做预检；不可信路径打开后按 `st_dev` / `st_ino` 校验文件身份（参考 `lib/artifacts/artifact_manifest.py`），或者在 Windows 上拒绝操作。

### `os.chmod(0o600)` 包在 `if os.name == "posix":` 里

Windows 上凭证保护依赖用户级 `%LOCALAPPDATA%` 的 ACL，`chmod` 在那里不起保护作用。

### 文本文件 I/O 显式 `encoding="utf-8"`

省略时默认编码随平台与 locale 变化，Windows 上通常是 ANSI 代码页，读写中文内容时会出现乱码或抛出异常。

### 临时目录用 `tempfile.gettempdir()`

硬编码 `/tmp` 在 Windows 上不存在。

按前缀匹配 Claude SDK 的临时输出路径时，`gettempdir()` 的原始结果与 `.resolve()` 后的结果都要列出，再加上 `/tmp`、`/private/tmp` 两个字面前缀；macOS 上 `/tmp`、`/var` 是 `/private/...` 的符号链接，只列一种形态会让 `startswith` 失配。参考 `server/agent_runtime/agent_access_policy.py` 的 `_sdk_tmp_prefixes`。

### 子进程用 list 参数、不经 shell；ffmpeg 用随包二进制

异步代码用 `asyncio.create_subprocess_exec`，同步代码用 `subprocess.run`（`shell=False`）。ffmpeg 一律经 `lib/infra/ffmpeg.py` 的查找器取随包二进制，不查 PATH；媒体探测走 `lib/infra/media_probe.py`，不调用 ffprobe。Windows 用户的 PATH 上通常没有这两个程序。

### 依赖沙箱能力的 Agent 工具要有 Windows 出口

Windows 原生没有 Agent 沙箱，降级为 Bash 命令前缀白名单（`docs/adr/0025`、`docs/adr/0026`）。依赖沙箱专属能力的工具，要么提供 Windows 降级路径，要么在沙箱不可用时显式拒绝运行。
