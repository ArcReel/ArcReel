# ark-agent-plan

火山方舟 `/api/plan/v3` 媒体 provider。该 id 也出现在独立的 Agent 凭证 preset 命名空间；本页只描述 `PROVIDER_REGISTRY` 身份。

- 总入口：[Agent/Coding Plan API 参考资源](https://www.volcengine.com/docs/82379/1326340?lang=zh)
- 接口与能力：[Agent Plan 图片生成 API](https://www.volcengine.com/docs/82379/1666945?lang=zh)、总入口中的文本与视频 API 资源
- 套餐入口：[火山方舟 Agent Plan](https://www.volcengine.com/product/ark)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["ark-agent-plan"]`、`lib/text_backends/ark.py::ArkTextBackend`、`lib/image_backends/ark.py::ArkImageBackend`、`lib/video_backends/ark.py::ArkVideoBackend`
