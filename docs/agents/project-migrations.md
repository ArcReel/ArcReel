# 项目结构迁移

新增项目结构迁移，或调整[产物补录规划器](../../lib/artifacts/artifact_planner.py)时读本文。迁移入口与版本顺序见 [runner](../../lib/project/project_migrations/runner.py)，用户可见的备份、报告和重试行为见[部署指南](../../website/docs/ops/deployment.md#project-schema-migrations)。

## 实施步骤

1. 用[已知旧形态](#已知旧形态)定位相关规则，收集所有最早仍受影响的版本及其变体。用[旧项目样本](../../tests/legacy_project_shapes.py)构造复现，并登记到 `LEGACY_PROJECT_SHAPES`。媒体内容要能区分不同字段，缺失路径要能探测误绑到其他文件的情况。
2. 比较当前 `CURRENT_PROJECT_SCHEMA_VERSION` 与最近的 `v*` 发版 tag 中的值。每个发行版最多加一步迁移，已有未发版的迁移步时合并进去。版本化备份用 [backups.py](../../lib/project/project_migrations/backups.py)，自行备份的步骤登记到 runner。存在内容冲突时，以保留副本保存被替下的文件，并把路径写进调整记录。
3. 用[迁移集成测试](../../tests/integration/lib/project/project_migrations/)从已发版的样本跑完整的 `migrate_project_dir` 迁移链。至少有一条用例通过 `WorkflowStateService` 或相应读模型验证用户看到的计数、状态或预览。[媒体字段保真用例](../../tests/integration/server/services/project/test_legacy_media_preservation.py)对登记的样本同时跑完整原地迁移和带信封的 ZIP 导入。
4. 用户可见的迁移行为变了，同步更新[部署指南](../../website/docs/ops/deployment.md#project-schema-migrations)及译文。

## 已知旧形态

按数据类型定位迁移实现，并结合旧项目样本与集成测试确认具体版本、输入和输出结果。

| 旧数据 | 核对入口 |
| --- | --- |
| 非规范剧本绑定、重复集号或条目编号、重名资产、跨资产共用文件、新旧脚本规划草稿名并存 | [链首规整](../../lib/project/project_migrations/legacy_shapes.py)。更早的共用文件在[资产名称空间迁移](../../lib/project/project_migrations/v5_to_v6_asset_namespace.py)的暂存树中先规整。 |
| 视频版本记录缺来源或时长为字符串、旁白版本记录缺 TTS 设置、旧草稿或源文件名 | [旧媒体来源补录](../../lib/project/project_migrations/v12_to_v13_legacy_media_provenance.py)与[版本来源解析](../../lib/project/legacy_media_provenance.py)。 |
| 广告旧镜头索引、旧参考生视频单元与重复编号 | [v6→v7](../../lib/project/project_migrations/v6_to_v7_ad_reference_video_units.py)、[v8→v9](../../lib/project/project_migrations/v8_to_v9_reference_unit_text.py)。 |
| 旧风格值、缺正式剧本、旧规划指纹、正式剧本缺字段 | [风格快照迁移](../../lib/project/project_migrations/v13_to_v14_legacy_style_values.py)、[正式剧本迁移](../../lib/project/project_migrations/v14_to_v15_formal_script_truth.py)。 |
| 旧转场、旁白交付、上传媒体登记、集号与整本源文 | [剪辑与集账本迁移](../../lib/project/project_migrations/v15_to_v16_edit_decisions.py)。 |
| 旧模型 ID、裸供应商引用、无后继的退役模型 | [退役文本模型](../../lib/project/project_migrations/v16_to_v17_retired_text_model_ids.py)、[退役模型冻结映射](../../lib/project/project_migrations/v17_to_v18_timeline_agent_turn.py)、[裸供应商模型迁移](../../lib/project/project_migrations/v19_to_v20_bare_provider_models.py)。 |
| 生成输入有缺口、产物缺内容指纹、时间线带 `agent_turn` | [v17→v18](../../lib/project/project_migrations/v17_to_v18_timeline_agent_turn.py)、[缺口补录](../../lib/project/project_migrations/input_gap_backfill.py)、[内容指纹补录](../../lib/project/project_migrations/content_digest_backfill.py)。 |
| 条目删后重用编号、分镜或宫格依据仍含资产名 | [条目编号与依据迁移](../../lib/project/project_migrations/v18_to_v19_item_id_high_water.py)。 |
| 各集遗留的隔离草稿 | [v20→v21](../../lib/project/project_migrations/v20_to_v21_retired_drafts.py) 将文件原样移入项目回收站。恢复删除记录时，暂存树保留这些文件供恢复入口取回。 |
| 不可读的媒体、宫格或草稿、无法计算依据、导入引用未定义资产 | [规划器激活模式](../../lib/artifacts/artifact_planner.py)、[迁移报告](../../lib/project/project_migration_report.py)、[归档导入](../../server/services/project/project_archive.py)。 |

## 当前约定

- 在生产流程写出的旧形态统一在链首规整，产物规划与各版本迁移消费规整后的数据。激活容错只用于迁移和归档导入，运行期读写继续使用正常检查。
- 保持媒体归属和字节。可读字段迁移后仍指向同一内容，悬空字段继续保持不可读。两个集使用同一编号时，归属集保留媒体，换号条目清空原编号的引用。归档导入可用已有的规范文件恢复缺失的原引用，原地迁移保持该字段不可读。归档导入无法恢复角色原图时，校验错误需要指出具体字段。
- 首次激活走 [activate_artifact_target_state](../../lib/artifacts/artifact_activation.py) 的只读预检，随后落盘。修改依据输入时分别规划改前和改后的目标态，只改写原先时新且登记有变化的条目。规划放宽时，只补录改前没有目标、改后有目标且清单中没有登记的条目。归档先将信封中的产物声明写入暂存清单，沿用同一迁移链，再按内容证据恢复声明。相关决策见 [ADR 0062](../adr/0062-artifact-lifecycle-and-currency.md)和 [ADR 0098](../adr/0098-backfill-keeps-existing-content-gaps-only-gate-generation.md)。
- 生成输入缺口仅拦截生成。产物依据仍基于可用输入构造，缺失部分标为 `absent`，输入完整时保留原摘要。更早的迁移步骤按目标版本对应的规则规划。
- 迁移报告的「已调整」收规整记录，「未能带入」收标注 `unreadable` 或 `legacy_audio_basis` 的跳过。原因记录通过 `ArtifactBackfillOutcome` 返回给 runner。算不出依据的跳过、待重新规划单位的相关媒体由报告逻辑过滤。报告未完成时在重试中继续补齐，完成后才对用户可见。实现与类型见[迁移报告](../../lib/project/project_migration_report.py)。
- 保持项目的最近活动时间：迁移改写项目和剧本内容时保留原有的 `metadata.updated_at`，新建剧本沿用原始事实中的时间，迁移路径不经过活动记账。
