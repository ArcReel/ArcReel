# Project Schema Migrations

新增或修改 `lib/project_migrations/` 里的一步迁移，或改动产物补录规划器（`lib/artifact_planner.py`）时按此文档工作。迁移链的结构、备份命名与失败裁决的机制见 `docs/adr/0022`、`docs/adr/0062`、`docs/adr/0075`；本文档只放代码里查不到的约定。

## 步骤

1. **列出这一步要处理的全部旧形态。** 每种形态是「某个版本之前的代码写出的、与当前代码期望不同的数据」；下表是已知清单，新发现的形态先补进表里。完成判据：每种相关形态在 `tests/legacy_project_shapes.py` 里有一个构造出的样本。
2. **写迁移器，登记与跳过都要有出口。** 改写产物清单的迁移返回 `ArtifactBackfillOutcome`（`lib/project_migration_report.py`），runner 把它折进项目内的 `.migration_report.json`。规划器里任何「不登记」的分支调用 `_skip(...)` 给出原因；静默 `return` 会让产物凭空消失而迁移报成功。完成判据：跳过的每一件产物都出现在迁移报告里。
3. **在旧形态样本上跑到用户口径。** 除迁移器本身的断言外，至少一条用例用 `WorkflowStateService.get_project_summary` / `get_status` 或演示读模型断言用户看到的结果（计数、状态、预览可用）。完成判据：`tests/integration/lib/project_migrations/` 下的用例覆盖了「≤ 上一版实际安装」到当前版本的整条链（`migrate_project_dir`），不只单步。
4. **同步面向用户的迁移说明。** `website/docs/ops/deployment.md` 的「项目结构迁移」一节描述备份、报告与重试行为；行为变了就改它。

## 已知旧形态

| 形态 | 出现版本 | 现在的期望 | 处理 |
| --- | --- | --- | --- |
| 视频版本记录只有 `version/file/prompt/created_at/duration_seconds`，`duration_seconds` 可能是字符串 | ≤ 0.26 | 类型化来源字段（`lib/artifact_version_provenance.py`） | v12→v13 按当时项目状态投影补写，标 `provenance_backfilled_at` |
| 旁白音频版本记录没有 TTS 设置 | ≤ 0.26 | `tts_*` + `artifact_audio_basis` | 不补写，进迁移报告 |
| 脚本规划草稿是 `.md`（`step1_*.md` / `script_plan_*.md`），没有 JSON 正式计划 | ≤ 0.26 | `drafts/episode_N/script_plan_*.json` | 剧本按无计划依据登记（`build_planless_episode_script_basis`） |
| 源文用上传原名，没有 `source/episode_N.txt` | ≤ 0.26 | `source/episode_N.txt` | 不改文件；剧本走无计划登记 |
| 剧本顶层没有 `episode` 字段 | 未见于真实项目 | 顶层 `episode == 绑定集号` | 激活预检拒绝并点名文件 |

## 约定

- 迁移只认「上一版写出的标准形态」是不够的：升级路径常常跨多个版本，更早版本留下的变体才是老用户手里的真实数据。写迁移前先在本机旧项目上跑一遍。
- 备份用 `lib/project_migrations/backups.py` 的 `ensure_versioned_backup`：内容相同只留一份，名字是 `<文件>.bak.v<起点版本>-<时间戳>`，runner 的清理只认这个形状。自行备份输入的迁移器要登记到 runner 的 `_MIGRATORS_WITH_OWNED_BACKUP`。
- 改写清单的迁移用 `activate_artifact_target_state(bump_schema=True, target_schema_version=...)`，先做只读预检再落盘；预检拒绝时项目目录一个字节不动。
- 「投影不出来」不等于「不存在」，也不等于「整项目失败」：依据从当前项目状态投影不出的目标不登记，进报告，读时报 missing。
