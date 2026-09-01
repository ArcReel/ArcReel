# k=25 / cover>=0.8 / 同文件「整体近似重复族」逐条判定（全量 63 族）

判定四类：

- **A 可删**：其中一条的输入与断言都已被另一条实质覆盖（含 CONTRIBUTING「重复弱化」：同路径、断言更弱）。
- **B 可合并**：结构同形、只差输入 / 期望值，或断言互补而 setup 重复 —— 可并成一条 `@parametrize` 或一条用例。减重，不可删。
- **C 正当重复—各守不同契约点**：断言形状不同，或输入差异本身就是被守的契约（#2265 的 pricing 形态）。
- **D 正当重复—setup 样板**：重复段是构造 / 打桩样板，两条用例本身不重复（cover>=0.8 过滤器在短用例上的假阳）。

判定材料：`python3 research/jscpd-survey/dump_families.py research/jscpd-survey/raw/mapped-k25.json` 打印的逐族源码。

| # | 判 | 文件 | 用例 | 理由 |
| ---: | :-: | --- | --- | --- |
| 0 | C | unit/server/routers/test_providers_api.py | TestGetProviderConfig 4 条 | 同一 6 行 patch 栈，断言分别落在 secret_field_groups / secret_fields / supports_base_url，形状不同 |
| 1 | B | integration/lib/config/test_config_resolver.py | TestDefaultBackends i2i / t2i / explicit | 同 settings 同断言，只差桶参数 |
| 2 | B | integration/lib/test_audio_utils.py | TestProbeExistingVideoDuration 3 条 | 只差 ffprobe JSON 与期望时长 |
| 3 | B | integration/server/routers/test_capability_overrides_api.py | TestSaveDropsUnlistedOverrides / SaveValidatesOpenOverrides | 只差 capability_overrides 输入与期望值 |
| 4 | B | integration/server/services/test_jianying_draft_routes.py | empty / control-chars / long draft_path | 同断言 422，只差 draft_path |
| 5 | B | unit/lib/test_retry.py | TestWithRetryAsync 3 条 | 只差 side_effect 与 call_count |
| 6 | B | unit/server/routers/test_assistant_routes.py | messages / snapshot / stream 均 410 | 只差 URL 后缀，最干净的参数化候选 |
| 7 | C | unit/server/routers/test_grids_router.py | 3 条错误映射 | 断言各不相同（500 不泄漏 / 400 / 500 非非法名） |
| 8 | **A** | unit/server/routers/test_providers_api.py | TestPatchProviderConfig::test_returns_204 ⊂ ::test_non_null_value_calls_set | 同路由同请求形状，断言是后者的真子集（重复弱化） |
| 9 | **A** | integration/lib/config/test_config_resolver.py | TestVideoGenerateAudio::test_global_true ≡ ::test_project_none_skips_override | #2266 存量第 1 组 |
| 10 | C | integration/lib/config/test_config_resolver.py | image / video backend 无 ready 供应商 | 被测的是两个不同生产函数 |
| 11 | C | integration/lib/config/test_config_resolver.py | TestPayloadPinnedVideoModel 2 条 | 传不传 capability 是被守的契约，期望值不同 |
| 12 | C | integration/lib/test_data_validator.py | voiceover 带 speaker / speaker 非字符串 | 两条独立校验规则，同一错误子串 |
| 13 | B | integration/lib/test_data_validator.py | TestGenerationModeValidation 2 条 | 双方都带 parametrize、函数体相同，可并成一份参数表 |
| 14 | C | integration/lib/test_media_generator_module.py | 首帧比例自适应 2 条 | 断言落点不同（ledger vs backend.calls） |
| 15 | C | integration/lib/test_script_batch_edit.py | manifest 写失败 / 提交后失败回滚 | 两个失败注入点，断言相同但差异即契约 |
| 16 | D | integration/lib/test_script_generator.py | prompt 追加 / script_plan 非对象 | 重复段是 `_write_drama_ledger_project` 建项目样板 |
| 17 | B | integration/lib/test_script_generator.py | _resolve_supported_durations 2 条 | 只差 gen_mode 与期望时长集合 |
| 18 | B | integration/lib/test_task_failure_capability.py | capability / video bucket code 三语 | 双方都带 parametrize、体逐字同，宜抽 helper 或并表 |
| 19 | B | integration/lib/test_task_failure_capability.py | narration / projection code 三语 | 同上 |
| 20 | C | integration/server/agent_runtime/sdk_tools/test_patch_tools.py | 重名冲突 / 幂等提示 | 断言的错误文案不同 |
| 21 | C | integration/server/services/test_execute_video_task.py | 无声模型 / legacy dialogue 关音频 | 两条不同的门控路径进同一出口 |
| 22 | B | integration/server/services/test_script_review.py | 有 / 无 episode target | 只差是否 `_set_episode_target_duration` 与期望值 |
| 23 | C | unit/lib/backend_assembly/test_backend_assembly_specs.py | kling 单 api_key / api_key 优先 | 期望 kwargs 相同，输入差异即优先级契约 |
| 24 | B | unit/lib/config/test_config_registry.py | 空分组 / 未覆盖键 | 只差 credential_groups 与 match 串 |
| 25 | C | unit/lib/custom_provider/test_declarative_video_backend.py | 混排文本素材 / 小于阈值素材 | 30 行 setup 相同、断言不同（prefix 在场 vs 长度 < 256） |
| 26 | B | unit/lib/custom_provider/test_model_discovery.py | is_enabled / display_name | 同一次 discover，两条各断言一个字段，宜并成一条 |
| 27 | C | unit/lib/db/models/test_credential_api.py | 部分提交不清空 / 完整切换清空 | 断言方向相反 |
| 28 | C | unit/lib/grid/test_grid_layout.py | 默认 allow_large_grid / 显式 False | 被守的契约是「默认值就是 False」；边界条目 |
| 29 | C | unit/lib/image_backends/test_agnes_image_backend.py | data 空 / 下载失败 | 两个失败点，同为 RuntimeError |
| 30 | C | unit/lib/image_backends/test_ark.py | 缺 api_key / env 兜底已删除 | env 存在与否即契约 |
| 31 | B | unit/lib/image_backends/test_ark.py | api_key 透传 / base_url 透传 | 只差构造参数与期望记录 |
| 32 | B | unit/lib/image_backends/test_ark.py | seedream-3 尺寸表 / 未知比例回退 | 只差 aspect_ratio 与期望 size |
| 33 | C | unit/lib/image_backends/test_grok.py | 缺 api_key / 空字符串 api_key | 缺失与空串是两个契约点 |
| 34 | D | unit/lib/image_backends/test_kling_image_backend.py | bearer 头 / 多图取首张 | 重复段是 4 行 route 打桩 |
| 35 | B | unit/lib/image_backends/test_minimax_image_backend.py | 缺文件 / 空路径 | 只差 ReferenceImage(path=...) 与期望 names |
| 36 | B | unit/lib/image_backends/test_openai_image_backend.py | capabilities / name_and_model | 同构造，断言互补，宜并成一条 |
| 37 | C | unit/lib/image_backends/test_openai_image_backend.py | 默认 mode / edits_only | 断言形状不同（含于 vs 等于集合） |
| 38 | B | unit/lib/image_backends/test_quality_propagation.py | quality 传 / 不传 | 30 余行 MagicMock 装配逐字相同，只差 SettlementInput(quality=...) |
| 39 | C | unit/lib/project_migrations/test_project_migration_v3_v4.py | int / str schema_version | 字符串归一化是独立契约点 |
| 40 | B | unit/lib/speech_composition/test_speech_composition.py | 结构化字段损坏 / 不可用形状 | 双方都带 parametrize、体逐字同 |
| 41 | C | unit/lib/test_asset_rename.py | NFD 等价键收编 2 条 | 改名目标与期望键不同 |
| 42 | B | unit/lib/test_generation_queue.py | provider 无 model / 完全解析不出 | 只差 ProviderModel 入参与期望 provider_id |
| 43 | **A** | unit/lib/test_logging_persistence.py | test_file_handler_disabled_by_env ⊂ test_disabled_env_accepts_aliases | 后者 parametrize 值含 `"1"`，函数体与 fixture 完全相同 |
| 44 | D | unit/lib/test_narration_delivery.py | 等长不加速 / 保留视觉档位 | 重复段是 12 行 prepare_narration_delivery 装配 |
| 45 | C | unit/lib/test_reference_compression.py | 小 jpeg / 小重格式 passthrough | 输入格式差异即契约 |
| 46 | C | unit/lib/text_backends/test_base.py | 合法 JSON / 缺字段 | 断言方向相反（is None / is not None） |
| 47 | B | unit/lib/text_backends/test_grok.py | name / default_model | 同构造，断言互补，宜并成一条 |
| 48 | C | unit/lib/video_backends/test_dashscope_video_backend.py | ConnectError 重试 / 真 503 重试 | 重试触发源不同，post 次数不同 |
| 49 | C | unit/lib/video_backends/test_dashscope_video_backend.py | 别名分派 / 恒有声型号 | 断言方向相反；双方都带 parametrize |
| 50 | B | unit/lib/video_backends/test_openai_video_backend.py | capabilities / name_and_model | 同构造，断言互补，宜并成一条 |
| 51 | C | unit/lib/video_backends/test_video_backend_base.py | 轮询至完成 / 瞬态错误重试 | side_effect 与 await_count 不同 |
| 52 | C | unit/lib/video_backends/test_video_backend_base.py | should_retry_poll / should_retry_download | 两个不同生产函数（异常清单可抽常量） |
| 53 | D | unit/lib/video_backends/test_video_backend_gemini.py | 限流器 / 无 negative_prompt | 重复段是 10 行 operation 打桩 |
| 54 | B | unit/server/agent_runtime/test_session_manager_store_injection.py | env batched / 默认 eager | 只差 setenv 与期望值 |
| 55 | C | unit/server/routers/test_endpoint_tests_api.py | 超上限 / 换字段名仍计数 | 断言相同，输入差异即「上限不可绕过」契约 |
| 56 | B | unit/server/routers/test_grid_router.py | generate / regenerate 拒 ad 项目 | 20 行建 app 样板逐字重复（含就地重声明 _AdPM），端点不同 |
| 57 | B | unit/server/routers/test_providers_api.py | test_returns_200_for_known_provider / test_ready_status_when_active_credential | 同请求同打桩，断言互补，宜并成一条 |
| 58 | C | unit/server/routers/test_providers_api.py | 未知 provider 404 / 未配置 status | 请求的 provider 不同 |
| 59 | B | unit/server/routers/test_providers_api.py | test_returns_200 / test_response_has_required_fields | 同请求同打桩，断言互补，宜并成一条 |
| 60 | D | unit/server/routers/test_system_config_options.py | 排除禁用模型 / provider_names 为空 | 重复段是 20 行 create_provider 装配 |
| 61 | C | unit/server/test_auth_kill_switch.py | get_current_user / get_current_user_flexible | 两个不同生产函数 |
| 62 | B | unit/server/test_startup_assertions.py | 全零静默 / 有计数打日志 | 只差 stats 与断言值 |

## 汇总

| 判定 | 族数 | 占比 |
| --- | ---: | ---: |
| A 可删 | 3 | 4.8% |
| B 可合并（减重，不可删） | 27 | 42.9% |
| C 正当重复—各守不同契约点 | 28 | 44.4% |
| D 正当重复—setup 样板（过滤器假阳） | 5 | 7.9% |
| 合计 | 63 | 100% |
