# 已知问题

多供应商视频生成接入（#98）过程中发现的存量技术债，不影响功能正确性，记录以便后续迭代。

---

## 1. UsageRepository 费用路由逻辑泄漏

**位置：** `lib/db/repositories/usage_repo.py` — `finish_call()`

**现状：** `finish_call()` 内部用 `if/elif` 按 `provider + call_type` 组合手动路由到不同的 `CostCalculator` 方法。Gemini video 分支靠"落到末尾 elif"隐式匹配，没有显式判断 `PROVIDER_GEMINI`。每新增一个供应商都要在 Repository 中加分支。

**风险：** 下一个供应商接入时容易漏改，Gemini 的隐式匹配可能误捕获新供应商。

**建议：** 在 `CostCalculator` 增加统一入口 `calculate_video_cost_by_provider(provider, ...)` → `(amount, currency)`，Repository 只调这一个方法。

---

## 2. CostCalculator 费用结构不对称

**位置：** `lib/cost_calculator.py`

**现状：** 三种供应商的费用字典结构完全不同：
- Gemini：`{(resolution, generate_audio): cost_per_second}`
- Seedance：`{(service_tier, generate_audio): price_per_million_tokens}`
- Grok：`{model: cost_per_second}`

**风险：** 无法统一迭代、测试或生成费率报表。

**建议：** 引入统一的费率数据结构，各供应商的计费差异通过策略方法封装，而非结构差异。可与问题 1 一起重构。

---

## 3. VideoGenerationRequest 参数膨胀

**位置：** `lib/video_backends/base.py` — `VideoGenerationRequest`

**现状：** 共享 dataclass 中混入了后端特有字段（`negative_prompt` 为 Veo 特有，`service_tier`/`seed` 为 Seedance 特有），靠注释"各 Backend 忽略不支持的字段"约定。

**风险：** 每新增一个供应商就可能增加新的特有字段，污染所有后端的接口。

**建议：** 将特有配置拆分为 per-backend config dataclass（如 `VeoConfig`、`SeedanceConfig`），通过 `VideoGenerationRequest.backend_config: Any` 透传。

---

## 4. SystemConfigManager secret 块重复模式

**位置：** `lib/system_config.py` — `_apply_to_env()`

**现状：** `gemini_api_key`、`anthropic_api_key`、`ark_api_key`、`xai_api_key` 等 secret 块各自有 ~4 行完全相同的 `if key in overrides: set_env / else: restore_or_unset` 代码，总计 ~8 个块。

**风险：** 纯可维护性问题，新增供应商时继续复制粘贴。

**建议：** 将 secret 映射统一进一个元组，与下方 `for override_key, env_key, cast in (...)` 循环写法对齐：
```python
for override_key, env_key in (
    ("gemini_api_key", "GEMINI_API_KEY"),
    ("ark_api_key", "ARK_API_KEY"),
    ("xai_api_key", "XAI_API_KEY"),
    ...
):
```

---

## 5. UsageRepository finish_call 双次 DB 往返

**位置：** `lib/db/repositories/usage_repo.py` — `finish_call()`

**现状：** 先 `SELECT` 读取整行（取 `provider`、`call_type` 等字段计算费用），再 `UPDATE` 写回结果。对每个任务两次串行数据库往返。

**风险：** 视频生成耗时分钟级，相比之下 DB 往返影响较小，但仍是可消除的冗余。

**建议：** 让调用方在 `finish_call` 时传入已知的计费字段（`provider`、`call_type`、`duration_seconds` 等），合并为一次纯 `UPDATE`。

---

## 6. UsageRepository.finish_call() 参数膨胀

**位置：** `lib/db/repositories/usage_repo.py` — `finish_call()`，`lib/usage_tracker.py` — `finish_call()`

**现状：** `finish_call()` 已有 9 个 keyword 参数（`status`、`output_path`、`error_message`、`retry_count`、`usage_tokens`、`service_tier`、`generate_audio`、`input_tokens`、`output_tokens`），且 `UsageTracker.finish_call()` 1:1 镜像透传。每新增 call_type 都可能增加新的类型特有参数。

**风险：** 参数列表持续膨胀，调用方需要了解所有其他 call_type 的参数才能正确调用。

**建议：** 引入 per-call-type 的 TypedDict（如 `TextFinishParams`、`VideoFinishParams`），或使用一个 `extra: dict` bag 传递类型特有字段。

---

## 7. call_type 裸字符串缺乏类型约束

**位置：** 后端 `usage_repo.py`、`text_generator.py`、`media_generator.py`；前端 `UsageDrawer.tsx`、`UsageStatsSection.tsx`

**现状：** `"image"`、`"video"`、`"text"` 作为裸字符串分散在后端和前端代码中，没有统一的枚举或 Literal 类型。拼写错误（如 `"texts"`）不会被静态检查捕获。

**风险：** 随着 call_type 增多，漏改或拼写错误的概率上升。

**建议：** Python 端定义 `CallType = Literal["image", "video", "text"]` 类型别名，前端定义对应的 union type，在接口签名中使用。

---

## 8. UsageRepository 查询方法 filter 构建重复

**位置：** `lib/db/repositories/usage_repo.py` — `get_stats()`、`get_stats_grouped_by_provider()`、`get_calls()`

**现状：** 三个方法各自内联构建相同的 date/project/provider 过滤条件（约 10 行重复逻辑）。`get_stats()` 有一个 `_base_filters()` 本地函数，但未被其他方法共享。

**风险：** 新增过滤条件时需要三处同步修改。

**建议：** 将 `_base_filters()` 提升为类级别的私有方法，三个查询方法共享。

---

## 9. test_text_backends 测试文件 asyncio.to_thread patch 重复

**位置：** `tests/test_text_backends/test_ark.py`、`tests/test_text_backends/test_grok.py`

**现状：** 多个测试文件各自内联 `patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw))`，ark 出现 5 次，grok 出现 2 次。`tests/test_text_backends/` 下不存在 `conftest.py` 共享 fixture。

**风险：** 纯可维护性问题，新增后端测试时继续复制粘贴。

**建议：** 在 `tests/test_text_backends/conftest.py` 中提取 `sync_to_thread` fixture，各测试文件共享。
