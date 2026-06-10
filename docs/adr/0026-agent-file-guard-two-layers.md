---
status: accepted
---

# Agent 文件防护双层：内核沙箱管 Bash 子进程树，内置文件工具走 PreToolUse hook

内核沙箱（`SandboxSettings.filesystem.denyRead/denyWrite`）只约束 Bash 工具及其派生的全部子进程；SDK 内置的 Read/Write/Edit/Glob/Grep 不经 Bash、在主进程内直接执行，内核沙箱无法覆盖。决定对内置文件工具用应用层 PreToolUse hook（`_is_path_allowed`：敏感文件、跨项目读写、代码扩展名、cwd 越界）补上第二层，两层覆盖同源的路径规则。拦截点必须是 PreToolUse hook 而不是 `can_use_tool`：SDK 权限链中 Read/Glob/Grep 会先被 allow 规则放行，不会到达 `can_use_tool`。

## Consequences

- 评审涉及 agent 文件访问的改动需分别确认：Bash 路径能否拦截（沙箱 profile）、内置工具路径能否拦截（hook）——任何一层单独存在都有旁路。
- `docs/adr/0003` 是这套双层在「项目 JSON 写入」上的具体应用（denyWrite 与 `_check_write_access` 同源双层）。
