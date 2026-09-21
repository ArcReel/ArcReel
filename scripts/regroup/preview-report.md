# 目录归组预演报告

> 本文件由 `uv run python scripts/regroup/preview.py --write` 生成，勿手工编辑。
> 输入：`scripts/regroup/module_map.toml` 与当前 `lib/`、`server/` 源码的静态导入图。
> 各项裁定的理由见 `scripts/regroup/module_map.toml` 的 `[rulings]`、`[accepted_edges]` 与 `[accepted_server_edges]`。

## 结论

- 映射条目 173 条；覆盖问题 0 个
- 有包间环的强连通分量 7 个，分布在 6 个父包
- 断环需豁免的包间依赖 38 条（模块级 import 131 条，其中 34 条本身就在现有的模块级环上）；未登记 0 条；登记了但不存在 0 条
- 既有 import-linter 契约改写路径后判定变化 0 条
- 路线中立违规 0 处；核心库 → 服务端依赖 2 条，未登记 0 条；登记了但不存在 0 条
- 镜像测试移动 222 项；无法推导去向 0 个
- 预演判定：通过

## 新布局

### `lib.agent`（Agent 运行时）

- `lib.agent_memory_index` → `lib.agent.agent_memory_index`
- `lib.agent_memory_paths` → `lib.agent.agent_memory_paths`
- `lib.agent_memory_store` → `lib.agent.agent_memory_store`
- `lib.agent_profile` → `lib.agent.agent_profile`
- `lib.agent_provider_catalog` → `lib.agent.agent_provider_catalog`
- `lib.agent_session_store` → `lib.agent.agent_session_store`
- `lib.profile_frontmatter` → `lib.agent.profile_frontmatter`
- `lib.profile_manifest` → `lib.agent.profile_manifest`

### `lib.artifacts`（产物与制作状态）

- `lib.artifact_activation` → `lib.artifacts.artifact_activation`
- `lib.artifact_currency` → `lib.artifacts.artifact_currency`
- `lib.artifact_input_claims` → `lib.artifacts.artifact_input_claims`
- `lib.artifact_manifest` → `lib.artifacts.artifact_manifest`
- `lib.artifact_planner` → `lib.artifacts.artifact_planner`
- `lib.artifact_provenance` → `lib.artifacts.artifact_provenance`
- `lib.artifact_registration` → `lib.artifacts.artifact_registration`
- `lib.artifact_version_provenance` → `lib.artifacts.artifact_version_provenance`
- `lib.formal_write` → `lib.artifacts.formal_write`
- `server.services.image_artifact_currency` → `lib.artifacts.image_artifact_currency`
- `lib.image_reference_snapshot` → `lib.artifacts.image_reference_snapshot`
- `lib.media_artifact_currency` → `lib.artifacts.media_artifact_currency`
- `lib.version_manager` → `lib.artifacts.version_manager`
- `lib.video_artifact_commit` → `lib.artifacts.video_artifact_commit`
- `lib.video_artifact_facts` → `lib.artifacts.video_artifact_facts`
- `lib.video_visual_provenance` → `lib.artifacts.video_visual_provenance`
- `lib.visual_artifact_provenance` → `lib.artifacts.visual_artifact_provenance`

### `lib.backends`（供应商与模型）

- `lib.agnes_shared` → `lib.backends.agnes_shared`
- `lib.ark_shared` → `lib.backends.ark_shared`
- `lib.aspect_size` → `lib.backends.aspect_size`
- `lib.audio_backends` → `lib.backends.audio_backends`
- `lib.backend_assembly` → `lib.backends.backend_assembly`
- `lib.dashscope_shared` → `lib.backends.dashscope_shared`
- `lib.data_uri` → `lib.backends.data_uri`
- `lib.gemini_shared` → `lib.backends.gemini_shared`
- `lib.generation_type_buckets` → `lib.backends.generation_type_buckets`
- `lib.grok_shared` → `lib.backends.grok_shared`
- `lib.http_status_errors` → `lib.backends.http_status_errors`
- `lib.image_backends` → `lib.backends.image_backends`
- `lib.kling_backend_base` → `lib.backends.kling_backend_base`
- `lib.kling_shared` → `lib.backends.kling_shared`
- `lib.minimax_shared` → `lib.backends.minimax_shared`
- `lib.openai_shared` → `lib.backends.openai_shared`
- `lib.providers` → `lib.backends.providers`
- `lib.text_backends` → `lib.backends.text_backends`
- `lib.text_generator` → `lib.backends.text_generator`
- `lib.video_backends` → `lib.backends.video_backends`
- `lib.video_frame_slots` → `lib.backends.video_frame_slots`
- `lib.vidu_shared` → `lib.backends.vidu_shared`

### `lib.billing`（计费）

- `lib.call_failure` → `lib.billing.call_failure`
- `lib.cost_calculator` → `lib.billing.cost_calculator`
- `lib.ledger` → `lib.billing.ledger`
- `lib.pricing` → `lib.billing.pricing`
- `lib.usage_summary` → `lib.billing.usage_summary`

### `lib.config`

- `lib.system_config` → `lib.config.system_config`

### `lib.episode`（脚本与分镜）

- `lib.episode_ledger` → `lib.episode.episode_ledger`
- `lib.episode_paths` → `lib.episode.episode_paths`
- `lib.episode_planner` → `lib.episode.episode_planner`
- `lib.episode_reset` → `lib.episode.episode_reset`
- `lib.episode_target_duration` → `lib.episode.episode_target_duration`
- `lib.episode_target_volume` → `lib.episode.episode_target_volume`

### `lib.generation`（任务与取消）

- `lib.batch_admission` → `lib.generation.batch_admission`
- `lib.generation_admission` → `lib.generation.generation_admission`
- `lib.generation_batch` → `lib.generation.generation_batch`
- `lib.generation_queue` → `lib.generation.generation_queue`
- `lib.generation_queue_client` → `lib.generation.generation_queue_client`
- `lib.generation_result` → `lib.generation.generation_result`
- `lib.generation_worker` → `lib.generation.generation_worker`
- `lib.media_generator` → `lib.generation.media_generator`
- `lib.task_failure` → `lib.generation.task_failure`
- `lib.task_terminal_events` → `lib.generation.task_terminal_events`

### `lib.infra`（——（纯技术工具））

- `lib.api_errors` → `lib.infra.api_errors`
- `lib.app_data_dir` → `lib.infra.app_data_dir`
- `lib.async_thread` → `lib.infra.async_thread`
- `lib.content_digest` → `lib.infra.content_digest`
- `lib.env_init` → `lib.infra.env_init`
- `lib.httpx_shared` → `lib.infra.httpx_shared`
- `lib.image_utils` → `lib.infra.image_utils`
- `lib.json_io` → `lib.infra.json_io`
- `lib.logging_config` → `lib.infra.logging_config`
- `lib.logging_utils` → `lib.infra.logging_utils`
- `lib.path_safety` → `lib.infra.path_safety`
- `lib.retry` → `lib.infra.retry`
- `lib.schema_guards` → `lib.infra.schema_guards`
- `lib.text_metrics` → `lib.infra.text_metrics`
- `lib.text_utils` → `lib.infra.text_utils`
- `lib.thumbnail` → `lib.infra.thumbnail`
- `lib.validation_messages` → `lib.infra.validation_messages`

### `lib.project`（项目与资产）

