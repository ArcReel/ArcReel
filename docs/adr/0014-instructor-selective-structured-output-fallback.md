---
status: accepted
---

# 结构化输出：Instructor 作为选择性降级路径，而非统一入口

原生支持 `structured_output` 的模型走原生 `response_format` 更快更准；仅对不支持的模型（及未注册模型，保守降级）走 Instructor（prompt 注入 schema + 解析 + 校验重试）。决定不让所有 backend 统一走 Instructor，降级逻辑收在 `lib/text_backends/instructor_support.py` 的纯函数、对上层透明——原生路径再套一层只会更慢、更不准；PydanticAI / BAML 等备选过重或 DSL 不兼容。

## Consequences

- 多一个第三方依赖，且能力判断要查 registry（按模型 capabilities 门控，未注册模型不加 STRUCTURED_OUTPUT、宁可降级也不调会报错的原生 API）。
- 无原生结构化输出的模型也能产出结构化结果，且不破坏原生路径（与 `docs/adr/0013`「能力声明在模型级」配套）。
