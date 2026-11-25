---
status: accepted
---

# 结构化输出优先走原生参数，统一经提示词 JSON 降级

模型和调用通道声明支持原生结构化输出时，统一文本执行运行时优先请求供应商按原生 schema 参数生成，并在清洗开头的 `<think>` 思考块与外层 Markdown JSON 代码围栏后复验返回值：Pydantic 模型按模型校验，字典 schema 只检查是否为合法 JSON。能力声明由上层装配（[文本运行时](../../lib/video_runtime/text.py)）经能力快照向下注入，协议驱动不再自行回查目录。

兼容网关可能拒收原生结构化参数、把 schema 错误包装成其他状态码，或返回 HTTP 200 却静默忽略 schema。声明无原生结构化能力、原生参数被拒或复验不通过时，全部文本协议统一走基于 Pydantic 的 `prompt_json_fallback`（将 JSON Schema 写入提示词、剥离代码围栏并校验，不通过时带字段路径摘要反馈重试一次），不再保留 `instructor` 依赖及同步/异步双份的 `TOOLS → MD_JSON` 降级链。

移除 `instructor` 是因为现有五家官方 SDK 均已原生支持结构化输出与异步流式，而 `instructor` 的流式接口与完整用量回传、校验重试互斥，且会把内部重试藏在黑盒中与外层叠乘。原生调用与降级链上每次实际完成的供应商物理请求均保留各自计量，无论最终成功还是降级失败都交给账本按已知计量结算（见 [ADR 0076](0076-usage-record-is-projection-of-provider-calls.md) 与 [ADR 0100](0100-price-table-meters-and-dimensions.md)）。能力来源见 [ADR 0104](0104-capability-sources-split-by-dimension.md)，重试与超时边界见 [ADR 0047](0047-text-backend-retry-wraps-single-call-only.md)。
