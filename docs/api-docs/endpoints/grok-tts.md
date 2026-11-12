# grok-tts

只在内置 `grok` 供应商凭证下可用，登记在 `PROVIDER_ONLY_ENDPOINTS`，不开放给自定义供应商。

- 协议与语速、语言约束：[Text to Speech](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech)
- 字段与音色列表：[Voice REST reference](https://docs.x.ai/developers/rest-api-reference/inference/voice)（`POST /v1/tts`、`GET /v1/tts/voices`）
- 计费：[Pricing](https://docs.x.ai/developers/pricing)
- 代码：`lib/backends/backend_assembly/specs.py::PROVIDER_ONLY_ENDPOINTS["grok-tts"]`、`lib/backends/audio_backends/grok.py::GrokAudioBackend`
