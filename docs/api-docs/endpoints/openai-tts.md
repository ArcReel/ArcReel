# openai-tts

- 协议：[OpenAI text to speech](https://developers.openai.com/api/docs/guides/text-to-speech)
- 模型与快照：[GPT-4o mini TTS](https://developers.openai.com/api/docs/models/gpt-4o-mini-tts)；音色候选按型号名查表，见 `lib/backends/audio_backends/openai.py::_VOICES_BY_MODEL`
- 计费：[API pricing](https://developers.openai.com/api/docs/pricing)
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["openai-tts"]`、`lib/backends/audio_backends/openai.py::OpenAIAudioBackend`
