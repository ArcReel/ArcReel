# 已知问题

多供应商视频生成接入（#98）过程中发现的存量技术债，不影响功能正确性，记录以便后续迭代。

---

## ~~1. UsageRepository 费用路由逻辑泄漏~~ ✅ 已修复

**修复：** `CostCalculator.calculate_cost()` 统一入口按 `(call_type, provider)` 显式路由，Repository 只调一次。Gemini video 不再隐式 fallthrough。

---

## ~~2. CostCalculator 费用结构不对称~~ ✅ 已修复

**修复：** 随 Issue 1 一并解决。`calculate_cost()` 统一入口隐藏了各供应商的费率字典结构差异。

---

## 3. VideoGenerationRequest 参数膨胀

**位置：** `lib/video_backends/base.py` — `VideoGenerationRequest`

**现状：** 共享 dataclass 中混入了后端特有字段（`negative_prompt` 为 Veo 特有，`service_tier`/`seed` 为 Seedance 特有），靠注释"各 Backend 忽略不支持的字段"约定。

**评估：** 仅 3 个后端 3 个特有字段，引入 per-backend config 类的复杂度不值得。待第 4 个后端接入时再重构。

---

## ~~4. SystemConfigManager secret 块重复模式~~ ✅ 已修复

**修复：** 将 `_apply_to_env()` 中 ~8 个相同模式的 if/else secret 块替换为元组 + 循环。

---

## 5. UsageRepository finish_call 双次 DB 往返

**位置：** `lib/db/repositories/usage_repo.py` — `finish_call()`

**现状：** 先 `SELECT` 读取整行（取 `provider`、`call_type` 等字段计算费用），再 `UPDATE` 写回结果。对每个任务两次串行数据库往返。

**评估：** 视频生成耗时分钟级，DB 往返影响极小。消除需改动 3 个调用方（MediaGenerator、TextGenerator、UsageTracker），风险不对称。

---

## 6. UsageRepository.finish_call() 参数膨胀

**位置：** `lib/db/repositories/usage_repo.py` — `finish_call()`，`lib/usage_tracker.py` — `finish_call()`

**现状：** `finish_call()` 已有 9 个 keyword 参数，且 `UsageTracker.finish_call()` 1:1 镜像透传。

**评估：** 与 Issue 5 耦合，单独改收益低。待 Issue 5 一并重构。

---

## ~~7. call_type 裸字符串缺乏类型约束~~ ✅ 已修复

**修复：** Python 端定义 `CallType = Literal["image", "video", "text"]`（`lib/providers.py`），前端定义对应 `CallType` 类型（`frontend/src/types/provider.ts`），在接口签名中统一使用。

---

## ~~8. UsageRepository 查询方法 filter 构建重复~~ ✅ 已修复

**修复：** 将 `_base_filters()` 提升为类方法 `_build_filters()`，三个查询方法共享。

---

## ~~9. update_project 后端字段缺少 provider 合法性校验~~ ✅ 已修复

**修复：** 提取共享校验函数 `validate_backend_value()`（`server/routers/_validators.py`），`update_project()` 和 `patch_system_config()` 共同使用，拒绝非法 provider/model 值并返回 400。

---

## ~~10. test_text_backends 测试文件 asyncio.to_thread patch 重复~~ ✅ 已修复

**修复：** 在 `tests/test_text_backends/conftest.py` 中提取 `sync_to_thread` fixture，各测试文件共享。

---

## 11. 剧本生成任务对模型输出 token 上限有强约束

**位置：** `lib/script_generator.py`、`lib/text_backends/`

**现状：** 大型 JSON 剧本（22+ 场景）约需 14K–16K 输出 token。`TextGenerationRequest.max_output_tokens` 已支持并在 `SCRIPT_MAX_OUTPUT_TOKENS = 32000` 处显式传入，但各模型的**硬上限**仍会截断：

- `doubao-seed-1-8-251228`：输出硬上限 ~8192，不满足剧本生成需求
- `gemini-3-flash-preview` / `gemini-2.5-pro`：默认上限足够（≥32K）
- `gpt-5.4` 系列：默认上限足够
- `doubao-seed-2.x` 系列：输出上限较高（依模型）

**建议：** 在 `/settings` 为 SCRIPT 任务配置**输出上限 ≥16K 的模型**。若必须使用 doubao-seed-1-8，则需将场景数控制在 15 个以内以规避截断。

**后续增强（未做）：** 可在 `lib/config/registry.py` 的 `PROVIDER_REGISTRY` 为每个模型声明 `max_output_tokens` 能力字段，运行时按 `min(request, model_limit)` clamp 并 `logger.warning`，在 UI 选择模型时给予提示。
