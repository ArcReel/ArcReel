# ark-agent-plan-tts

只在内置 `ark-agent-plan` 供应商凭证下可用，登记在 `PROVIDER_ONLY_ENDPOINTS`，不开放给自定义供应商。请求使用 Agent Plan 的专属 API Key，资源 ID 固定为 `seed-tts-2.0`。

- 套餐接入方式、接口地址与鉴权头：[Agent Plan 语音模型](https://console.volcengine.com/ark/region:cn-beijing/docs/agent-plan-personal-voice-model)
- 请求字段、语速取值、HTTP Chunked 响应对象、结束码与错误码：[HTTP Chunked/SSE 单向流式 V3](https://docs.volcengine.com/docs/6561/1598757)（Markdown 版：[http-chunked-sse-unidirectional-streaming-v3.md](https://docs.volcengine.com/docs/zh/doubao-voice/http-chunked-sse-unidirectional-streaming-v3.md)）
- 音色列表（「豆包语音合成模型 2.0」一节）：[音色列表](https://docs.volcengine.com/docs/6561/1257544)
- 计费字符的计数规则与按量价格（目录价目表的依据，不是套餐内调用的扣减规则）：[计费说明](https://docs.volcengine.com/docs/6561/1359370)
- 套餐额度、语音模型的日限额与月限额、超额后付费：[套餐概览](https://docs.volcengine.com/docs/ark/agent-plan-personal-plan-overview?lang=zh)、[超额后付费管理](https://docs.volcengine.com/docs/ark/agent-plan-personal-over-quota-postpaid-management?lang=zh)
- 代码：`lib/backends/backend_assembly/specs.py::PROVIDER_ONLY_ENDPOINTS["ark-agent-plan-tts"]`、`lib/backends/audio_backends/ark_agent_plan.py::ArkAgentPlanAudioBackend`
