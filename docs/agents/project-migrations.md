# Project Schema Migrations

新增或修改 `lib/project/project_migrations/` 里的一步迁移，或改动产物补录规划器（`lib/artifacts/artifact_planner.py`）时按此文档工作。迁移链的结构、备份命名与失败裁决的机制见 `docs/adr/0022`、`docs/adr/0062`、`docs/adr/0075`；本文档只放代码里查不到的约定。

## 步骤

1. **列出这一步要处理的全部旧形态。** 每种形态是「某个版本之前的代码写出的、与当前代码期望不同的数据」；下表是已知清单，新发现的形态先补进表里。完成判据：每种相关形态在 `tests/legacy_project_shapes.py` 里有一个构造出的样本。
2. **写迁移器，登记与跳过都要有出口。** 一个发行版至多新增一步迁移：`CURRENT_PROJECT_SCHEMA_VERSION` 比最近的 `v*` 发版 tag 上的值大，说明最新一步尚未发版，改动并进这一步；两者相等才新开一步。改写产物清单的迁移返回 `ArtifactBackfillOutcome`（`lib/project/project_migration_report.py`），runner 把它折进项目内的 `.migration_report.json`。规划器里每一条「不登记」的分支调用 `_skip(...)` 给出原因。完成判据：跳过的每一件产物都出现在迁移报告里。
3. **在旧形态样本上跑到用户口径。** 除迁移器本身的断言外，至少一条用例用 `WorkflowStateService.get_project_summary` / `get_status` 或演示读模型断言用户看到的结果（计数、状态、预览可用）。完成判据：`tests/integration/lib/project/project_migrations/` 下的用例覆盖了「≤ 上一版实际安装」到当前版本的整条链（`migrate_project_dir`），不只单步。
4. **同步面向用户的迁移说明。** `website/docs/ops/deployment.md` 的「项目结构迁移」一节描述备份、报告与重试行为；行为变了就改它。

## 已知旧形态

