# 存量测试负价值形态盘点：测 mock 本身 / patch 私有符号

盘点 `tests/` 下两类可机械识别的形态，作为「无意义测试」判据讨论的数据底座。只读扫描，未改动任何测试。
所有数字由 `scripts/audit_test_doubles.py` 产出，可复跑复现（用法见附录）。

扫描基线：`origin/main` @ `8006659`，432 个测试文件、8169 个 test 函数、2756 处 patch 站点。

## 摘要

| 指标 | 数量 |
| --- | ---: |
| 类 1：全部断言都落在替身调用记录上的 test 函数 | **122**（占 8169 的 1.5%），分布在 **34** 个文件 |
| 类 1：抽样人工核对 | 31 条，误报 **0** |
| 类 2：patch 目标命中 `lib.` / `server.` 下 `_` 前缀私有符号 | **315** 处 / **69** 个符号 / **45** 个文件 |
| 其中目标符号确由该模块自身定义（非 import 进来的引用） | **313** 处 |
| 类 2：integration 用例 patch 被测 module 自身定义的公共入口 | **4** 处 |
| 对照项：integration 用例 patch 被测 module 内的协作者引用（合法 seam 形态） | 306 处 |
| 类 2 动机分档 | 绕外部 I/O **145** ／ 绕轮询·重试等待常量 **90** ／ 绕被测逻辑本身 **84** |
| 附带：一个断言都没有的 test 函数 | 47 |

两个结论先行：

1. **类 1 高度聚集在供应商适配层**。122 条分布在 34 个文件，前两个文件（`test_backend_assembly_specs.py`、`test_custom_provider_factory.py`）就占 36 条；断言主体 Top 3 是 `mock_create`(34)、`mock_cls`(16)、`persist`(14)——全是「后端构造函数收到了什么参数」「持久化 helper 被以什么参数调用」。这批用例的争议点不在于是否命中机械口径，而在于「出站请求/构造参数是不是适配器的可观察行为」，正是判据讨论要拍板的地方。
2. **integration 标记下「mock 被测 module 公共入口」几乎不存在（4 处）**，而看似同形的 306 处实际是 patch 被测 module 内的协作者引用（`patch("server.services.generation_tasks.get_project_manager")` 这类，符号定义在别的模块）。只按目标字符串前缀判定会把这 306 处一并误报——区分二者需要读生产代码判定符号归属，脚本已内建这一步。

## 方法与局限

### 类 1 识别口径

对每个 test 函数（含 async、含类内方法）先解析出「替身绑定」集合，再给每条断言分类：

- 替身绑定来源：`@patch(...)` 装饰器注入的位置参数（`new=` 形式不注入）、`with patch(...) as x`、`x = MagicMock()/AsyncMock()/Mock()/create_autospec()/patch(...)`、`x = <已知替身>.<attr>`、`patcher.start()` 的返回值；以及「调用记录容器」——函数体内 `x = []`，且 `x` 在传给 `monkeypatch.setattr` / `patch` 的嵌套 lambda / def 里被 `append`。
- 判为**替身断言**：`X.assert_called*()` / `assert_awaited*()` / `assert_not_called()` / `assert_has_calls()` 等表达式语句（这些方法只存在于 mock 上，无需解析 `X` 即可判定）；断言表达式里出现 `call_args` / `call_args_list` / `call_count` / `await_args` / `await_count` / `mock_calls` / `called` 等属性；或断言表达式引用的所有自由名都在替身绑定集合内。
- 判为**实质断言**：其余 `assert`；`with pytest.raises/warns(...)`；调用 `assert*` / `check_*` / `verify_*` / `expect_*` 前缀的断言辅助函数（保守计入实质断言，宁可漏报类 1）。
- 类 1 = 替身断言 ≥ 1 且实质断言 = 0。

局限（按影响排序）：

