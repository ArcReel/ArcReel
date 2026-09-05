# ark-agent-plan

火山方舟 `/api/plan/v3` 媒体 provider。该 id 也出现在独立的 Agent 凭证 preset 命名空间；本页只描述 `PROVIDER_REGISTRY` 身份。

- 总入口：[Agent/Coding Plan API 参考资源](https://www.volcengine.com/docs/82379/1326340?lang=zh)
- 接口与能力：[对话 Chat API](https://api.volcengine.com/api-docs/view?action=ChatCompletions&serviceCode=ark&version=2024-01-01)、[Agent Plan 图片生成 API](https://www.volcengine.com/docs/82379/1666945?lang=zh)、[创建视频生成任务](https://www.volcengine.com/docs/82379/1520757?lang=zh)
- 套餐入口：[火山方舟 Agent Plan](https://www.volcengine.com/product/ark)
- Agent 凭证 preset（Anthropic 协议，与本页 media provider 分属两个命名空间）：[Agent Plan 接入 Claude Code](https://www.volcengine.com/docs/82379/2373740?lang=zh)、[Agent Plan 支持模型及 Harness](https://www.volcengine.com/docs/82379/2366394?lang=zh)、[Coding Plan 模型配置](https://www.volcengine.com/docs/82379/1928262?lang=zh)；代码 `lib/agent_provider_catalog.py::PRESET_PROVIDERS["ark-agent-plan"]`、`["ark-coding-plan"]`
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["ark-agent-plan"]`、`lib/text_backends/ark.py::ArkTextBackend`、`lib/image_backends/ark.py::ArkImageBackend`、`lib/video_backends/ark.py::ArkVideoBackend`
