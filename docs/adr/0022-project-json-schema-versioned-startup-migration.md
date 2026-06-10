---
status: accepted
---

# project.json 文件级 schema 版本化 + 启动时逐级幂等迁移

不版本化、靠读时即时兼容旧字段虽零迁移成本，但旧形状字段会无限期残留、读路径分支越积越多。决定为 project.json 引入顶层 `schema_version` + `lib/project_migrations` 注册表，启动时扫各项目逐级跑纯函数 migrator（迁移前备份、原子写回、级联迁移剧本），单项目失败隔离不中断启动——显式版本化换取数据形状收敛与一次性改写，胜过读路径无限累积兼容分支。

## Consequences

- 引入备份 / 原子写 / 失败隔离 / 幂等机制复杂度，且迁移就地改写文件不可逆（如 v0→v1 拆 clue 为 scene/prop 并删 importance；v1→v2 归一化 provider）。
- 同一迁移链不只在启动期跑：项目归档**导入**路径也复用 `migrate_project_dir`，因为启动期 runner 只覆盖启动时已存在的项目，启动后导入的旧归档需在导入入口补跑完整链。