- **漏报：手写 fake 的调用日志不计**。仓库里存在 `_FakePM` / `_FakeQueue` 这类手写替身，`assert fake.calls == [...]` 形式的断言当前判为实质断言。要覆盖需要跨文件解析测试内定义的 fake 类，本轮未做。
- **漏报：断言辅助函数一律计为实质断言**。若某个 `_assert_xxx(...)` 内部其实只断言替身调用记录，本轮会漏掉。
- **误报风险：出站请求断言**。`assert client.post.call_args.kwargs["json"][...] == ...` 机械上是断言替身调用记录，但对适配器而言这可能就是它唯一的可观察行为。本报告如实计入，并在类 1 样本里单列这一类。
- 已修掉的一个误报源：早期版本按 `.patch` 后缀识别 patch 调用，把 `client.patch(...)`（FastAPI TestClient 的 HTTP 方法）当成 `mock.patch`，于是 `resp = client.patch(...)` 后的 `assert resp.status_code == 422` 被判为替身断言。修正后类 1 从 174 降到 122（52 条误报）。当前实现用严格白名单 `patch` / `mock.patch` / `unittest.mock.patch` / `mocker.patch` 及其 `.object` 形式。

### 类 2 识别口径

采集全部 `patch("...")` / `patch.object(obj, "attr")` / `monkeypatch.setattr(...)` 站点，把目标解析成点分符号：

- **别名解析是作用域感知的**。`tests/server/agent_runtime/test_sdk_tools.py` 里几十个测试各自 `from ... import X as mod` 再 `monkeypatch.setattr(mod, "_foo", ...)`，模块级 last-wins 的别名表会把它们全部归到最后一次 import 的模块上。脚本为每个函数单独建局部别名表覆盖模块级表。修正前 `_i2i_provider_available` 被错记在 `enqueue_videos` 名下，修正后正确归到 `enqueue_image_edits`。
- **符号归属按生产代码判定**。对解析出的模块，读 `lib/` `server/` 下对应的 `.py`，判断末段符号是该模块自身定义（`def` / `class` / 模块级赋值 / 类成员）还是 import 进来的引用。这一步区分了「挖空被测实现」与「在既有 import 边界上换协作者」。
- 私有判定：目标点分路径里存在 `_` 开头（非 dunder）的段。
- 被测 module 启发式：测试文件名去掉 `test_` 前缀，与文件内 `lib.` / `server.` import 的模块末段做前缀匹配（`stem == last` / `stem` 以 `last_` 开头 / `last` 以 `stem` 开头），取匹配最长者；无匹配时取被 import 次数最多的 `lib.` / `server.` 模块。候选先用「磁盘上是否存在同名模块文件」过滤掉符号 import。人工核对了 integration 命中集涉及的 23 个文件的映射结果，全部正确（如 `tests/test_generation_tasks_service.py` → `server.services.generation_tasks`，`tests/server/agent_runtime/test_sdk_tools.py` → `server.agent_runtime.sdk_tools`）。
- 「integration 命中被测 module」按包前缀匹配，因此 `tests/server/agent_runtime/test_sdk_tools.py`（被测 module 判为 `sdk_tools` 包）会把包内任一子模块的目标算作命中。这是刻意放宽。
- **漏报：宿主是局部变量的 patch 不计**。`patch.object(mgr, "_evict_one")` 里 `mgr` 是函数内构造的实例，静态解析拿不到它的类型，目标无法还原成 `lib.` / `server.` 点分路径，因而不计入 315 处。`tests/test_session_lifecycle.py` 里这种形态成片存在。类 2 的真实规模只会大于报告数字。

动机分档：先按符号名正则分「轮询/重试等待常量」→「外部 I/O」→ 其余「被测逻辑本身」；随后逐符号读生产代码人工复核了全部 73 个 distinct 目标，正则判错的 21 个写进脚本里的 `MOTIVE_OVERRIDES` 表（含判定依据注释）。因此三档数字仍由脚本产出且可复现，但其中 21 个符号的归档依据是人工判定而非正则。

### 其他

- 扫描不执行任何测试，纯 AST 静态分析，零第三方依赖。
- 多次运行数字稳定（早期版本因遍历 set 导致「被测 module」在并列时抖动，已改为排序遍历）。
- `tests/conftest.py`、`tests/fakes.py` 等非 `test_*.py` 的辅助文件同样被扫描（patch 站点会计入，其中不属于任何 test 函数的记为 `<module>`）。