- `lib.asset_derivative_cleanup` → `lib.project.asset_derivative_cleanup`
- `lib.asset_derivative_rename` → `lib.project.asset_derivative_rename`
- `lib.asset_derivatives` → `lib.project.asset_derivatives`
- `lib.asset_fingerprints` → `lib.project.asset_fingerprints`
- `lib.asset_inventory` → `lib.project.asset_inventory`
- `lib.asset_rename` → `lib.project.asset_rename`
- `lib.asset_types` → `lib.project.asset_types`
- `lib.data_validator` → `lib.project.data_validator`
- `lib.legacy_media_provenance` → `lib.project.legacy_media_provenance`
- `lib.project_change_hints` → `lib.project.project_change_hints`
- `lib.project_manager` → `lib.project.project_manager`
- `lib.project_migration_failure` → `lib.project.project_migration_failure`
- `lib.project_migration_guard` → `lib.project.project_migration_guard`
- `lib.project_migration_report` → `lib.project.project_migration_report`
- `lib.project_migrations` → `lib.project.project_migrations`
- `lib.project_schema` → `lib.project.project_schema`
- `lib.resource_paths` → `lib.project.resource_paths`
- `lib.source_revision` → `lib.project.source_revision`

### `lib.prompts`（脚本与分镜）

- `lib.prompt_builders` → `lib.prompts.prompt_builders`
- `lib.prompt_builders_ad` → `lib.prompts.prompt_builders_ad`
- `lib.prompt_builders_reference` → `lib.prompts.prompt_builders_reference`
- `lib.prompt_builders_script` → `lib.prompts.prompt_builders_script`
- `lib.prompt_rules` → `lib.prompts.prompt_rules`
- `lib.prompt_style` → `lib.prompts.prompt_style`
- `lib.prompt_templates` → `lib.prompts.prompt_templates`
- `lib.prompt_utils` → `lib.prompts.prompt_utils`
- `lib.reference_image_numbering` → `lib.prompts.reference_image_numbering`
- `lib.style_templates` → `lib.prompts.style_templates`

### `lib.references`（参考图与压缩）

- `lib.reference_admission` → `lib.references.reference_admission`
- `lib.reference_catalog` → `lib.references.reference_catalog`
- `lib.reference_compression` → `lib.references.reference_compression`

### `lib.script`（脚本与分镜）

- `lib.draft_quarantine` → `lib.script.draft_quarantine`
- `lib.draft_violation` → `lib.script.draft_violation`
- `lib.grid` → `lib.script.grid`
- `lib.reference_video` → `lib.script.reference_video`
- `lib.script_batch_edit` → `lib.script.script_batch_edit`
- `lib.script_document` → `lib.script.script_document`
- `lib.script_editor` → `lib.script.script_editor`
- `lib.script_generator` → `lib.script.script_generator`
- `lib.script_models` → `lib.script.script_models`
- `lib.script_plan_entries` → `lib.script.script_plan_entries`
- `lib.script_references` → `lib.script.script_references`
- `lib.script_review` → `lib.script.script_review`
- `lib.script_skeleton` → `lib.script.script_skeleton`
- `lib.script_structure_validator` → `lib.script.script_structure_validator`
- `lib.source_loader` → `lib.script.source_loader`
- `lib.storyboard_mentions` → `lib.script.storyboard_mentions`
- `lib.storyboard_sequence` → `lib.script.storyboard_sequence`

### `lib.script.grid`

- `server.services.grid_access` → `lib.script.grid.grid_access`
- `lib.grid_manager` → `lib.script.grid.grid_manager`
- `server.services.grid_resolution` → `lib.script.grid.grid_resolution`

### `lib.speech`（媒体类型与配音）

- `lib.audio_utils` → `lib.speech.audio_utils`
- `lib.character_voice` → `lib.speech.character_voice`
- `lib.narration_delivery` → `lib.speech.narration_delivery`
- `lib.speech_artifact_provenance` → `lib.speech.speech_artifact_provenance`
- `lib.speech_composition` → `lib.speech.speech_composition`
- `lib.speech_presentation` → `lib.speech.speech_presentation`
- `lib.speech_rate` → `lib.speech.speech_rate`

### `lib.workflow`（产物与制作状态）

- `lib.workflow_plan` → `lib.workflow.workflow_plan`
- `lib.workflow_rules` → `lib.workflow.workflow_rules`
- `lib.workflow_state` → `lib.workflow.workflow_state`

### `server.services.admission`（准入与预估族）

- `server.services.cost_estimation` → `server.services.admission.cost_estimation`
- `server.services.prompt_preview` → `server.services.admission.prompt_preview`
- `server.services.reference_admission` → `server.services.admission.reference_admission`
- `server.services.video_batch_admission` → `server.services.admission.video_batch_admission`

### `server.services.currency`（产物时效族）

- `server.services.artifact_version_restore` → `server.services.currency.artifact_version_restore`
- `server.services.upload_finalize` → `server.services.currency.upload_finalize`
- `server.services.video_artifact_currency` → `server.services.currency.video_artifact_currency`

### `server.services.grid`（宫格族）

- `server.services.grid_split` → `server.services.grid.grid_split`

### `server.services.presentation`（呈现族）

- `server.services.jianying_draft_service` → `server.services.presentation.jianying_draft_service`
- `server.services.presentation_bundle` → `server.services.presentation.presentation_bundle`
- `server.services.presentation_read_model` → `server.services.presentation.presentation_read_model`

### `server.services.project`（项目族）

- `server.services.end_frame` → `server.services.project.end_frame`
- `server.services.project_archive` → `server.services.project.project_archive`
- `server.services.project_cover` → `server.services.project.project_cover`
- `server.services.project_events` → `server.services.project.project_events`
- `server.services.script_review` → `server.services.project.script_review`
- `server.services.workflow_planner` → `server.services.project.workflow_planner`

### `server.services.system`（系统诊断（六族之外，见 rulings））

- `server.services.diagnostics` → `server.services.system.diagnostics`

### `server.services.tasks`（任务执行族）

- `server.services.derivative_sheet_tasks` → `server.services.tasks.derivative_sheet_tasks`
- `server.services.generation_context` → `server.services.tasks.generation_context`
- `server.services.generation_tasks` → `server.services.tasks.generation_tasks`
- `server.services.image_edit_tasks` → `server.services.tasks.image_edit_tasks`
- `server.services.narration_delivery_tasks` → `server.services.tasks.narration_delivery_tasks`
- `server.services.reference_video_tasks` → `server.services.tasks.reference_video_tasks`
- `server.services.resume_executor` → `server.services.tasks.resume_executor`
- `server.services.video_caps` → `server.services.tasks.video_caps`

## 包间环

口径同 import-linter `acyclic_siblings`：在 `lib`、`server.services` 与每个新建包内，把直接子项（子包或
模块）之间的模块级 import 聚合成包间依赖，求强连通分量。每个分量给出一个自底向上的顺序，使「下层
import 上层」的逆向依赖所含的模块级 import 总数最小；逆向依赖就是断环所需的豁免。模块级 import 标
「既有环」的，两端在当前代码里已经处于同一个模块级环上——这一段环与目录无关，任何归属都消除不了。

### `lib` 内的环（14 个子项）

自底向上：`db` < `references` < `speech` < `agent` < `prompts` < `artifacts` < `config` < `backends` < `episode` < `script` < `project` < `billing` < `custom_provider` < `generation`

