# 文档维护

## 先找事实来源

| 要查什么 | 从哪里开始 |
| --- | --- |
| 领域概念、规范用词和易混概念 | [术语表](../../GLOSSARY.md)的相关章节 |
| 当前行为、配置默认值、输入限制 | 生产入口、配置声明、测试和界面词条 |
| 跨模块决策及其取舍 | [ADR](../adr/)中与任务相关的记录，注意一并查找同主题的后续决定，并核对适用范围 |
| 供应商协议或限制 | [供应商](../api-docs/providers/README.md)与[调用端点](../api-docs/endpoints/README.md)索引中的官方资料 |
| 调研证据、验证边界和历史观察 | [研究索引](../research/README.md) |
| 未交付需求和验收条款 | [GitHub 议题](issue-tracker.md)中的 Spec 与 ticket |
| 用户如何完成任务 | [文档站](../../website/docs/)对应指南。开发入口见[贡献指南](../../CONTRIBUTING.md) |

## 从任务定位关键决策

- 解析供应商模型、判断输入可用性、解释费用 → [模型目录](../adr/0099-model-catalog-shared-entries-per-model.md)、[能力来源](../adr/0104-capability-sources-split-by-dimension.md)、[价目表](../adr/0100-price-table-meters-and-dimensions.md)。
- 处理剧本、内容确认或提示词编写 → [正式脚本与视觉提示词](../adr/0080-review-confirm-materializes-script-prompt-authoring-fills-pending.md)、[视频单元](../adr/0064-video-unit-body-is-the-only-truth.md)、[发声归属](../adr/0089-single-speech-owner-per-video-unit-narration-delivery-per-project.md)。
- 判断产物时效、迁移或恢复项目 → [产物生命周期](../adr/0062-artifact-lifecycle-and-currency.md)、[存量补录](../adr/0098-backfill-keeps-existing-content-gaps-only-gate-generation.md)、[项目归档](../adr/0119-archives-carry-content-not-storage-locators.md)。
- 更换素材存储或供应商素材关联 → [素材存储与供应商素材关联](../adr/0108-material-storage-independent-of-provider-registration.md)、[素材关联与入库](../adr/0111-asset-upload-job-and-link-share-one-record.md)、[远端内容版本](../adr/0116-remote-content-versions-saved-by-formal-commit-with-object-references.md)。
- 提交、取消或接续生成 → [批量视频准入](../adr/0061-batch-video-admission-all-or-nothing.md)、[排队任务取消](../adr/0006-cancel-only-queued-tasks.md)、[在途任务恢复](../adr/0007-orphan-tasks-not-requeued-on-restart.md)、[任务活跃快照与资产占用](../adr/0126-task-active-snapshot-and-asset-occupancy-guards.md)。
- 修改会话、工具或凭证边界 → [会话运行时](../adr/0046-session-manager-decomposition-boundaries.md)、[工具声明](../adr/0087-agent-toolset-single-declaration.md)、[凭证隔离](../adr/0027-provider-secrets-banned-from-process-env.md)。

## 把内容写回负责的位置

- **术语表**定义 ArcReel 领域概念和规范名称。用一两句写清概念及关键区别。调整术语时核对用户界面、公开文档与创作 Agent 指令的用词。多份术语表只在存在独立领域语义时采用，以 `GLOSSARY-MAP.md` 指向各自的词表。
- **ADR**记难逆转、需要背景才能理解、涉及真实取舍的决定。对同一决策的局部修订融入负责该决策的记录，修正入链并删掉重复文件。需要保留独立取舍或解释历史数据时，标明适用范围和后续决定。临时实施步骤和进度由 Git 与议题记录。
- **规范与运行指令**只保留实际有效的约定，靠近执行入口。内嵌创作 Agent 指令属于产品运行时，迁移说明前先验证目标文件随 profile 分发且能被读到。
- **第三方 skill** 按 [skills-lock.json](../../skills-lock.json) 中的来源维护。ArcReel 特有约定写在仓库入口、规范或自有指令中。
- **研究**保留仍有复用价值的证据、出处和观察边界。写明时间或适用版本。落地后的当前行为回到对应的公开文档、约定或代码。
- **公开文档**按读者任务组织。一个主题有一个维护位置，其他入口链接到相关段落。操作步骤按实际界面、命令、配置和权限验证。

## 随改动同步多语文档

中英文 README、贡献指南、文档站页面和站点文案随同一 PR 维护。以中文原文为准，对照对应译文更新新增、修改、移动和删除的内容。保持显式锚点、标识符、命令及配置示例一致，链接到读者当前语言的页面。贡献指南的中文站点副本由构建生成，编辑根目录的 [CONTRIBUTING.md](../../CONTRIBUTING.md)。

在仓库根目录执行：

```bash
node website/scripts/translation-lock.mjs status
node website/scripts/translation-lock.mjs record <已核对的源文路径>
pnpm --dir website check
```

`status` 列出异常及对应译文。逐对核对内容后，`record` 更新已确认的源文指纹。同时删除源文和译文时，从 `website/i18n/translation.lock.json` 删除对应源文的条目。编辑站点文案时，用 `pnpm --dir website write-translations --locale en` 生成键，补齐[英文文案](../../website/i18n/en/)，运行 `node website/scripts/ui-messages.mjs` 检查。修改页面结构、链接或导航时，另跑 `pnpm --dir website build` 验证双语构建。

## 盘点与清理

按主题从入口顺着引用一路读到当前事实来源；遇到冲突时核对代码、测试、有效 ADR 和一手资料，再改写负责该主题的页面。合并重复说明，删除失效步骤、无人消费的过程记录和可由现有资料还原的旧副本。删除、移动或改名时，检查站点路由、锚点、仓库入链、脚本和 Agent 是否仍消费该路径。验证一次真实的读者任务从入口能走到可执行的说明。