## 类 1 结果：全部断言落在替身调用记录上

122 条，34 个文件。按文件全量（数字为 `该文件类 1 条数 / 该文件 test 函数总数`）：

| 条数/总数 | 文件 |
| --- | --- |
| 20/42 | `tests/test_backend_assembly_specs.py` |
| 16/33 | `tests/test_custom_provider_factory.py` |
| 8/70 | `tests/test_dashscope_video_backend.py` |
| 7/20 | `tests/test_image_backends/test_ark.py` |
| 6/28 | `tests/test_dashscope_image_backend.py` |
| 6/62 | `tests/test_video_backend_base.py` |
| 5/10 | `tests/test_backend_assembly_loader.py` |
| 5/19 | `tests/test_session_lifecycle.py` |
| 5/9 | `tests/test_text_backends/test_factory.py` |
| 4/30 | `tests/test_text_backends/test_ark.py` |
| 3/70 | `tests/test_kling_video_backend.py` |
| 3/27 | `tests/test_minimax_image_backend.py` |
| 3/44 | `tests/test_text_backends/test_instructor_support.py` |
| 2/12 | `tests/test_ark_shared.py` |
| 2/30 | `tests/test_audio_backends.py` |
| 2/44 | `tests/test_minimax_video_backend.py` |
| 2/23 | `tests/test_model_discovery.py` |
| 2/26 | `tests/test_newapi_video_backend.py` |
| 2/28 | `tests/test_retry.py` |
| 2/10 | `tests/test_text_backends/test_agnes_text_backend.py` |
| 2/10 | `tests/test_thumbnail_fallback.py` |
| 2/50 | `tests/test_video_backend_ark.py` |
| 2/25 | `tests/test_video_backend_gemini.py` |
| 1/22 | `tests/test_agnes_image_backend.py` |
| 1/39 | `tests/test_agnes_video_backend.py` |
| 1/1 | `tests/test_app_startup_migration.py` |
| 1/14 | `tests/test_auth_api_key.py` |
| 1/31 | `tests/test_execute_tts_task.py` |
| 1/48 | `tests/test_generation_queue_client.py` |
| 1/19 | `tests/test_image_edit_executor.py` |
| 1/5 | `tests/test_openai_connection.py` |
| 1/25 | `tests/test_openai_video_backend.py` |
| 1/26 | `tests/test_resume_executor.py` |
| 1/44 | `tests/test_v2_video_generations_backend.py` |

标记分布：`unit` 117、`unit + asyncio` 4、`unit + parametrize` 1。**没有一条落在 integration 标记下。**

被断言的替身主体 Top 10：`mock_create` 34、`mock_cls` 16、`persist` 14、`client.post` 10、`mock_evict` 5、`post` 3、`client.images.generate` 3、`mock_ark_cls` 2、`mock_client.audio.speech.create` 2、`get` 2。

### 样本

**A. 断言构造参数（占比最大的一类）**

- `tests/test_backend_assembly_specs.py:49` `TestBuildSimpleBaseUrlPriority::test_user_base_url_wins_over_registry_default` — 调 `spec.build_backend(...)` 后只有 `mock_create.assert_called_once_with("ark", api_key=..., model=..., base_url=...)`。
- `tests/test_custom_provider_factory.py:125` `TestEndpointDispatch::test_openai_tts_appends_v1` — 调 `create_custom_backend(...)` 后只有 `mock_cls.assert_called_once_with(api_key=..., base_url=".../v1", ...)`。
- `tests/test_backend_assembly_loader.py:100` `TestAssembleBuiltinEndToEnd::test_text_simple_end_to_end` — 注释写着「端到端」，也确实真跑了凭证装载与 resolver，但唯一断言仍是 `mock_create.assert_called_once_with(...)`。
- `tests/test_video_backend_ark.py:695` `TestArkVideoBackendBaseUrl::test_custom_base_url_passed_through` — 构造 `ArkVideoBackend` 后只断言 `mock_create.assert_called_once_with(api_key=..., base_url=...)`。

**B. 断言出站请求体（适配器契约，价值判断有争议）**

