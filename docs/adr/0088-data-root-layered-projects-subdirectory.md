---
status: accepted
---

# 数据根分层，项目集中在 projects/ 子目录

当项目目录与运行数据平铺在同一层级时，系统只能依靠文件名前缀来区分目录类型，导致各读取入口的判断容易出现分歧。数据根继续沿用部署者已配置、挂载并纳入备份的位置；所有项目统一收进 `projects/` 子目录，而数据库、全局资产库、用户数据、日志、凭证与运行时目录则各自独立存放。这样做无需在升级时调整卷挂载配置，但旧版本无法解析新的目录布局。

[DataRootLayout](../../lib/infra/data_root_layout.py) 持有目录定义。只有 `projects/` 下名称合法且带 `project.json` 的目录才是项目。ArcReel 的用户记忆从旧的 `.arcreel/users/` 迁到 `users/<user_id>/memory/`，项目内部的记忆仍按 [ADR 0074](0074-agent-memory-two-level-storage.md) 隔离。Claude SDK 自身的配置目录单独管理。Agent 对数据根默认无读取权，只为当前项目和用户记忆放行，见 [ADR 0026](0026-agent-file-guard-two-layers.md)。
