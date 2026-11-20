# 维护供应商 API 文档索引

按任务需要，从[供应商索引](providers/README.md)或[调用端点索引](endpoints/README.md)查找官方文档和代码入口。这些索引用于核验上游依据：模型事实以模型目录为准，通道事实以调用端点与请求实现为准。选择依据见 [ADR 0060](../adr/0060-official-links-replace-vendored-api-docs.md)。

- 按规范 ID 定位文件。开放给自定义供应商的随版端点均在 `ENDPOINT_REGISTRY`；仅内置供应商可用的端点从供应商页定位，需独立索引的协议在端点目录单列。
- 核验接口、能力约束与价格时，定位官方文档相应章节并链接该页面。代码入口用 `path::symbol` 指向实际读取这些字段的实现。新增、移除或改名时同步更新所在索引。
- 「简介来源」仅用于 `lib/i18n/<locale>/providers.py` 中的 `provider_desc_<id>`。简介只写平台是什么，来源优先查 Wikipedia 或 Wikidata，缺失或过时时查官方说明。
- 数值和完整协议正文查官方原文，运行时实际使用的数据查端点声明。测试只保存所需的最小响应 fixture。本地索引保留来源链接和所用主题。
