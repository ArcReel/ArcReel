---
status: accepted
---

# Agent 经领域工具写入项目 JSON

直接写剧本和 `project.json` 曾只检查 JSON 语法，绕过剧本结构校验和项目资产校验。Agent 现在通过服务端领域工具写入。内置文件工具由权限 hook 检查，沙箱子进程由文件写入规则保护，两层从同一处访问策略配置派生，见 [ADR 0026](0026-agent-file-guard-two-layers.md)。服务端工具使用与其他写入方相同的校验入口，剧本保存的兼容策略见 [ADR 0002](0002-save-validation-no-worse-semantics.md)。

批量结构编辑以读取时的剧本修订为条件，在锁内对整批候选内容做结构、引用与适用的发声预检。预检失败时整批零写入，未被本批改动的历史发声问题可继续保留。剧本、集索引及产物清单的更新在同一临界区编排，后一步失败时补偿已写内容。读到旧骨架时按数据形状解析，使历史剧本可编辑，生成仍受项目路线约束，见 [ADR 0045](0045-script-skeleton-registry-dual-resolvers.md)。

新增或拆分结构时会分配新的条目 ID，其中拆分后的首项沿用原 ID 及其已有产物。修改正文或提示词只改变内容，产物时效在读取时动态计算。结构移除连带的媒体与版本进入项目回收站，见 [ADR 0062](0062-artifact-lifecycle-and-currency.md) 和 [ADR 0107](0107-deletion-goes-to-trash-restore-only-adds.md)。
