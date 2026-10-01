---
paths:
  - "lib/**"
  - "server/**"
---

# Windows 兼容

主开发平台是 macOS / Linux，server 同时要在 Windows 原生环境完成项目创建与基础流程。CI 只跑 Linux，下面这些问题在 Linux 上全部静默通过，只会在用户的 Windows 机器上出现。涉及文件系统、子进程、临时目录、权限的改动按这些规则审。

### POSIX-only 的 `os` 常量用 `getattr` 取值，不可信路径要校验文件身份

`O_NOFOLLOW`、`O_DIRECTORY` 等常量逐个用 `getattr(os, "<常量名>", 0)` 取值，直接引用在 Windows 上 import 即失败。常量缺失时，`is_symlink()` 预检与随后的 `os.open()` 之间有 TOCTOU 窗口，替代不了 `O_NOFOLLOW` 的原子保证。判定看路径是否可信：可信路径（如 `lib/agent/profile_manifest.py` 的项目锁）可以只做预检；不可信路径打开后按 `st_dev` / `st_ino` 校验文件身份（参考 `lib/artifacts/artifact_manifest.py`），或者在 Windows 上拒绝操作。

### `os.chmod(0o600)` 包在 `if os.name == "posix":` 里

Windows 上凭证保护依赖用户级 `%LOCALAPPDATA%` 的 ACL，`chmod` 在那里不起保护作用。

### 文本文件 I/O 显式 `encoding="utf-8"`

省略时默认编码随平台与 locale 变化，Windows 上通常是 ANSI 代码页，读写中文会损坏或抛错。

### 临时目录用 `tempfile.gettempdir()`

硬编码 `/tmp` 在 Windows 上不存在。匹配 Claude SDK 的临时输出路径时，tempdir 与 POSIX 别名同时列出。

### 子进程用 list 参数、不经 shell；ffmpeg 走随包二进制

异步代码用 `asyncio.create_subprocess_exec`，同步代码用 `subprocess.run`（`shell=False`）。ffmpeg 一律经 `lib/infra/ffmpeg.py` 的查找器取随包二进制，不查 PATH；媒体探测走 `lib/infra/media_probe.py`，不调用 ffprobe——Windows 用户的 PATH 上通常没有这两个程序。

### 依赖沙箱能力的 Agent 工具要有 Windows 出口

Windows 原生没有 Agent 沙箱，降级为 Bash 命令前缀白名单（`docs/adr/0025`、`docs/adr/0026`）。依赖沙箱专属能力的工具，要么提供 Windows 降级路径，要么在沙箱不可用时显式拒绝运行。