| 逆向依赖 | 模块级 import |
| --- | --- |
| `lib.artifacts` → `lib.episode` | `lib.artifacts.artifact_planner -> lib.episode.episode_paths`<br>`lib.artifacts.artifact_provenance -> lib.episode.episode_ledger`<br>`lib.artifacts.artifact_provenance -> lib.episode.episode_target_duration` |
| `lib.artifacts` → `lib.project` | `lib.artifacts.artifact_activation -> lib.project.project_schema`<br>`lib.artifacts.artifact_currency -> lib.project.project_manager`（既有环）<br>`lib.artifacts.artifact_currency -> lib.project.project_migration_failure`<br>`lib.artifacts.artifact_currency -> lib.project.project_schema`<br>`lib.artifacts.artifact_input_claims -> lib.project.project_manager`（既有环）<br>`lib.artifacts.artifact_manifest -> lib.project.asset_types`<br>`lib.artifacts.artifact_planner -> lib.project.asset_derivatives`（既有环）<br>`lib.artifacts.artifact_planner -> lib.project.asset_types`<br>`lib.artifacts.artifact_planner -> lib.project.project_migration_failure`<br>`lib.artifacts.artifact_planner -> lib.project.project_migration_report`<br>`lib.artifacts.artifact_planner -> lib.project.project_schema`<br>`lib.artifacts.artifact_planner -> lib.project.resource_paths`<br>`lib.artifacts.artifact_registration -> lib.project.asset_derivatives`（既有环）<br>`lib.artifacts.artifact_registration -> lib.project.asset_types`<br>`lib.artifacts.artifact_registration -> lib.project.project_migration_failure`<br>`lib.artifacts.artifact_registration -> lib.project.project_schema`<br>`lib.artifacts.artifact_registration -> lib.project.resource_paths`<br>`lib.artifacts.artifact_version_provenance -> lib.project.asset_derivatives`（既有环）<br>`lib.artifacts.artifact_version_provenance -> lib.project.asset_types`<br>`lib.artifacts.artifact_version_provenance -> lib.project.resource_paths`<br>`lib.artifacts.media_artifact_currency -> lib.project.asset_types`<br>`lib.artifacts.media_artifact_currency -> lib.project.project_manager`（既有环）<br>`lib.artifacts.media_artifact_currency -> lib.project.resource_paths`<br>`lib.artifacts.version_manager -> lib.project.asset_rename`<br>`lib.artifacts.version_manager -> lib.project.asset_types`<br>`lib.artifacts.version_manager -> lib.project.resource_paths`<br>`lib.artifacts.video_artifact_facts -> lib.project.asset_types`<br>`lib.artifacts.visual_artifact_provenance -> lib.project.asset_types`<br>`lib.artifacts.visual_artifact_provenance -> lib.project.project_schema` |
| `lib.artifacts` → `lib.script` | `lib.artifacts.artifact_currency -> lib.script.reference_video.duration_migration`<br>`lib.artifacts.artifact_input_claims -> lib.script.storyboard_sequence`<br>`lib.artifacts.artifact_planner -> lib.script.grid.layout`<br>`lib.artifacts.artifact_planner -> lib.script.grid.models`<br>`lib.artifacts.artifact_planner -> lib.script.script_editor`<br>`lib.artifacts.artifact_planner -> lib.script.script_review`（既有环）<br>`lib.artifacts.artifact_planner -> lib.script.storyboard_sequence`<br>`lib.artifacts.media_artifact_currency -> lib.script.reference_video.duration_slots`<br>`lib.artifacts.media_artifact_currency -> lib.script.reference_video.prompt_render`<br>`lib.artifacts.media_artifact_currency -> lib.script.reference_video.request_projection`（既有环）<br>`lib.artifacts.media_artifact_currency -> lib.script.script_editor`<br>`lib.artifacts.media_artifact_currency -> lib.script.storyboard_sequence`<br>`lib.artifacts.visual_artifact_provenance -> lib.script.grid.prompt_builder`<br>`lib.artifacts.visual_artifact_provenance -> lib.script.reference_video.request_projection`（既有环）<br>`lib.artifacts.visual_artifact_provenance -> lib.script.reference_video.text_parser` |
| `lib.backends` → `lib.billing` | `lib.backends.gemini_shared -> lib.billing.cost_calculator`（既有环）<br>`lib.backends.text_generator -> lib.billing.ledger`（既有环）<br>`lib.backends.video_backends.gemini -> lib.billing.cost_calculator` |
| `lib.backends` → `lib.custom_provider` | `lib.backends.backend_assembly.assembler -> lib.custom_provider`<br>`lib.backends.backend_assembly.assembler -> lib.custom_provider.loader`（既有环）<br>`lib.backends.backend_assembly.specs -> lib.custom_provider.builtin_definitions`（既有环）<br>`lib.backends.backend_assembly.specs -> lib.custom_provider.declarative_backend`（既有环）<br>`lib.backends.backend_assembly.specs -> lib.custom_provider.endpoints`（既有环）<br>`lib.backends.generation_type_buckets -> lib.custom_provider.capabilities`<br>`lib.backends.generation_type_buckets -> lib.custom_provider.endpoints` |
| `lib.backends` → `lib.generation` | `lib.backends.video_backends.base -> lib.generation.generation_queue`（既有环） |
| `lib.billing` → `lib.custom_provider` | `lib.billing.cost_calculator -> lib.custom_provider` |
| `lib.config` → `lib.backends` | `lib.config.registry -> lib.backends.agnes_shared`<br>`lib.config.registry -> lib.backends.ark_shared`<br>`lib.config.registry -> lib.backends.dashscope_shared`（既有环）<br>`lib.config.registry -> lib.backends.minimax_shared`<br>`lib.config.resolver -> lib.backends.backend_assembly.specs`（既有环）<br>`lib.config.resolver -> lib.backends.text_backends.base` |
| `lib.config` → `lib.billing` | `lib.config.registry -> lib.billing.pricing.types` |
| `lib.config` → `lib.custom_provider` | `lib.config.resolver -> lib.custom_provider`<br>`lib.config.resolver -> lib.custom_provider.capabilities`（既有环）<br>`lib.config.resolver -> lib.custom_provider.endpoint_resolution`（既有环） |
| `lib.config` → `lib.episode` | `lib.config.resolver -> lib.episode.episode_target_duration` |
| `lib.config` → `lib.project` | `lib.config.resolver -> lib.project.project_manager`（既有环） |
| `lib.custom_provider` → `lib.generation` | `lib.custom_provider.endpoint_test.trial_run -> lib.generation.task_failure` |
| `lib.db` → `lib.backends` | `lib.db.repositories.task_repo -> lib.backends.providers`<br>`lib.db.repositories.usage_repo -> lib.backends.providers` |
| `lib.db` → `lib.billing` | `lib.db.repositories.usage_repo -> lib.billing.call_failure`<br>`lib.db.repositories.usage_repo -> lib.billing.cost_calculator`（既有环）<br>`lib.db.repositories.usage_repo -> lib.billing.pricing.strategies`<br>`lib.db.repositories.usage_repo -> lib.billing.usage_summary` |
| `lib.db` → `lib.config` | `lib.db.repositories.credential_repository -> lib.config.url_utils`<br>`lib.db.repositories.usage_repo -> lib.config.registry`（既有环） |
| `lib.db` → `lib.custom_provider` | `lib.db.models.custom_provider -> lib.custom_provider`<br>`lib.db.repositories.custom_provider_repo -> lib.custom_provider`<br>`lib.db.repositories.custom_provider_repo -> lib.custom_provider.endpoints`（既有环）<br>`lib.db.repositories.usage_repo -> lib.custom_provider` |
| `lib.db` → `lib.generation` | `lib.db.repositories.task_repo -> lib.generation.task_failure`<br>`lib.db.repositories.task_repo -> lib.generation.task_terminal_events`<br>`lib.db.repositories.usage_repo -> lib.generation.task_terminal_events` |
| `lib.episode` → `lib.project` | `lib.episode.episode_planner -> lib.project.project_manager`<br>`lib.episode.episode_reset -> lib.project.project_manager` |
| `lib.episode` → `lib.script` | `lib.episode.episode_planner -> lib.script.script_review` |
| `lib.prompts` → `lib.project` | `lib.prompts.prompt_rules.asset_appearance -> lib.project.asset_types`<br>`lib.prompts.prompt_utils -> lib.project.asset_types`<br>`lib.prompts.reference_image_numbering -> lib.project.asset_types` |
| `lib.prompts` → `lib.script` | `lib.prompts.prompt_builders_ad -> lib.script.script_models`<br>`lib.prompts.prompt_utils -> lib.script.script_models`<br>`lib.prompts.reference_image_numbering -> lib.script.reference_video.text_parser` |
| `lib.references` → `lib.project` | `lib.references.reference_admission -> lib.project.asset_types`<br>`lib.references.reference_catalog -> lib.project.asset_types` |
| `lib.script` → `lib.project` | `lib.script.grid.grid_access -> lib.project.project_manager`<br>`lib.script.reference_video.prompt_render -> lib.project.asset_types`<br>`lib.script.reference_video.request_projection -> lib.project.asset_types`<br>`lib.script.reference_video.script_preview -> lib.project.asset_types`<br>`lib.script.reference_video.text_parser -> lib.project.asset_types`<br>`lib.script.script_batch_edit -> lib.project.data_validator`<br>`lib.script.script_batch_edit -> lib.project.project_manager`<br>`lib.script.script_batch_edit -> lib.project.project_migration_failure`<br>`lib.script.script_generator -> lib.project.project_manager`<br>`lib.script.script_references -> lib.project.asset_types`<br>`lib.script.script_review -> lib.project.project_manager`（既有环）<br>`lib.script.storyboard_mentions -> lib.project.asset_types`<br>`lib.script.storyboard_sequence -> lib.project.resource_paths` |
| `lib.speech` → `lib.artifacts` | `lib.speech.narration_delivery -> lib.artifacts.artifact_manifest`<br>`lib.speech.speech_artifact_provenance -> lib.artifacts.artifact_manifest`<br>`lib.speech.speech_presentation -> lib.artifacts.artifact_manifest` |
| `lib.speech` → `lib.project` | `lib.speech.narration_delivery -> lib.project.resource_paths`<br>`lib.speech.speech_artifact_provenance -> lib.project.asset_types` |
| `lib.speech` → `lib.script` | `lib.speech.narration_delivery -> lib.script.reference_video.duration_slots`<br>`lib.speech.speech_composition -> lib.script.reference_video.text_parser` |

