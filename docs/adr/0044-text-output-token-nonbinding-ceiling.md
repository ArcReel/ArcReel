---
status: accepted
---

# 按模型输出上限规划批次，结构化截断明确报错

把文本输出上限当作省钱预算曾把正常的大批次结构化结果切断。文本生成采用 [模型输出上限与统一安全上限](../../lib/backends/text_generator.py) 中较低的值。缺少模型事实时使用统一安全上限。分集规划按已解析的输出上限自动决定批次大小，方案见 [ADR 0032](0032-episode-planning-server-side-batched.md)。模型事实与渠道覆盖的边界见 [ADR 0104](0104-capability-sources-split-by-dimension.md)。

结构化响应触顶时返回 `TextOutputTruncatedError`，指出当前模型并引导选择输出能力更高的模型。重发相同请求仍会触顶，截断错误直接结束结构校验重试。自由文本截断默认只记录告警并返回部分结果。调用方要求完整结果（`require_complete=True`）时也抛出 `TextOutputTruncatedError`。
