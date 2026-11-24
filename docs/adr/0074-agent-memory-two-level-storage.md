---
status: accepted
---

# 项目记忆使用 SDK 原生机制，用户记忆由 ArcReel 管理

SDK 原生记忆每个会话只有一个目录。ArcReel 将它用于变动较频繁的项目记忆，以保留 SDK 后台提取和索引超限提示。用户记忆跨项目共享，单独存储并在装配会话时注入索引。将原生提取用于用户记忆会把项目细节混入跨项目笔记。

项目记忆落在 `<项目目录>/.arcreel/memory/`，用户记忆在数据根下的定位以 [ADR 0088](0088-data-root-layered-projects-subdirectory.md) 为准。项目目录经 SDK 会话选项设置，无需写入项目设置文件或切换 Claude 配置根。项目 `.claude/` 与 `CLAUDE.md` 由 profile 三方同步与重建机制管理（见 [ADR 0128](0128-project-agent-profile-three-way-sync.md)），其中的文件无法承担持久记忆。

内置文件工具能在各自允许的记忆目录读写。项目记忆在项目工作目录内，可由 Bash 访问。用户记忆通过内置工具访问，Bash 沙箱仍以数据根边界隔离它。双层文件防护见 [ADR 0026](0026-agent-file-guard-two-layers.md)，实际路径由 [数据目录布局](../../lib/infra/data_root_layout.py) 和 [项目记忆路径](../../lib/agent/agent_memory_paths.py) 定义。
