---
status: accepted
---

# 自定义供应商只使用 API Key 与服务地址

自定义供应商的连接由 `api_key` 和 `base_url` 表达。无需鉴权的服务可以将 `api_key` 留空。原生多字段凭证由内置供应商声明并处理，例如可灵的 Access Key 与 Secret Key，或 Vertex AI 的服务账号。中转服务可以在自己的服务端处理原生鉴权，对 ArcReel 暴露单密钥接口。

多字段凭证扩展到自定义供应商，会同时改变存储、脱敏与设置表单。把多项值拼进 `api_key` 也会绕开这些字段的独立校验与脱敏。现行边界由 [自定义供应商模型](../../lib/db/models/custom_provider.py)和[内置供应商注册表](../../lib/config/registry.py)承载，内置凭证的存储取舍见 [ADR 0037](0037-builtin-provider-multi-secret-typed-columns.md)。ComfyUI 反向代理可通过端点定义选择凭证放在请求头还是查询参数中，沿用同一连接模型。见 [ADR 0081](0081-comfyui-endpoint-as-second-custom-endpoint-kind.md)。