| 形态 | 出现版本 | 现在的期望 | 处理 |
| --- | --- | --- | --- |
| 视频版本记录只有 `version/file/prompt/created_at/duration_seconds`，`duration_seconds` 可能是字符串 | ≤ 0.26 | 类型化来源字段（`lib/artifacts/artifact_version_provenance.py`） | v12→v13 按当时项目状态投影补写，标 `provenance_backfilled_at` |
| 旁白音频版本记录没有 TTS 设置 | ≤ 0.26 | `tts_*` + `artifact_audio_basis` | 不补写，进迁移报告 |
| 脚本规划草稿是 `.md`（`step1_*.md` / `script_plan_*.md`），没有 JSON 正式计划 | ≤ 0.26 | `drafts/episode_N/script_plan_*.json` | 不改文件；剧本依据不以脚本规划为输入，照常登记 |
| 源文用上传原名，没有 `source/episode_N.txt` | ≤ 0.26 | `source/episode_N.txt` | 不改文件；只影响脚本规划自身的登记，剧本照常登记 |
| 剧本顶层没有 `episode` 字段 | 未见于真实项目 | 顶层 `episode == 绑定集号` | 激活预检拒绝并点名文件 |
| 风格值以「画风：」开头（当时的风格模版带该前缀） | 0.9 – 0.15 | 已剥前缀的风格值 | v13→v14 就地剥离 |
| 风格值是 `Photographic` / `Anime` / `3D Animation` 短标签 | ≤ 0.8 | `style_template_id` + 展开后的模版快照 | v13→v14 解析并展开；已有 `style_template_id` 时不动 |
| 宫格联合图、切格分镜与参考视频的依据（清单登记与选中版本记录冻结的依据）不含风格描述 | ≤ 0.30（schema ≤ 13） | 描述非空时依据含 `style_description` | schema < 14 的项目按不记描述的口径规划（`project_basis_style_description`），v13→v14 之前的激活与来源补写不受影响；v13→v14 只改写改写前时新的登记及其选中版本记录 |
| 集绑定 `script_file` 指向非规范文件名（SSE 索引同步登记了带 `episode` 整数的任意 `scripts/*.json`） | schema ≤ 14 | 逐字等于 `scripts/episode_N.json` | v14→v15 判据是字面相等、不做归一，裸名 `episode_N.json`、`./` 前缀与反斜杠写法一并拒绝：拒绝整个项目并点名集号与绑定，一个字节都不改；运维把剧本挪到规范路径、改绑后重跑 |
| 剧本条目带 `script_plan_entry_revision`，剧本 `metadata` 带 `script_plan_revision` | schema ≤ 14 | 无指纹字段 | v14→v15 删除 |
| 脚本规划已确认，绑定的正式脚本不在盘上 | schema ≤ 14 | 确认即转出正式脚本 | v14→v15 整份转出、全部待编写；转不出的进迁移报告 |
| 正式脚本分镜视觉层两侧皆空，没有待编写标记 | schema ≤ 14 | `pending_authoring: true` | v14→v15 盖标记（参考生视频单元不按此判） |
| drama 正式分镜缺 `scene_description` | schema ≤ 14 | 从脚本规划透传 | v14→v15 按分镜 id 从脚本规划回填 |
| 参考生视频正式单元缺 `source_text` | schema ≤ 14 | 从脚本规划投影 | v14→v15 按单元 id 补录，取不到留空 |
| 有正式脚本与脚本规划，没有 `script_plan_review` | schema ≤ 14 | 确认记录 | v14→v15 把当前规划指纹记为确认基线；账本 stale 的集不记 |
| 剧本清单依据是 v2（以脚本规划内容为输入）或无计划依据 | schema 8 – 14 | v3（不以脚本规划为输入） | v14→v15 只改写改写前时新的登记，本就过期的保留 |
| `project.json` 没有 `aspect_ratio` 字段（新建项目一律写入） | 很早期版本或导入项目 | 分镜图画布按项目比例规则取缺省值：解说与广告 9:16、剧情 16:9 | 不补字段，不改写登记；剧情项目此前按 9:16 登记的分镜图读为过期 |
| 分镜图已生成，但引用未登记、角色/场景/道具资产图不可登记、已声明的商品资产图不可用，或商品原图已声明但读不到；旧执行器遇到这类缺口静默丢图照常出图 | 任意 | 分镜图的生成输入成立才登记；商品未声明资产图时可只用原图，也未声明原图时可只用文字 | 不登记该分镜图，进迁移报告，读时报 missing；上一分镜图因此未登记时，下一张只是不带它 |
| 剧本条目带 `transition_to_next`（`cut` / `fade` / `dissolve`） | schema ≤ 15 | 无转场字段；转场属于剪辑时间线 | v15→v16 删除，非硬切值直接丢弃；v6→v7 与 v8→v9 按当前模型自检单元时先剔除它 |
| 呈现模型文件带 `transition_to_next`，依据是 v2（输入含转场），清单登记旧依据 | schema ≤ 15 | 文件无转场，依据 v3 | schema < 16 的激活按旧依据核对与登记（`build_legacy_transition_presentation_basis`）；v15→v16 改写文件，登记恰为文件旧依据的改写为 v3，其余登记保留 |
| TTS 下限使视频申请档位高于编排时长，清单与选中版本仍按较高档位记时效依据 | schema ≤ 15 | 视频时效只以编排时长为时长输入，实际付费档位仍保留 | schema < 16 的规划沿用已登记且时新的选中配音下限；v15→v16 改前改后各规划一次，只改写改前时新且目标变了的视频登记。选中版本补记 `artifact_video_currency_duration_seconds`，付费事实与请求摘要原样保留；本就过期的登记与版本记录不动 |
| `project.json` 没有 `narration_delivery`；`audio_backend` 可以是裸供应商，音色与语速留空跟随全局默认 | schema ≤ 15 | 必填的旁白交付方式；TTS 配音项目带完整快照（`provider/model` + 音色 + 可选语速），不再继承全局默认 | v15→v16 按清单登记的旁白配音判定：选中版本记录带完整 TTS 设置的判为 TTS 配音，写入 `created_at` 最新一条的设置；其余判为后期配音，旧字段原样保留 |
| 上传的视频不登记清单，只凭选中的 `manual_upload` 版本记录认领；上传分镜图时提示词为空或引用资产缺资产图，分镜图登记被删去，其余上传分镜图按生成输入登记 | schema ≤ 15 | 选中版本是字节一致的手动上传时，依据只由上传字节决定，上传即 current | v15→v16 按改后目标态补登或改写这些登记；选中的已是生成版本、或文件与上传快照不一致的不动 |
| 新集号取「账本最大号 + 1」，重置与重新规划会复用被清出账本的号；清出的集在磁盘（草稿目录、源文留底、`E{N}` 前缀的媒体名、媒体已删去后的版本历史与宫格记录）、产物清单与任务和调用记录（含生成输入引用的路径）里留下更大的号 | schema ≤ 15 | `project.json` 带 `episode_id_high_water`，新集 ID 只从高水位之后分配，不复用 | v15→v16 取账本、磁盘残留、清单与 runner 注入的数据库记录中的最大集 ID 写入高水位；账本顺序不重排 |
| 脚本规划依据里的下集大纲取「集号 + 1」那一集，下一集没有规划数据时省略 | schema ≤ 15 | 取播出顺序（账本排列）中紧接的那一集，没有规划数据时给标题 | v15→v16 改前改后各规划一次，只改写改前时新且目标变了的脚本规划登记；脚本规划没有版本记录，只改清单 |
| 资产图已生成，但角色或商品声明的原图读不到，或描述为空；衍生资产图已生成，但本体资产图不能登记。旧规划器对前两者静默不登记、不进报告，对衍生只看本体资产图文件在不在 | 任意 | 资产图与衍生资产图的生成输入成立才登记；衍生经本体资产图的清单登记判定可用，规划顺序本体先于衍生 | 不登记，进迁移报告，读时报 missing |

## 约定

- 升级路径常常跨多个版本：迁移要处理的是更早版本留下的全部变体，不只上一版写出的标准形态。
- 备份走 `lib/project/project_migrations/backups.py`；自行备份输入的迁移器登记到 runner 的 `_MIGRATORS_WITH_OWNED_BACKUP`。
- 改写清单的迁移用 `activate_artifact_target_state(bump_schema=True, target_schema_version=...)`，先做只读预检再落盘。
- 整份激活会把在场产物一律登记为时新。改的只是某个依据输入、既有产物本不该因此过期时，改前改后各规划一次目标态，只把「改前正是时新、且目标登记变了」的条目改写过去（先例：v13→v14 的风格值归一），本就过期的条目不被伪造成时新。
- 「投影不出来」不等于「不存在」，也不等于「整项目失败」：依据从当前项目状态投影不出的目标不登记，进报告，读时报 missing。