- `tests/test_dashscope_image_backend.py:128` `TestTextToImage::test_wan_default_size_follows_aspect` — 唯一断言 `client.post.call_args.kwargs["json"]["parameters"]["size"] == "1440*2560"`。
- `tests/test_dashscope_video_backend.py:286` `TestReferenceToVideo::test_r2v_ref_limit_wan_5` — 唯一断言 `len(post.call_args.kwargs["json"]["input"]["media"]) == 5`。
- `tests/test_image_backends/test_ark.py:192` `TestArkImageBackendGenerate::test_size_for_seedream_3_uses_1k_table` — 唯一断言 `mock_client.images.generate.call_args.kwargs["size"] == "720x1280"`。
- `tests/test_minimax_image_backend.py:161` `TestTextToImage::test_no_seed_field_when_unset` — 唯一断言 `"seed" not in client.post.call_args.kwargs["json"]`。

**C. 断言内部协作被调用（替身即被测协作者）**

- `tests/test_app_startup_migration.py:31` `test_startup_invokes_project_migrations` — 跑完 lifespan 后只有 `run_mock.assert_called_once()` + `cleanup_mock.assert_called_once()`；该文件仅此一个 test 函数。
- `tests/test_session_lifecycle.py:254` `TestEnsureCapacity::test_evicts_completed_session_when_no_idle` — `patch.object(mgr, "_evict_one")` 后只断言 `mock_evict.assert_called_once_with(completed)`（同时命中类 2）。
- `tests/test_resume_executor.py:719` `test_reference_resume_post_production_does_not_reproject_tts` — 约 40 行 setup，唯一断言 `output_guard.assert_not_awaited()`。
- `tests/test_auth_api_key.py:120` `TestVerifyAndGetPayloadAsync::test_jwt_path_not_called_for_api_key` — 唯一断言 `mock_jwt.assert_not_called()`。
- `tests/test_video_backend_base.py:682` `TestProviderJobIdPersistenceMixin::test_worker_path_persists_via_module_helper` — 唯一断言 `persist.assert_awaited_once_with("local-task-1", "job-1", provider="ark", endpoint=None, base_url=None)`。

### 抽样核对结论

对 122 条按固定步长（步长 7，起点 0 与 3）取两批共 34 条、去重后 **31 条 distinct** 逐条读源码核对：

- 误报 **0 条**（31/31 确为「所有断言都作用于替身调用记录，无任何返回值/状态/副作用断言」）。
- 按 95% 置信度、31 样本 0 命中的经验上界估算，真实误报率上界约 10%，即 122 条中最多约 12 条可能不成立。
- 核对同时暴露了上一节记录的 `client.patch` 误报源（在修正前的 174 条里，18 条抽样命中 5 条）。修正后重新抽样才得到上述结果。
- 漏报未做定量估计。已知漏报通道见「方法与局限」，方向上真实规模只会大于 122。

## 类 2 结果：patch 私有符号 / 被测公共入口

### 按目标符号（Top 30，共 69 个 distinct 私有符号 / 315 处）

