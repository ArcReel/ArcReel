# MySQL 8 兼容性调研

> 状态：调研阶段。差距清单为工作清单，非完成报告。
> 关联：[#1983](https://github.com/ArcReel/ArcReel/issues/1983)（提案）、[#1985](https://github.com/ArcReel/ArcReel/pull/1985)（迁移/部署/文档第一批实现）

## 背景

ArcReel 当前支持 SQLite / PostgreSQL 双方言。本调研评估将 MySQL 8.0+ 纳入自托管可选后端的差距与路径。

## 当前状态

- [x] 调研完成，兼容性差距清单已整理（本文档）
- [ ] 引擎层适配（`lib/db/engine.py`）
- [ ] alembic 迁移修复
- [ ] CI: mysql-compat job
- [ ] 文档更新（`.env.example` + docs）

## 兼容性差距清单

以下条目按证据强度标注：**已复现** = 在 SQLite/PG 上实际观察到；**待验证** = 基于 MySQL 文档推断，尚未在真实 MySQL 8 上测试。

### 1. 表达式索引（已复现仅限 SQLite）

- SQLite 回滚重建表时丢失 expression index（见 [#1807](https://github.com/ArcReel/ArcReel/issues/1807)）
- MySQL 8 是否有同类问题：**待验证**。#1807 的证据仅覆盖 SQLite，不构成 MySQL 故障证据。验证时需记录：MySQL 版本、索引定义、迁移 revision、预期索引状态与实际结果。

### 2. 部分唯一索引（MySQL 已知不支持，替代方案待定）

项目内两处依赖 `sqlite_where` / `postgresql_where` 的部分唯一索引：

- `lib/db/models/task.py` 的 `idx_tasks_dedupe_active`（活跃任务去重：`COALESCE` 表达式列 + where 条件）
- `alembic/versions/2c57c41eca74_add_provider_credential_table.py` 的 `uq_provider_credential_one_active`（每 provider 仅一条 active 凭证）

MySQL 8 不支持部分索引：直接迁移会得到**无条件唯一索引**——前者导致去重失效或迁移失败，后者会阻止"多条 inactive 凭证"的既有数据通过迁移。候选替代：生成列（virtual generated column）+ 普通唯一索引。需为两处分别设计并测试。

### 3. 运行时 DML 方言分支（待补）

`lib/agent_session_store/store.py` 的批量插入只区分 postgresql / sqlite 两个分支，其余方言（含 MySQL）会走 SQLite 的 `on_conflict_do_nothing()`，在 MySQL 上编译失败。引擎层适配时需补 MySQL insert 策略（`INSERT IGNORE` 或 `ON DUPLICATE KEY`）及兼容测试。

### 4. JSON 类型（待验证）

SQLite 存文本 / PostgreSQL native JSON / MySQL 8 native JSON，三者读写路径需逐一验证。

### 5. server_default（初核无差异，待系统核对）

初核未发现 `text("NOW()")` 写法，现有迁移统一使用 `sa.func.now()`。需要系统核对的是：`func.now()` 在三方言下生成的实际 DDL（`CURRENT_TIMESTAMP` 变体）及时间精度（MySQL `DATETIME` 默认秒级 vs 项目要求的微秒）是否一致，逐迁移记录。

## 迁移验证计划

每个受影响迁移须通过 offline 与 online 两套标准：

```text
# offline：SQL 生成与内容检查（不连库）
alembic upgrade <start>:<end> --sql > upgrade.sql
alembic downgrade <end>:<start> --sql > downgrade.sql
# 检查生成的 SQL：方言差异、类型转换逻辑、降级回滚路径

# online：真实数据库执行闭环（需受控 MySQL 实例）
DATABASE_URL='mysql+aiomysql://arcreel:example_password@mysql:3306/arcreel?charset=utf8mb4' alembic upgrade head
DATABASE_URL='mysql+aiomysql://arcreel:example_password@mysql:3306/arcreel?charset=utf8mb4' alembic downgrade -1
DATABASE_URL='mysql+aiomysql://arcreel:example_password@mysql:3306/arcreel?charset=utf8mb4' alembic upgrade head
```

| 迁移 | Revision | 影响内容 | 验证范围 |
|---|---|---|---|
| 7a8b9c0d1e2f | unify_partial_indexes_and_string_lengths | 生成列 + 字段长度 | MySQL: 生成列语法正确、唯一索引语义等价；PG/SQLite: partial index 未被破坏 |
| bcaaa615ff38 | mysql_varchar_to_datetime | VARCHAR→DATETIME(6) | MySQL: 数据转换无精度丢失；PG/SQLite: 无操作 |

| 模式 | 定义 | 验证标准 |
|---|---|---|
| offline | 只渲染 SQL、不连接数据库 | upgrade/downgrade 脚本语法正确、降级路径可逆 |
| online | 连真实 MySQL 实例执行 | `upgrade → downgrade → upgrade` 闭环无错、最终 schema 与首次升级一致 |
| online（数据） | 加载代表性 legacy 数据后执行 | 既有数据经迁移后保持语义等价（如 provider_credential 的多条 inactive 记录仍能共存） |


## 后续

差距清单全部转为实现后，按 引擎层 → 迁移 → CI → 文档 顺序分 PR 推进；第一批（VARCHAR→DATETIME 迁移 / 部署模板 / 运维文档）见 #1985。
