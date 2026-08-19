---
paths:
  - "lib/**"
  - "server/**"
---

# Windows 兼容性

主开发平台是 macOS / Linux，server 须同时能在 Windows 原生环境完成项目创建与基础流程。涉及文件系统、子进程、临时目录、权限的代码遵循以下约定：

- **POSIX-only `os` 常量**：`O_NOFOLLOW` / `O_DIRECT` 等用 `getattr(os, "O_NOFOLLOW", 0)`。常量缺失时 `is_symlink()` 预检与随后的 `os.open()` 之间存在 TOCTOU 窗口，不能替代 `O_NOFOLLOW` 的原子保证：仅可信路径（如 `lib/profile_manifest.py` 的项目锁）可只做预检；不可信路径须在打开后按 `st_dev` / `st_ino` 校验文件身份（参考 `lib/artifact_manifest.py`），或在 Windows 上拒绝操作。
- **`os.chmod(0o600)`** 以 `if os.name == "posix":` 包裹；Windows 上凭证保护依赖 ACL（用户级 `%LOCALAPPDATA%`）。
- **文件 I/O 显式 `encoding="utf-8"`**，否则 Windows 默认 cp936/cp1252 会破坏 UTF-8 文本。
- **临时目录用 `tempfile.gettempdir()`**，不硬编码 `/tmp`；匹配 Claude SDK 临时输出时 tempdir 与 POSIX 别名须同时列出。
- **subprocess 一律 list 参数、不经 shell**：异步代码用 `asyncio.create_subprocess_exec`，同步代码用 `subprocess.run`（`shell=False`，带超时）；ffmpeg/ffprobe 先用 `shutil.which()` 探测，缺失时降级处理而非直接失败。
- **长路径**：Windows 10 1607+ 需 `LongPathsEnabled=1` 解除 MAX_PATH (260) 限制。

Agent 沙箱在 Windows 上的降级路径见 `.claude/rules/agent-runtime.md`。