| 处数 | 目标符号 | 动机档 |
| ---: | --- | --- |
| 26 | `server.agent_runtime.sdk_tools.text_generation._fetch_caps_with_fallback` | 外部 I/O |
| 23 | `lib.video_backends.agnes._POLL_INTERVAL_SECONDS` | 轮询常量 |
| 19 | `lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS` | 轮询常量 |
| 15 | `lib.video_backends.newapi._POLL_INTERVAL_SECONDS` | 轮询常量 |
| 13 | `lib.retry._compute_wait` | 轮询常量 |
| 12 | `server.agent_runtime.sdk_tools._context.resolve_video_caps` | 外部 I/O |
| 11 | `server.agent_runtime.sdk_tools.enqueue_image_edits._i2i_provider_available` | 外部 I/O |
| 11 | `lib.generation_worker.GenerationWorker._process_resume_task` | 被测逻辑 |
| 10 | `lib.config.anthropic_probe._post` | 外部 I/O |
| 10 | `lib.video_backends.v2_video_generations._POLL_INTERVAL_SECONDS` | 轮询常量 |
| 9 | `server.routers.custom_providers._invalidate_caches` | 被测逻辑 |
| 8 | `server.agent_runtime.sdk_tools.text_generation._fetch_reference_caps_with_fallback` | 外部 I/O |
| 8 | `lib.config.anthropic_probe._get` | 外部 I/O |
| 7 | `lib.artifact_manifest._O_NOFOLLOW` | 外部 I/O |
| 7 | `server.routers.system_config._read_app_version` | 外部 I/O |
| 6 | `lib.generation_worker.GenerationWorker._requeue_single_task` | 被测逻辑 |
| 6 | `lib.image_backends.kling._POLL_INTERVAL_SECONDS` | 轮询常量 |
| 5 | `server.agent_runtime.session_manager.SessionManager._ensure_capacity` | 被测逻辑 |
| 5 | `lib.script_generator.ScriptGenerator._fetch_video_capabilities` | 外部 I/O |
| 5 | `lib.script_generator.ScriptGenerator._resolve_supported_durations` | 被测逻辑 |
| 5 | `server.agent_runtime.sdk_tools.text_generation._resolve_video_capabilities` | 外部 I/O |
| 5 | `server.routers.system_config._get_latest_release` | 外部 I/O |
| 4 | `server.agent_runtime.session_manager.SessionManager._build_options` | 被测逻辑 |
| 4 | `server.auth._verify_api_key` | 外部 I/O |
| 4 | `server.routers.custom_providers._run_discover` | 外部 I/O |
| 4 | `lib.artifact_activation._commit_schema_version` | 被测逻辑 |
| 3 | `server.app._DOCKERENV_PATH` | 外部 I/O |
| 3 | `server.app._CGROUP_PATH` | 外部 I/O |
| 3 | `server.routers.agent_chat._collect_reply` | 被测逻辑 |
| 3 | `lib.audio_utils._ffprobe_available` | 外部 I/O |

patch 手法分布：`patch("字符串")` 156 处、`monkeypatch.setattr` 155 处、`patch.object` 8 处。

私有 patch 最集中的测试文件：`tests/server/agent_runtime/test_sdk_tools.py` 61、`tests/test_agnes_video_backend.py` 28、`tests/test_generation_worker_module.py` 24、`tests/test_newapi_video_backend.py` 21、`tests/test_kling_video_backend.py` 19。

### integration 用例 patch 被测 module 自身定义的公共入口（4 处）

| 位置 | 目标 | 说明 |
| --- | --- | --- |
| `tests/server/test_presentations_router.py:58` | `server.routers.presentations.get_presentation_read_model` | 在 `_client` helper 里换掉路由自身的读模型工厂 |
| `tests/server/test_presentations_router.py:60` | `server.routers.presentations.get_presentation_bundle_service` | 同上，换掉 bundle service 工厂 |
| `tests/test_jianying_draft_routes.py:78` | `server.routers.projects.get_jianying_draft_service` | `_client` helper 里换掉草稿服务工厂 |
| `tests/test_cost_estimation_service.py:1358` | `server.services.cost_estimation.quote_video_request_from_price` | `TestCostEstimationService::test_reference_video_tts_quote_uses_current_visual_tier_for_zero_or_incremental_cost` 里换掉本模块的计价函数 |

前三处是路由层的依赖工厂（形态上更接近 DI 覆盖），第四处是把被测模块自己的计价函数换掉后再测该模块的另一条路径——四处里唯一实质踩线的一处。

对照：另有 **306 处** integration 用例 patch 了被测 module 命名空间下的符号，但那些符号定义在别的模块（`patch("server.services.generation_tasks.get_project_manager")` 35 处、`...resolve_generation_context` 34 处、`server.agent_runtime.sdk_tools.enqueue_videos.batch_enqueue_and_wait` 45 处等），属于「在 import 边界上换协作者」，不构成 marker 文档禁止的形态。

### 动机三档

| 档 | 处数 | 占比 | 符号数 |
| --- | ---: | ---: | ---: |
| 绕外部 I/O | 145 | 45% | 30 |
| 绕轮询/重试等待常量 | 90 | 28% | 10 |
| 绕被测逻辑本身 | 84 | 26% | 33 |

