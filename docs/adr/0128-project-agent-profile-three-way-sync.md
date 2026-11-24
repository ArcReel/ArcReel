---
status: accepted
---

# 项目 Agent profile 采用基于基线清单的三方同步

每个项目目录下物化的内置 Agent 配置覆盖 `.claude/` 与 `CLAUDE.md`，需要同时满足两项约束：随版本升级下发内置指令修订，并保留创作者在项目内对指令的修改与主动删除。每次打开项目都全量覆盖会静默丢弃本地定制，永不覆盖又会让旧项目永久滞留在过期指令上。

同步入口以当前内置源（按项目 `content_mode` 解析出的逻辑路径映射）、项目目录现有文件以及上次物化记录的基线清单（manifest）三方决议，实现见 [profile_manifest.py](../../lib/agent/profile_manifest.py)：

- 项目文件摘要仍等于基线记录时，上游更新则替换升级，上游移除则删除项目文件并转为 `tombstone`。
- 项目文件摘要偏离基线时视为本地定制，保留项目文件不覆盖；即使上游已移除该内置文件，仍保留原有 `active` 基线条目，使设置页继续识别它是从内置 profile 分叉的定制，而非无来源的仅项目文件。
- 内置源仍在而项目侧删除了受管文件时，将基线转为 `tombstone`，后续同步不再补回；若项目侧之后重新放入同名文件，则清除 `tombstone` 并按仅项目文件保留。
- 此前无基线记录的同名项目文件，仅在内容与内置源完全一致时建立 `active` 记录，否则保留原文件不覆盖。

当清单缺失、损坏、schema 版本或 `profile_id` 不匹配，或清单已记录非空 `content_mode` 且与本次请求的 `content_mode` 不同时，同步按当前投影全量重建：先将待舍弃的定制常规文件移入项目回收站（见 [ADR 0107](0107-deletion-goes-to-trash-restore-only-adds.md)），再清空并写入内置投影与新清单。旧清单缺少 `content_mode` 时不触发全量重建，继续逐文件三方同步并在写回时补填当前模式。

本同步边界仅覆盖 `.claude/` 与 `CLAUDE.md`，不管理存放在 `.arcreel/memory/` 的项目记忆，记忆隔离边界见 [ADR 0074](0074-agent-memory-two-level-storage.md)。
