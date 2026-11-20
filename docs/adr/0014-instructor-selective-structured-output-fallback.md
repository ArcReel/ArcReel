---
status: accepted
---

# 结构化输出优先走原生参数，按通道选择降级方式

模型和调用通道支持原生结构化输出时，优先请求供应商按 schema 生成，并复验返回值。Pydantic 模型可复验 schema，字典 schema 只检查是否为合法 JSON。兼容网关可能接受请求却忽略 schema，复验失败时走降级。声明未提供原生能力时直接降级。原生调用触发降级时，已发生的调用用量一并计入。

OpenAI 兼容和方舟通道可用 Instructor 完成提示词约束、解析和校验。Gemini 的降级使用提示词表达 schema，以避开可能被网关忽略的原生结构化参数。原生路径的性能和精度仍可保留。能力来源见 [ADR 0104](0104-capability-sources-split-by-dimension.md)，重试范围见 [ADR 0047](0047-text-backend-retry-wraps-single-call-only.md)。