（分母 319 = 私有符号 315 处 + integration 公共入口 4 处。）

**绕轮询/重试等待常量（90 处 / 10 个符号）** — 全部是 5 个视频/图片 backend 模块的模块级轮询常量，加上 `lib.retry._compute_wait`：

- `tests/test_agnes_video_backend.py:168` `patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0)`
- `tests/test_kling_video_backend.py:596` `patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0)`
- `tests/test_newapi_video_backend.py` 对 `lib.video_backends.newapi._POLL_INTERVAL_SECONDS` 的 15 处
- `lib.retry._compute_wait` 的 13 处散在 4 个 backend 测试文件里（newapi 4、v2_video_generations 4、agnes 3、vidu 2），全部用于让重试链路不真等退避，如 `tests/test_agnes_video_backend.py:886` `TestSubmitResilience::test_submit_retries_on_503_busy`
- `tests/test_agnes_video_backend.py` 另有 `_MIN_POLL_TIMEOUT_SECONDS` / `_POLL_TIMEOUT_PER_SECOND` 各 1 处

**绕外部 I/O（145 处 / 30 个符号）**：

- `tests/server/agent_runtime/test_sdk_tools.py:5505` `monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)` — 避开 `ConfigResolver` 的 DB 查询
- `tests/server/agent_runtime/test_sdk_tools.py:1639` `monkeypatch.setattr(mod, "_i2i_provider_available", fake_i2i)` — 同上
- `tests/test_anthropic_probe.py` 对 `lib.config.anthropic_probe._post` / `._get` 的 18 处 — 避开真实 HTTP 探测
- `tests/lib/test_script_generator_reference_branch.py:190` `patch.object(ScriptGenerator, "_fetch_video_capabilities", ...)`（该处标记为 `integration`）
- `tests/test_system_version_api.py` 对 `server.routers.system_config._read_app_version` / `._get_latest_release` 的 12 处 — 避开读 `pyproject.toml` 与 GitHub API

**绕被测逻辑本身（84 处 / 33 个符号）**：

- `tests/test_generation_worker_module.py` 对 `GenerationWorker._process_resume_task`(11) / `._requeue_single_task`(6) / `._cleanup_video_staging`(3) 的 20 处 — 把 worker 自己的处理步骤换掉后测 worker 的调度
- `tests/agent_runtime/test_agent_startup_error.py:127` `monkeypatch.setattr(SessionManager, "_build_options", ...)`；同文件对 `_ensure_capacity` 的 5 处（`:131` `:179` `:194` 等）
- `tests/test_custom_providers_api.py` 对 `server.routers.custom_providers._invalidate_caches` 的 9 处
- `tests/lib/test_script_generator_reference_branch.py` 对 `ScriptGenerator._resolve_supported_durations` 的 5 处 — 该函数是纯计算（caps → registry 两级解析后按联动约束收窄）
- `tests/test_project_migration_v7_v8.py` 对 `lib.artifact_activation._commit_schema_version` 的 3 处（该符号共 4 处）— 注入迁移中断

## 附带观察

**私有符号被 patch 最集中的 3 个生产模块**：`server.agent_runtime.sdk_tools.text_generation`（39 处）、`lib.video_backends.agnes`（25 处）、`lib.generation_worker`（25 处）。紧随其后：`lib.video_backends.kling` 19、`lib.config.anthropic_probe` 18、`server.routers.custom_providers` 18、`lib.video_backends.newapi` 17。前 7 个模块合计 161 处，占 315 的 51%——问题集中在少数模块，不是全仓库弥散。

**改成注入式 seam 能消解多少处**。把 319 处按「一个共享注入点能覆盖多少」归堆：