### `lib.backends` 内的环（2 个子项）

自底向上：`backend_assembly` < `text_backends`

| 逆向依赖 | 模块级 import |
| --- | --- |
| `lib.backends.backend_assembly` → `lib.backends.text_backends` | `lib.backends.backend_assembly.specs -> lib.backends.text_backends.registry` |

### `lib.backends` 内的环（3 个子项）

自底向上：`kling_backend_base` < `video_backends` < `image_backends`

| 逆向依赖 | 模块级 import |
| --- | --- |
| `lib.backends.kling_backend_base` → `lib.backends.video_backends` | `lib.backends.kling_backend_base -> lib.backends.video_backends.base`（既有环） |
| `lib.backends.video_backends` → `lib.backends.image_backends` | `lib.backends.video_backends.ark -> lib.backends.image_backends.base`（既有环） |

### `lib.generation` 内的环（4 个子项）

自底向上：`generation_batch` < `generation_queue` < `generation_queue_client` < `generation_result`

| 逆向依赖 | 模块级 import |
| --- | --- |
| `lib.generation.generation_batch` → `lib.generation.generation_result` | `lib.generation.generation_batch -> lib.generation.generation_result`（既有环） |

### `lib.project` 内的环（4 个子项）

自底向上：`data_validator` < `project_manager` < `legacy_media_provenance` < `project_migrations`

| 逆向依赖 | 模块级 import |
| --- | --- |
| `lib.project.data_validator` → `lib.project.project_manager` | `lib.project.data_validator -> lib.project.project_manager`（既有环） |
| `lib.project.project_manager` → `lib.project.project_migrations` | `lib.project.project_manager -> lib.project.project_migrations`（既有环） |

### `server.services` 内的环（3 个子项）

自底向上：`currency` < `grid` < `tasks`

| 逆向依赖 | 模块级 import |
| --- | --- |
| `server.services.currency` → `server.services.tasks` | `server.services.currency.artifact_version_restore -> server.services.tasks.reference_video_tasks`<br>`server.services.currency.upload_finalize -> server.services.tasks.generation_tasks`<br>`server.services.currency.video_artifact_currency -> server.services.tasks.narration_delivery_tasks` |
| `server.services.grid` → `server.services.tasks` | `server.services.grid.grid_split -> server.services.tasks.generation_tasks`（既有环） |

### `server.services.tasks` 内的环（4 个子项）

自底向上：`derivative_sheet_tasks` < `generation_tasks` < `image_edit_tasks` < `reference_video_tasks`

| 逆向依赖 | 模块级 import |
| --- | --- |
| `server.services.tasks.derivative_sheet_tasks` → `server.services.tasks.generation_tasks` | `server.services.tasks.derivative_sheet_tasks -> server.services.tasks.generation_tasks`（既有环） |
| `server.services.tasks.generation_tasks` → `server.services.tasks.image_edit_tasks` | `server.services.tasks.generation_tasks -> server.services.tasks.image_edit_tasks`（既有环） |
| `server.services.tasks.generation_tasks` → `server.services.tasks.reference_video_tasks` | `server.services.tasks.generation_tasks -> server.services.tasks.reference_video_tasks`（既有环） |

## 既有契约改写

按映射表改写 `pyproject.toml` 中 import-linter 契约的模块路径，在新导入图上按 import 链重新判定，结果须
与旧图一致；存量豁免条数按构造不变。

| 契约 | 存量豁免 | 旧图违规 | 新图违规 |
| --- | --- | --- | --- |
| lib.config / lib.*_backends / lib.custom_provider / lib.market 分层契约 | 22 | 0 | 0 |
| ComfyUI 端点实现不依赖声明式运行时 | 0 | 0 | 0 |
| ComfyUI 顶层实现模块不直接依赖声明式运行时 | 0 | 0 | 0 |
| 路线中立层不依赖参考生视频子包 | 0 | 0 | 0 |

- lib.config / lib.*_backends / lib.custom_provider / lib.market 分层契约：`lib.audio_backends` → `lib.backends.audio_backends`；`lib.audio_backends.registry` → `lib.backends.audio_backends.registry`；`lib.backend_assembly.assembler` → `lib.backends.backend_assembly.assembler`；`lib.backend_assembly.specs` → `lib.backends.backend_assembly.specs`；`lib.cost_calculator` → `lib.billing.cost_calculator`；`lib.image_backends` → `lib.backends.image_backends`；`lib.image_backends.registry` → `lib.backends.image_backends.registry`；`lib.project_manager` → `lib.project.project_manager`；`lib.text_backends` → `lib.backends.text_backends`；`lib.text_backends.base` → `lib.backends.text_backends.base`；`lib.text_backends.factory` → `lib.backends.text_backends.factory`；`lib.text_backends.registry` → `lib.backends.text_backends.registry`；`lib.text_generator` → `lib.backends.text_generator`；`lib.video_backends` → `lib.backends.video_backends`；`lib.video_backends.base` → `lib.backends.video_backends.base`；`lib.video_backends.registry` → `lib.backends.video_backends.registry`
- 路线中立层不依赖参考生视频子包：`lib.draft_quarantine` → `lib.script.draft_quarantine`；`lib.draft_violation` → `lib.script.draft_violation`；`lib.episode_paths` → `lib.episode.episode_paths`；`lib.reference_catalog` → `lib.references.reference_catalog`；`lib.reference_video` → `lib.script.reference_video`

## 路线中立

源：`lib.references`, `lib.script.draft_violation`, `lib.script.draft_quarantine`, `lib.episode.episode_paths`；禁止（含间接）依赖 `lib.script.reference_video`。

成立：以上模块及其传递依赖均不触达参考生视频子包。

## 核心库 → 服务端

- `lib.generation.generation_worker` → `server.services.tasks.generation_tasks`
- `lib.generation.generation_worker` → `server.services.tasks.resume_executor`

## 镜像测试移动

