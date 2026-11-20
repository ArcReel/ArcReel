---
status: accepted
---

# 项目结构以文件级版本逐步迁移

在 `project.json` 记录 `schema_version`。应用启动和归档导入复用迁移链，逐级就地迁移并保留版本化备份，失败隔离在单个项目。显式迁移增加写入与备份成本，换取长期收敛数据形状和读路径。因数据问题拒绝项目与迁移执行失败的边界见 [ADR 0098](0098-backfill-keeps-existing-content-gaps-only-gate-generation.md)。

多文件迁移需要先完成只读预检。v5→v6 在同父目录的暂存树中改写文件，经两次改名安装结果，进程内失败时恢复原项目。若进程在两次改名之间被强制终止，保留原项目的 rollback 目录供人工恢复。清理备份只处理原项目目录仍在位的暂存与 rollback 目录。恢复操作见[部署文档](../../website/docs/ops/deployment.md#project-schema-migrations)，迁移步的编写和版本兼容表见[项目 schema 迁移](../agents/project-migrations.md)。