| 若引入的 seam | 可消解 | 覆盖的目标 |
| --- | ---: | --- |
| 轮询时钟/间隔按构造参数注入（或统一复用 `tests/fakes.py::bounded_poll_clock` 式假表） | 90 | 5 个 backend 模块的 `_POLL_*` 常量 + `lib.retry._compute_wait` |
| 能力解析器（`ConfigResolver` 系）按参数注入 | 67 | `_fetch_caps_with_fallback` 26、`resolve_video_caps` 12、`_i2i_provider_available` 11、`_fetch_reference_caps_with_fallback` 8、`_resolve_video_capabilities` 5、`ScriptGenerator._fetch_video_capabilities` 5 |
| HTTP 探测客户端按参数注入 | 31 | `anthropic_probe._post`/`._get` 18、`_get_latest_release` 5、`_run_discover` 4、`_test_openai`/`._test_google` 4 |
| 文件系统/子进程访问按参数注入 | 36 | `_O_NOFOLLOW` 7、`_read_app_version` 7、`_DOCKERENV_PATH`/`_CGROUP_PATH` 6、ffprobe 6、`ProjectManager._read/_write_script_unlocked` 4、其余 6 |
| **合计** | **224（70%）** | |

剩余 95 处 = 84 处「绕被测逻辑本身」+ 11 处零散 I/O（`server.auth._verify_api_key` 4、`lib.text_backends.openai._instructor_fallback` 3、`lib.image_backends.registry._BACKEND_FACTORIES` 2 等）。那 84 处不是 seam 能消解的——它们要么该改成对真实行为的断言，要么该拆出可独立测试的单元。

**已有共享 seam 未被收编**。`tests/fakes.py::bounded_poll_clock` 已经把 `lib.video_backends.base` 的 `asyncio.sleep` + `time.monotonic` 换成假表，但 90 处轮询常量 patch 仍在各 backend 测试里手写。这是「seam 已存在但未收编」而非「无 seam 可用」。

**47 个 test 函数一条断言都没有**（既无实质断言也无替身断言）。这类是「跑通不炸即通过」的冒烟形态，不属于本次两类口径，但同属判据讨论的相邻议题，规模已一并给出（`totals.no_assertion_tests`）；如需逐条清单可扩展脚本输出。

**类 1 与类 2 交集很小：122 条里只有 8 条同时命中类 2**（如 `tests/test_kling_video_backend.py` 的 3 条、`tests/test_auth_api_key.py:120`、`tests/test_resume_executor.py:719`）。两类基本各自独立：类 1 用例 patch 的多是第三方 SDK 构造函数或 `httpx`，不落在 `lib.` / `server.` 私有符号集内；而类 2 的 315 处大多出现在有实质断言的用例里。整改时两条线可以分开推进，不必互等。

需要注意的一个盲区：`patch.object(mgr, "_evict_one")` 这类以**局部变量**为宿主的私有符号 patch（`tests/test_session_lifecycle.py` 里成片存在），静态解析拿不到 `mgr` 的类型，因此不计入类 2 的 315 处。类 2 的真实规模同样只会大于报告数字。

## 附录：脚本用法

`scripts/audit_test_doubles.py`，零第三方依赖，只用标准库 `ast`。

```bash
# 打印汇总（Top 30 榜单）
uv run python scripts/audit_test_doubles.py

# 导出完整明细（含每条类 1 用例的 path/line/func/marks/evidence/subjects，
# 以及每处类 2 patch 站点的 target/kind/module/symbol_origin/motive）
uv run python scripts/audit_test_doubles.py --json /tmp/audit.json

# 调整榜单长度 / 扫描别的目录
uv run python scripts/audit_test_doubles.py --top 50
uv run python scripts/audit_test_doubles.py --root . --tests tests
```

JSON 关键字段：

- `totals` — 摘要表全部数字
- `double_only_cases` — 类 1 逐条（`subjects` 是被断言的替身主体，用于分层）
- `private_patch_sites` / `integration_self_patch_sites` / `integration_collaborator_patch_sites` — 类 2 逐处
- `motive_counts` / `motive_counts_private_only` / `motive_counts_integration_self` / `motive_samples` — 动机分档与样本
- `private_modules_top` — 私有符号最集中的生产模块

整改批次复跑时，`totals` 里 `double_only_tests`、`private_patch_sites`、`integration_self_public_patch_sites` 三个数字即进度指标。调整动机归档请改脚本里的 `MOTIVE_OVERRIDES` 表，并在注释里写明判定依据。
