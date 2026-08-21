# MySQL 8 兼容性支持

## 背景

参见 issue [#1983](https://github.com/ArcReel/ArcReel/issues/1983)

## 当前状态

- [x] 调研完成，兼容性差距清单已整理
- [ ] 引擎层适配（lib/db/engine.py）
- [ ] alembic 迁移修复
- [ ] CI: mysql-compat job
- [ ] 文档更新（.env.example + docs）

## 迁移兼容性差距清单

### 已知差异点

1. **alembic 表达式索引**（见 issue #1807）
   - SQLite 回滚时丢失 expression index
   - MySQL 可能有类似问题，需测试

2. **JSON 类型处理**
   - SQLite: JSON 存为文本
   - PostgreSQL: native JSON/JSONB
   - MySQL 8: native JSON，但语法差异

3. **server_default 方言差异**
   - PostgreSQL: server_default=text("NOW()")
   - MySQL: server_default=func.now()

4. **部分索引 (Partial Index) 限制**
   - SQLite / PostgreSQL: 支持部分索引（如 sqlite_where / postgresql_where）
   - MySQL 8: 不支持部分索引。项目中 idx_tasks_dedupe_active 依赖部分索引实现活跃任务去重，在 MySQL 8 中需要寻找替代方案（例如通过虚拟列 Virtual Column 配合唯一索引实现）

### 需要测试的迁移

待逐一跑 alembic downgrade/upgrade 验证。