| 旧路径 | 新路径 | 推导依据 |
| --- | --- | --- |
| `tests/unit/lib/agent_session_store/` | `tests/unit/lib/agent/agent_session_store/` | 目录跟随 |
| `tests/unit/lib/audio_backends/` | `tests/unit/lib/backends/audio_backends/` | 目录跟随 |
| `tests/unit/lib/backend_assembly/` | `tests/unit/lib/backends/backend_assembly/` | 目录跟随 |
| `tests/unit/lib/grid/` | `tests/unit/lib/script/grid/` | 目录跟随 |
| `tests/unit/lib/image_backends/` | `tests/unit/lib/backends/image_backends/` | 目录跟随 |
| `tests/unit/lib/pricing/` | `tests/unit/lib/billing/pricing/` | 目录跟随 |
| `tests/unit/lib/project_migrations/` | `tests/unit/lib/project/project_migrations/` | 目录跟随 |
| `tests/unit/lib/prompt_rules/` | `tests/unit/lib/prompts/prompt_rules/` | 目录跟随 |
| `tests/unit/lib/prompt_templates/` | `tests/unit/lib/prompts/prompt_templates/` | 目录跟随 |
| `tests/unit/lib/reference_video/` | `tests/unit/lib/script/reference_video/` | 目录跟随 |
| `tests/unit/lib/source_loader/` | `tests/unit/lib/script/source_loader/` | 目录跟随 |
| `tests/unit/lib/speech_composition/` | `tests/unit/lib/speech/speech_composition/` | 目录跟随 |
| `tests/unit/lib/text_backends/` | `tests/unit/lib/backends/text_backends/` | 目录跟随 |
| `tests/unit/lib/video_backends/` | `tests/unit/lib/backends/video_backends/` | 目录跟随 |
| `tests/unit/lib/test_accounting_characterization.py` | `tests/unit/lib/backends/test_accounting_characterization.py` | 提及 4 次 |
| `tests/unit/lib/test_ad_reference_video_units_v7.py` | `tests/unit/lib/script/test_ad_reference_video_units_v7.py` | 提及 6 次 |
| `tests/unit/lib/test_agent_memory_index.py` | `tests/unit/lib/agent/test_agent_memory_index.py` | 文件名 |
| `tests/unit/lib/test_agent_memory_paths.py` | `tests/unit/lib/agent/test_agent_memory_paths.py` | 文件名 |
| `tests/unit/lib/test_agent_memory_store.py` | `tests/unit/lib/agent/test_agent_memory_store.py` | 文件名 |
| `tests/unit/lib/test_agent_provider_catalog.py` | `tests/unit/lib/agent/test_agent_provider_catalog.py` | 文件名 |
| `tests/unit/lib/test_agnes_shared.py` | `tests/unit/lib/backends/test_agnes_shared.py` | 文件名 |
| `tests/unit/lib/test_app_data_dir.py` | `tests/unit/lib/infra/test_app_data_dir.py` | 文件名 |
| `tests/unit/lib/test_ark_shared.py` | `tests/unit/lib/backends/test_ark_shared.py` | 文件名 |
| `tests/unit/lib/test_artifact_activation_schema_gate.py` | `tests/unit/lib/artifacts/test_artifact_activation_schema_gate.py` | 文件名 |
| `tests/unit/lib/test_artifact_manifest.py` | `tests/unit/lib/artifacts/test_artifact_manifest.py` | 文件名 |
| `tests/unit/lib/test_artifact_provenance.py` | `tests/unit/lib/artifacts/test_artifact_provenance.py` | 文件名 |
| `tests/unit/lib/test_aspect_size.py` | `tests/unit/lib/backends/test_aspect_size.py` | 文件名 |
| `tests/unit/lib/test_asset_fingerprints.py` | `tests/unit/lib/project/test_asset_fingerprints.py` | 文件名 |
| `tests/unit/lib/test_asset_name_validation.py` | `tests/unit/lib/project/test_asset_name_validation.py` | 提及 5 次 |
| `tests/unit/lib/test_asset_rename.py` | `tests/unit/lib/project/test_asset_rename.py` | 文件名 |
| `tests/unit/lib/test_asset_type_localization.py` | `tests/unit/lib/project/test_asset_type_localization.py` | 提及 3 次 |
| `tests/unit/lib/test_asset_types_product.py` | `tests/unit/lib/project/test_asset_types_product.py` | 文件名 |
| `tests/unit/lib/test_batch_admission.py` | `tests/unit/lib/generation/test_batch_admission.py` | 文件名 |
| `tests/unit/lib/test_call_failure.py` | `tests/unit/lib/billing/test_call_failure.py` | 文件名 |
| `tests/unit/lib/test_character_voice.py` | `tests/unit/lib/speech/test_character_voice.py` | 文件名 |
| `tests/unit/lib/test_content_digest.py` | `tests/unit/lib/infra/test_content_digest.py` | 文件名 |
| `tests/unit/lib/test_cost_calculator.py` | `tests/unit/lib/billing/test_cost_calculator.py` | 文件名 |
| `tests/unit/lib/test_cost_calculator_reference_video.py` | `tests/unit/lib/billing/test_cost_calculator_reference_video.py` | 文件名 |
| `tests/unit/lib/test_cost_calculator_text.py` | `tests/unit/lib/billing/test_cost_calculator_text.py` | 文件名 |
| `tests/unit/lib/test_custom_cost.py` | `tests/unit/lib/billing/test_custom_cost.py` | 提及 2 次 |
| `tests/unit/lib/test_dashscope_shared.py` | `tests/unit/lib/backends/test_dashscope_shared.py` | 文件名 |
| `tests/unit/lib/test_data_uri.py` | `tests/unit/lib/backends/test_data_uri.py` | 文件名 |
| `tests/unit/lib/test_draft_quarantine.py` | `tests/unit/lib/script/test_draft_quarantine.py` | 文件名 |
| `tests/unit/lib/test_drama_pipeline_split.py` | `tests/unit/lib/script/test_drama_pipeline_split.py` | 提及 7 次 |
| `tests/unit/lib/test_episode_paths.py` | `tests/unit/lib/episode/test_episode_paths.py` | 文件名 |
| `tests/unit/lib/test_episode_target_duration.py` | `tests/unit/lib/episode/test_episode_target_duration.py` | 文件名 |
| `tests/unit/lib/test_episode_target_volume.py` | `tests/unit/lib/episode/test_episode_target_volume.py` | 文件名 |
| `tests/unit/lib/test_generation_admission.py` | `tests/unit/lib/generation/test_generation_admission.py` | 文件名 |
| `tests/unit/lib/test_generation_batch.py` | `tests/unit/lib/generation/test_generation_batch.py` | 文件名 |
| `tests/unit/lib/test_generation_queue.py` | `tests/unit/lib/generation/test_generation_queue.py` | 文件名 |
| `tests/unit/lib/test_generation_queue_client.py` | `tests/unit/lib/generation/test_generation_queue_client.py` | 文件名 |
| `tests/unit/lib/test_generation_result.py` | `tests/unit/lib/generation/test_generation_result.py` | 文件名 |
| `tests/unit/lib/test_generation_worker_wake.py` | `tests/unit/lib/generation/test_generation_worker_wake.py` | 文件名 |
| `tests/unit/lib/test_grid_executor.py` | `tests/unit/lib/artifacts/test_grid_executor.py` | 提及 20 次 |
| `tests/unit/lib/test_grid_manager.py` | `tests/unit/lib/script/grid/test_grid_manager.py` | 文件名 |
| `tests/unit/lib/test_grok_shared.py` | `tests/unit/lib/backends/test_grok_shared.py` | 文件名 |
| `tests/unit/lib/test_image_compression_batch.py` | `tests/unit/lib/infra/test_image_compression_batch.py` | 提及 2 次 |
| `tests/unit/lib/test_image_reference_snapshot.py` | `tests/unit/lib/artifacts/test_image_reference_snapshot.py` | 文件名 |
| `tests/unit/lib/test_image_utils.py` | `tests/unit/lib/infra/test_image_utils.py` | 文件名 |
| `tests/unit/lib/test_kling_backend_base.py` | `tests/unit/lib/backends/test_kling_backend_base.py` | 文件名 |
| `tests/unit/lib/test_kling_shared.py` | `tests/unit/lib/backends/test_kling_shared.py` | 文件名 |
| `tests/unit/lib/test_ledger.py` | `tests/unit/lib/billing/test_ledger.py` | 文件名 |
| `tests/unit/lib/test_locked_episode_script_toctou.py` | `tests/unit/lib/project/test_locked_episode_script_toctou.py` | 提及 5 次 |
| `tests/unit/lib/test_logging_config.py` | `tests/unit/lib/infra/test_logging_config.py` | 文件名 |
| `tests/unit/lib/test_logging_persistence.py` | `tests/unit/lib/infra/test_logging_persistence.py` | 提及 3 次 |
| `tests/unit/lib/test_logging_utils.py` | `tests/unit/lib/infra/test_logging_utils.py` | 文件名 |
| `tests/unit/lib/test_media_generator_image_capability.py` | `tests/unit/lib/generation/test_media_generator_image_capability.py` | 文件名 |
| `tests/unit/lib/test_media_generator_resume.py` | `tests/unit/lib/generation/test_media_generator_resume.py` | 文件名 |
| `tests/unit/lib/test_minimax_shared.py` | `tests/unit/lib/backends/test_minimax_shared.py` | 文件名 |
| `tests/unit/lib/test_narration_delivery.py` | `tests/unit/lib/speech/test_narration_delivery.py` | 文件名 |
| `tests/unit/lib/test_no_env_fallback.py` | `tests/unit/lib/backends/test_no_env_fallback.py` | 提及 8 次 |
| `tests/unit/lib/test_openai_shared.py` | `tests/unit/lib/backends/test_openai_shared.py` | 文件名 |
| `tests/unit/lib/test_path_safety.py` | `tests/unit/lib/infra/test_path_safety.py` | 文件名 |
| `tests/unit/lib/test_profile_manifest.py` | `tests/unit/lib/agent/test_profile_manifest.py` | 文件名 |
| `tests/unit/lib/test_project_manager_ad_mode.py` | `tests/unit/lib/project/test_project_manager_ad_mode.py` | 文件名 |
| `tests/unit/lib/test_project_manager_compat.py` | `tests/unit/lib/project/test_project_manager_compat.py` | 文件名 |
| `tests/unit/lib/test_project_manager_content_mode_dispatch.py` | `tests/unit/lib/project/test_project_manager_content_mode_dispatch.py` | 文件名 |
| `tests/unit/lib/test_project_manager_global_assets.py` | `tests/unit/lib/project/test_project_manager_global_assets.py` | 文件名 |
| `tests/unit/lib/test_project_manager_legacy_migration.py` | `tests/unit/lib/project/test_project_manager_legacy_migration.py` | 文件名 |
| `tests/unit/lib/test_project_manager_save_validation.py` | `tests/unit/lib/project/test_project_manager_save_validation.py` | 文件名 |
| `tests/unit/lib/test_project_manager_source_kind.py` | `tests/unit/lib/project/test_project_manager_source_kind.py` | 文件名 |
| `tests/unit/lib/test_project_manager_symlink.py` | `tests/unit/lib/project/test_project_manager_symlink.py` | 文件名 |
| `tests/unit/lib/test_prompt_builders.py` | `tests/unit/lib/prompts/test_prompt_builders.py` | 文件名 |
| `tests/unit/lib/test_prompt_builders_ad.py` | `tests/unit/lib/prompts/test_prompt_builders_ad.py` | 文件名 |
| `tests/unit/lib/test_prompt_builders_reference.py` | `tests/unit/lib/prompts/test_prompt_builders_reference.py` | 文件名 |
| `tests/unit/lib/test_prompt_builders_script.py` | `tests/unit/lib/prompts/test_prompt_builders_script.py` | 文件名 |
| `tests/unit/lib/test_prompt_builders_script_duration.py` | `tests/unit/lib/prompts/test_prompt_builders_script_duration.py` | 文件名 |
| `tests/unit/lib/test_prompt_style.py` | `tests/unit/lib/prompts/test_prompt_style.py` | 文件名 |
| `tests/unit/lib/test_prompt_utils.py` | `tests/unit/lib/prompts/test_prompt_utils.py` | 文件名 |
| `tests/unit/lib/test_reference_admission.py` | `tests/unit/lib/references/test_reference_admission.py` | 文件名 |
| `tests/unit/lib/test_reference_catalog.py` | `tests/unit/lib/references/test_reference_catalog.py` | 文件名 |
| `tests/unit/lib/test_reference_compression.py` | `tests/unit/lib/references/test_reference_compression.py` | 文件名 |
| `tests/unit/lib/test_reference_image_numbering.py` | `tests/unit/lib/prompts/test_reference_image_numbering.py` | 文件名 |
| `tests/unit/lib/test_reference_video_concurrent_rmw.py` | `tests/unit/lib/project/test_reference_video_concurrent_rmw.py` | 提及 4 次 |
| `tests/unit/lib/test_registry_consistency.py` | `tests/unit/lib/project/test_registry_consistency.py` | 提及 3 次 |
| `tests/unit/lib/test_resource_paths.py` | `tests/unit/lib/project/test_resource_paths.py` | 文件名 |
| `tests/unit/lib/test_retry.py` | `tests/unit/lib/infra/test_retry.py` | 文件名 |
| `tests/unit/lib/test_script_editor.py` | `tests/unit/lib/script/test_script_editor.py` | 文件名 |
| `tests/unit/lib/test_script_models.py` | `tests/unit/lib/script/test_script_models.py` | 文件名 |
| `tests/unit/lib/test_script_models_duration_enum.py` | `tests/unit/lib/script/test_script_models_duration_enum.py` | 文件名 |
| `tests/unit/lib/test_script_models_reference.py` | `tests/unit/lib/script/test_script_models_reference.py` | 文件名 |
| `tests/unit/lib/test_script_plan_entries.py` | `tests/unit/lib/script/test_script_plan_entries.py` | 文件名 |
| `tests/unit/lib/test_script_references.py` | `tests/unit/lib/script/test_script_references.py` | 文件名 |
| `tests/unit/lib/test_script_review_binding.py` | `tests/unit/lib/script/test_script_review_binding.py` | 文件名 |
| `tests/unit/lib/test_script_skeleton.py` | `tests/unit/lib/script/test_script_skeleton.py` | 文件名 |
| `tests/unit/lib/test_script_structure_validator.py` | `tests/unit/lib/script/test_script_structure_validator.py` | 文件名 |
| `tests/unit/lib/test_slot_table.py` | `tests/unit/lib/generation/test_slot_table.py` | 提及 2 次 |
| `tests/unit/lib/test_speech_artifact_provenance.py` | `tests/unit/lib/speech/test_speech_artifact_provenance.py` | 文件名 |
| `tests/unit/lib/test_speech_presentation.py` | `tests/unit/lib/speech/test_speech_presentation.py` | 文件名 |
| `tests/unit/lib/test_speech_rate.py` | `tests/unit/lib/speech/test_speech_rate.py` | 文件名 |
| `tests/unit/lib/test_storyboard_mentions.py` | `tests/unit/lib/script/test_storyboard_mentions.py` | 文件名 |
| `tests/unit/lib/test_storyboard_sequence.py` | `tests/unit/lib/script/test_storyboard_sequence.py` | 文件名 |
| `tests/unit/lib/test_style_templates.py` | `tests/unit/lib/prompts/test_style_templates.py` | 文件名 |
| `tests/unit/lib/test_system_config.py` | `tests/unit/lib/config/test_system_config.py` | 文件名 |
| `tests/unit/lib/test_task_failure.py` | `tests/unit/lib/generation/test_task_failure.py` | 文件名 |
| `tests/unit/lib/test_task_localization.py` | `tests/unit/lib/generation/test_task_localization.py` | 提及 2 次 |
| `tests/unit/lib/test_text_generator.py` | `tests/unit/lib/backends/test_text_generator.py` | 文件名 |
| `tests/unit/lib/test_text_metrics.py` | `tests/unit/lib/infra/test_text_metrics.py` | 文件名 |
| `tests/unit/lib/test_text_utils.py` | `tests/unit/lib/infra/test_text_utils.py` | 文件名 |
| `tests/unit/lib/test_thumbnail.py` | `tests/unit/lib/infra/test_thumbnail.py` | 文件名 |
| `tests/unit/lib/test_thumbnail_fallback.py` | `tests/unit/lib/infra/test_thumbnail_fallback.py` | 文件名 |
| `tests/unit/lib/test_tts_skeleton.py` | `tests/unit/lib/generation/test_tts_skeleton.py` | 提及 9 次 |
| `tests/unit/lib/test_update_project_atomicity.py` | `tests/unit/lib/project/test_update_project_atomicity.py` | 提及 2 次 |
| `tests/unit/lib/test_usage_summary.py` | `tests/unit/lib/billing/test_usage_summary.py` | 文件名 |
| `tests/unit/lib/test_validation_messages.py` | `tests/unit/lib/infra/test_validation_messages.py` | 文件名 |
| `tests/unit/lib/test_version_manager.py` | `tests/unit/lib/artifacts/test_version_manager.py` | 文件名 |
| `tests/unit/lib/test_video_artifact_facts.py` | `tests/unit/lib/artifacts/test_video_artifact_facts.py` | 文件名 |
| `tests/unit/lib/test_video_frame_slots.py` | `tests/unit/lib/backends/test_video_frame_slots.py` | 文件名 |
| `tests/unit/lib/test_video_workflow_prompt.py` | `tests/unit/lib/generation/test_video_workflow_prompt.py` | 提及 8 次 |
| `tests/unit/lib/test_vidu_cost.py` | `tests/unit/lib/backends/test_vidu_cost.py` | 提及 5 次 |
| `tests/unit/lib/test_vidu_shared.py` | `tests/unit/lib/backends/test_vidu_shared.py` | 文件名 |
| `tests/unit/lib/test_workflow_action_types.py` | `tests/unit/lib/workflow/test_workflow_action_types.py` | 提及 2 次（并列取文件名前缀匹配） |
| `tests/unit/lib/test_workflow_plan.py` | `tests/unit/lib/workflow/test_workflow_plan.py` | 文件名 |
| `tests/unit/server/services/test_diagnostics_service.py` | `tests/unit/server/services/system/test_diagnostics_service.py` | 文件名 |
| `tests/unit/server/services/test_execute_tts_task.py` | `tests/unit/server/services/tasks/test_execute_tts_task.py` | 提及 5 次 |
| `tests/unit/server/services/test_execute_voice_sample_task.py` | `tests/unit/server/services/tasks/test_execute_voice_sample_task.py` | 提及 4 次 |
| `tests/unit/server/services/test_generation_tasks_dispatch.py` | `tests/unit/server/services/tasks/test_generation_tasks_dispatch.py` | 文件名 |
| `tests/unit/server/services/test_grid_resolution.py` | `tests/unit/lib/script/grid/test_grid_resolution.py` | 文件名 |
| `tests/unit/server/services/test_grid_split_service.py` | `tests/unit/server/services/grid/test_grid_split_service.py` | 文件名 |
| `tests/unit/server/services/test_image_edit_executor.py` | `tests/unit/server/services/tasks/test_image_edit_executor.py` | 提及 15 次 |
| `tests/unit/server/services/test_narration_delivery_tasks.py` | `tests/unit/server/services/tasks/test_narration_delivery_tasks.py` | 文件名 |
| `tests/unit/server/services/test_project_archive_ad_reference.py` | `tests/unit/server/services/project/test_project_archive_ad_reference.py` | 文件名 |
| `tests/unit/server/services/test_project_cover.py` | `tests/unit/server/services/project/test_project_cover.py` | 文件名 |
| `tests/unit/server/services/test_reference_video_e2e.py` | `tests/unit/server/services/tasks/test_reference_video_e2e.py` | 提及 14 次 |
| `tests/unit/server/services/test_reference_video_e2e_backend.py` | `tests/unit/server/services/tasks/test_reference_video_e2e_backend.py` | 提及 8 次 |
| `tests/unit/server/services/test_resume_executor.py` | `tests/unit/server/services/tasks/test_resume_executor.py` | 文件名 |
| `tests/unit/server/services/test_video_batch_admission.py` | `tests/unit/server/services/admission/test_video_batch_admission.py` | 文件名 |
| `tests/unit/server/services/test_video_caps.py` | `tests/unit/server/services/tasks/test_video_caps.py` | 文件名 |
| `tests/integration/lib/agent_session_store/` | `tests/integration/lib/agent/agent_session_store/` | 目录跟随 |
| `tests/integration/lib/project_migrations/` | `tests/integration/lib/project/project_migrations/` | 目录跟随 |
| `tests/integration/lib/reference_video/` | `tests/integration/lib/script/reference_video/` | 目录跟随 |
| `tests/integration/lib/video_backends/` | `tests/integration/lib/backends/video_backends/` | 目录跟随 |
| `tests/integration/lib/test_artifact_manifest_storage.py` | `tests/integration/lib/artifacts/test_artifact_manifest_storage.py` | 文件名 |
| `tests/integration/lib/test_asset_inventory.py` | `tests/integration/lib/project/test_asset_inventory.py` | 文件名 |
| `tests/integration/lib/test_audio_utils.py` | `tests/integration/lib/speech/test_audio_utils.py` | 文件名 |
| `tests/integration/lib/test_data_validator.py` | `tests/integration/lib/project/test_data_validator.py` | 文件名 |
| `tests/integration/lib/test_data_validator_reference.py` | `tests/integration/lib/project/test_data_validator_reference.py` | 文件名 |
| `tests/integration/lib/test_end_frame_model.py` | `tests/integration/lib/script/test_end_frame_model.py` | 提及 8 次 |
| `tests/integration/lib/test_episode_ledger.py` | `tests/integration/lib/episode/test_episode_ledger.py` | 文件名 |
| `tests/integration/lib/test_episode_planner.py` | `tests/integration/lib/episode/test_episode_planner.py` | 文件名 |
| `tests/integration/lib/test_episode_reset.py` | `tests/integration/lib/episode/test_episode_reset.py` | 文件名 |
| `tests/integration/lib/test_generation_queue_batches.py` | `tests/integration/lib/generation/test_generation_queue_batches.py` | 文件名 |
| `tests/integration/lib/test_generation_worker_capacity_table.py` | `tests/integration/lib/generation/test_generation_worker_capacity_table.py` | 文件名 |
| `tests/integration/lib/test_generation_worker_module.py` | `tests/integration/lib/generation/test_generation_worker_module.py` | 文件名 |
| `tests/integration/lib/test_generation_worker_text_lane.py` | `tests/integration/lib/generation/test_generation_worker_text_lane.py` | 文件名 |
| `tests/integration/lib/test_media_generator_module.py` | `tests/integration/lib/generation/test_media_generator_module.py` | 文件名 |
| `tests/integration/lib/test_project_asset_namespace.py` | `tests/integration/lib/project/test_project_asset_namespace.py` | 提及 6 次 |
| `tests/integration/lib/test_project_manager.py` | `tests/integration/lib/project/test_project_manager.py` | 文件名 |
| `tests/integration/lib/test_project_manager_concurrent_save.py` | `tests/integration/lib/project/test_project_manager_concurrent_save.py` | 文件名 |
| `tests/integration/lib/test_project_migration_blocking.py` | `tests/integration/lib/project/test_project_migration_blocking.py` | 提及 15 次 |
| `tests/integration/lib/test_project_migration_v8_v9.py` | `tests/integration/lib/project/test_project_migration_v8_v9.py` | 提及 4 次 |
| `tests/integration/lib/test_project_summary.py` | `tests/integration/lib/project/test_project_summary.py` | 提及 7 次 |
| `tests/integration/lib/test_script_batch_edit.py` | `tests/integration/lib/script/test_script_batch_edit.py` | 文件名 |
| `tests/integration/lib/test_script_generator.py` | `tests/integration/lib/script/test_script_generator.py` | 文件名 |
| `tests/integration/lib/test_script_generator_ad_prompt_authoring.py` | `tests/integration/lib/script/test_script_generator_ad_prompt_authoring.py` | 文件名 |
| `tests/integration/lib/test_script_generator_incremental.py` | `tests/integration/lib/script/test_script_generator_incremental.py` | 文件名 |
| `tests/integration/lib/test_script_generator_reference_branch.py` | `tests/integration/lib/script/test_script_generator_reference_branch.py` | 文件名 |
| `tests/integration/lib/test_source_revision.py` | `tests/integration/lib/project/test_source_revision.py` | 文件名 |
| `tests/integration/lib/test_speech_artifact_provenance_integration.py` | `tests/integration/lib/speech/test_speech_artifact_provenance_integration.py` | 文件名 |
| `tests/integration/lib/test_task_failure_capability.py` | `tests/integration/lib/generation/test_task_failure_capability.py` | 文件名 |
| `tests/integration/lib/test_task_terminal_events.py` | `tests/integration/lib/generation/test_task_terminal_events.py` | 文件名 |
| `tests/integration/lib/test_video_artifact_commit.py` | `tests/integration/lib/artifacts/test_video_artifact_commit.py` | 文件名 |
| `tests/integration/lib/test_visual_artifact_provenance.py` | `tests/integration/lib/artifacts/test_visual_artifact_provenance.py` | 文件名 |
| `tests/integration/lib/test_workflow_plan_adapters.py` | `tests/integration/lib/workflow/test_workflow_plan_adapters.py` | 文件名 |
| `tests/integration/lib/test_workflow_state.py` | `tests/integration/lib/workflow/test_workflow_state.py` | 文件名 |
| `tests/integration/server/services/generation_tasks_support.py` | `tests/integration/server/services/tasks/generation_tasks_support.py` | 文件名 |
| `tests/integration/server/services/reference_video_tasks_support.py` | `tests/integration/server/services/tasks/reference_video_tasks_support.py` | 文件名 |
| `tests/integration/server/services/test_ad_product_fidelity.py` | `tests/integration/server/services/tasks/test_ad_product_fidelity.py` | 提及 1 次 |
| `tests/integration/server/services/test_apply_unit_video_assets.py` | `tests/integration/server/services/tasks/test_apply_unit_video_assets.py` | 提及 8 次 |
| `tests/integration/server/services/test_artifact_version_restore.py` | `tests/integration/server/services/currency/test_artifact_version_restore.py` | 文件名 |
| `tests/integration/server/services/test_assert_duration_supported.py` | `tests/integration/server/services/tasks/test_assert_duration_supported.py` | 提及 2 次 |
| `tests/integration/server/services/test_collect_sheet_references.py` | `tests/integration/server/services/tasks/test_collect_sheet_references.py` | 提及 4 次 |
| `tests/integration/server/services/test_compute_affected_fingerprints.py` | `tests/integration/server/services/tasks/test_compute_affected_fingerprints.py` | 提及 1 次 |
| `tests/integration/server/services/test_cost_estimation_service.py` | `tests/integration/server/services/admission/test_cost_estimation_service.py` | 文件名 |
| `tests/integration/server/services/test_derivative_sheet_tasks.py` | `tests/integration/server/services/tasks/test_derivative_sheet_tasks.py` | 文件名 |
| `tests/integration/server/services/test_emit_generation_success_batch.py` | `tests/integration/server/services/tasks/test_emit_generation_success_batch.py` | 提及 1 次 |
| `tests/integration/server/services/test_execute_generation_task.py` | `tests/integration/server/services/tasks/test_execute_generation_task.py` | 提及 1 次 |
| `tests/integration/server/services/test_execute_product_task.py` | `tests/integration/server/services/tasks/test_execute_product_task.py` | 提及 1 次 |
| `tests/integration/server/services/test_execute_reference_video_task.py` | `tests/integration/server/services/tasks/test_execute_reference_video_task.py` | 提及 44 次 |
| `tests/integration/server/services/test_execute_video_task.py` | `tests/integration/server/services/tasks/test_execute_video_task.py` | 提及 2 次 |
| `tests/integration/server/services/test_formal_image_finalization.py` | `tests/integration/server/services/tasks/test_formal_image_finalization.py` | 提及 1 次 |
| `tests/integration/server/services/test_generation_context.py` | `tests/integration/server/services/tasks/test_generation_context.py` | 文件名 |
| `tests/integration/server/services/test_generation_tasks_helpers.py` | `tests/integration/server/services/tasks/test_generation_tasks_helpers.py` | 文件名 |
| `tests/integration/server/services/test_get_aspect_ratio.py` | `tests/integration/server/services/tasks/test_get_aspect_ratio.py` | 提及 1 次 |
| `tests/integration/server/services/test_jianying_draft_routes.py` | `tests/integration/server/services/presentation/test_jianying_draft_routes.py` | 提及 2 次 |
| `tests/integration/server/services/test_jianying_draft_service.py` | `tests/integration/server/services/presentation/test_jianying_draft_service.py` | 文件名 |
| `tests/integration/server/services/test_presentation_read_model.py` | `tests/integration/server/services/presentation/test_presentation_read_model.py` | 文件名 |
| `tests/integration/server/services/test_project_archive_reference_video.py` | `tests/integration/server/services/project/test_project_archive_reference_video.py` | 文件名 |
| `tests/integration/server/services/test_project_archive_service.py` | `tests/integration/server/services/project/test_project_archive_service.py` | 文件名 |
| `tests/integration/server/services/test_project_events_service.py` | `tests/integration/server/services/project/test_project_events_service.py` | 文件名 |
| `tests/integration/server/services/test_prompt_preview.py` | `tests/integration/server/services/admission/test_prompt_preview.py` | 文件名 |
| `tests/integration/server/services/test_reference_image_clamping.py` | `tests/integration/server/services/tasks/test_reference_image_clamping.py` | 提及 2 次 |
| `tests/integration/server/services/test_reference_video_duration_resolution.py` | `tests/integration/server/services/tasks/test_reference_video_duration_resolution.py` | 提及 8 次 |
| `tests/integration/server/services/test_reference_visual_basis.py` | `tests/integration/server/services/tasks/test_reference_visual_basis.py` | 提及 2 次 |
| `tests/integration/server/services/test_render_unit_prompt.py` | `tests/integration/server/services/tasks/test_render_unit_prompt.py` | 提及 2 次 |
| `tests/integration/server/services/test_resume_custom_endpoint_chain.py` | `tests/integration/server/services/tasks/test_resume_custom_endpoint_chain.py` | 提及 7 次 |
| `tests/integration/server/services/test_script_review.py` | `tests/integration/server/services/project/test_script_review.py` | 文件名 |
| `tests/integration/server/services/test_stage_provider_media_for_task.py` | `tests/integration/server/services/tasks/test_stage_provider_media_for_task.py` | 提及 1 次 |
| `tests/integration/server/services/test_video_artifact_currency.py` | `tests/integration/server/services/currency/test_video_artifact_currency.py` | 文件名 |
| `tests/integration/server/services/test_workflow_planner.py` | `tests/integration/server/services/project/test_workflow_planner.py` | 文件名 |
| `tests/integration/server/services/test_build_storyboard_video_visual_basis.py` | `tests/integration/lib/artifacts/test_build_storyboard_video_visual_basis.py` | 提及 2 次 |
| `tests/integration/server/services/test_resolve_reference_assets.py` | `tests/integration/server/services/tasks/test_resolve_reference_assets.py` | 随辅助模块 reference_video_tasks_support |
