# Per-Model Output Token Limits

ArcReel 不维护「按模型的输出 token 上限表」，也不在运行期把请求的 `max_tokens` clamp 到该表的值。

## Why this is out of scope

请求示例（PR #405）：为 `TextGenerator` 增加各模型 `max_output_tokens` 常量表（如 `doubao-seed-1-8-251228 → 8192`），生成前取 `min(requested, model_limit)`，超出时静默分块。

ADR 0044（`docs/adr/0044-text-output-token-nonbinding-ceiling.md`）已选定另一条路：`lib/text_backends/base.py` 的 `DEFAULT_MAX_OUTPUT_TOKENS = 64000` 作为单一**非约束**安全阀，各 backend 在结构化输出被截断时通过 `check_truncation()` 抛 `TextOutputTruncatedError`，由用户显式换输出能力更高的模型；自由文本维持 log-only 告警。ADR「明确不采用」中逐条否掉了：

- **按模型上限表**：要为所有内置与自定义供应商补外部数据并持续维护，是一个易过时的第二真相源。#1847 已给出实例——PR #405 写死的 `8192` 在其提交后被火山方舟官方文档改为 64K，合入即把该模型正常长输出砍掉 8 倍。
- **截断即自适应降级**：静默缩批量与「显式失败、用户做主」相悖。

`lib/config/registry.py::ModelInfo` 至今没有 `max_output_tokens` 字段，这是刻意的。

重开条件（来自 #1847 关闭意见）：某个当前可选模型出现**真实、可复现**的不兼容，此时按具体 provider/model 单独开票，而非引入通用表。

## Prior requests

- #1847 — Spec「按模型输出 token 上限」，2026-08-13 按 YAGNI 关闭
- PR #405 — feat(ai): add token limit awareness to text generator
