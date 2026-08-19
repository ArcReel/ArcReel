---
paths:
  - "lib/**"
  - "server/**"
---

# Windows 兼容性

主开发平台是 macOS / Linux，server 同时须在 Windows 原生完成项目创建与基础流程。涉及文件系统、子进程、tmp 路径、权限的代码遵循：

- **POSIX-only `os` 常量**：`O_NOFOLLOW` / `O_DIRECT` 等用 `getattr(os, "O_NOFOLLOW", 0)`，Python 层 `is_symlink()` 兜底。
- **`os.chmod(0o600)`** 以 `if os.name == "posix":` 包裹；Windows 凭证保护交给 ACL（用户级 `%LOCALAPPDATA%`）。
- **文件 I/O 显式 `encoding="utf-8"`**，否则 Windows 默认 cp936/cp1252 会破坏 UTF-8 文本。
- **tmp 路径用 `tempfile.gettempdir()`**；匹配 Claude SDK tmp 输出时 tempdir 与 POSIX 别名须同时列出。
- **subprocess 用 `create_subprocess_exec`（list 形式）**；ffmpeg/ffprobe 先 `shutil.which()` 探测，缺失时降级处理。
- **长路径**：Windows 10 1607+ 需 `LongPathsEnabled=1` 解除 MAX_PATH (260) 限制。

Agent 沙箱在 Windows 的降级见 `.claude/rules/agent-runtime.md`。
