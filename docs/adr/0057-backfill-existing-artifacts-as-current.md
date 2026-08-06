# 迁移时将既有产物认定为 current

项目 schema 迁移到引入 provenance 的版本时，把项目内安全可读的既有正式产物按迁移时内容写入 manifest 并直接认定为 current，不持久化额外的 legacy 标记。旧产物无法证明其历史生成参数，但将其全部标为 stale 会诱发大规模重生成和费用；兼容处理集中在现有 `project_migrations` 迁移链的一步中，启动期项目与后续导入归档共用，迁移完成后的所有时效判断只遵循统一 provenance 规则。
