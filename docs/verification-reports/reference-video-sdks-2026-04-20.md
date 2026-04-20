# 参考生视频 SDK 验证报告（2026-04-20）

## 环境

- 分支：`feature/reference-video-pr7-e2e-release`
- PR：PR7（M6 E2E + 发版）
- 脚本：`scripts/verify_reference_video_sdks.py`
- 日期：2026-04-20
- 运行者：PR7 E2E 任务中的 agent-driven validation（非人工）

## 运行概述

尝试对四家供应商（Ark / Grok / Gemini Veo / OpenAI Sora）运行：

```bash
uv run python scripts/verify_reference_video_sdks.py --provider <p> --refs N --duration D --report-dir docs/verification-reports
```

**结果：四家均无法完成真实调用**——当前环境没有为任何一家配置 API Key。脚本在 backend 构造阶段（`create_backend` → `resolve_*_api_key`）即抛出：

| 供应商 | 退出位置 | 错误消息（精确复现） |
|---|---|---|
| Ark | `lib/ark_shared.py:23` | `ValueError: Ark API Key 未提供。请在「全局设置 → 供应商」页面配置 API Key。` |
| Grok | `lib/grok_shared.py`（`resolve_xai_api_key`） | `ValueError: XAI_API_KEY 未设置\n请在系统配置页中配置 xAI API Key` |
| Gemini Veo | `lib/video_backends/gemini.py:81` | `ValueError: GEMINI_API_KEY 环境变量未设置` |
| OpenAI Sora | `openai/_client.py:587` | `openai.OpenAIError: The api_key client option must be set either by passing api_key to the client or by setting the OPENAI_API_KEY environment variable` |

这是 plan PR7 Task 14 预期的 degraded scenario（"如任一 key 缺失，不阻塞 PR7；只验证已配置的供应商，并在报告中标注'未验证—密钥未配置'"）。本报告即为该 fallback 产物。

## Doc-based 能力矩阵（from `lib/reference_video/limits.py` + 供应商文档）

`lib/reference_video/limits.py` 是 prompt 构建阶段（`lib/script_generator.py:_resolve_max_refs()`）与 executor 强制阶段（`server/services/reference_video_tasks.py:_PROVIDER_LIMITS`）共享的 **single source of truth**：

```python
PROVIDER_MAX_REFS:     {"gemini": 3, "openai": 1, "grok": 7, "ark": 9}
PROVIDER_MAX_DURATION: {"gemini": 8, "openai": 12, "grok": 15, "ark": 15}
DEFAULT_MAX_REFS = 9
```

| 供应商 | 模型（代表） | 最大 refs | 最大时长 | generate_audio | 备注 |
|---|---|---|---|---|---|
| Ark Seedance 2.0 | doubao-seedance-2-0-260128 | 9 | 15s | ✅ | 首推；multi-shot `Shot N (Xs):` 已文档化 |
| Ark Seedance 2.0 fast | doubao-seedance-2-0-fast-pro | 9 | 15s | ✅ | 快模式，能力与 2.0 对齐 |
| Grok | grok-imagine-video | 7 | 15s | ✅（默认） | 请求体大小实测 pending；`RequestPayloadTooLargeError` 二次压缩通路已就绪 |
| Gemini Veo | veo-3.0-generate-preview | 3 | 8s | ✅（Vertex） | executor 已硬 clamp |
| OpenAI Sora | sora | 1 | 12s | —（当前 executor 不传） | **spec §11 第 4 项决策取决于此**——未 live 验证前保守按 `max_refs=1` 走单图降级路径 |

> 具体数字以 `lib/reference_video/limits.py` 为 single source of truth。若本表与代码漂移，**修代码**，然后同步此报告。

## Live validation pending

当 API key 可用时，运行以下命令逐个验证，并把真实结果 append 到本文件：

```bash
# Ark Seedance 2.0
uv run python scripts/verify_reference_video_sdks.py --provider ark --refs 9 --duration 8 --multi-shot --report-dir docs/verification-reports

# Grok — 重点记录请求体大小（>8MB 时观察 gRPC/HTTP 错误）
uv run python scripts/verify_reference_video_sdks.py --provider grok --refs 7 --duration 6 --report-dir docs/verification-reports

# Gemini Veo — 3 图 8s
uv run python scripts/verify_reference_video_sdks.py --provider veo --refs 3 --duration 8 --report-dir docs/verification-reports

# OpenAI Sora — 重点：多图是否支持
uv run python scripts/verify_reference_video_sdks.py --provider sora --refs 3 --duration 8 --report-dir docs/verification-reports
uv run python scripts/verify_reference_video_sdks.py --provider sora --refs 1 --duration 8 --report-dir docs/verification-reports  # 对照
```

实际运行后，应在每次调用后记录：

- 实际 HTTP/gRPC 状态码与耗时
- refs/duration 是否被 provider 静默 clamp
- multi-shot 是否被正确解析（观察产物是否呈现多个 shot 段落）
- generate_audio 是否在输出视频中可感知
- 请求体大小（Grok）与 `RequestPayloadTooLargeError` 是否触发二次压缩（`long_edge=1024, q=70`）
- Sora 多图模式下的真实行为（完全拒绝 / 静默丢弃 / 正常支持），据此更新 spec §11 第 4 项决策

## 结论（截至报告日期）

- 代码层已将四家 provider 的能力上限抽象进 `lib/reference_video/limits.py`，executor 的 `_apply_provider_constraints` 会据此 clamp refs / duration 并回传 `warnings`。
- Doc-based 数值当前与 spec 附录 B 的表格一致。
- **Sora 参考模式决策（spec §11 第 4 项）保持"保守单图降级"**，等 live 验证后再决定是否完全隐藏或放宽到多图。`_apply_provider_constraints` 的 `ref_sora_single_ref` warning 通路为该决策的运行时兜底。
- 请求体过大场景的 `ref_payload_too_large` 二次压缩分支（`long_edge=1024, q=70`）已有单测覆盖，live 场景下是否触发 pending。
- 本报告不阻塞 PR7 合入；凭据就绪后应作为首要 follow-up，结果回填至本文件尾部。
