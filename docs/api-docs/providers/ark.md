# ark

火山方舟标准 `/api/v3` 媒体 provider。

- 总入口：[火山方舟文档](https://www.volcengine.com/docs/82379/?lang=zh)
- 接口与能力：[对话 Chat API](https://api.volcengine.com/api-docs/view?action=ChatCompletions&serviceCode=ark&version=2024-01-01)、[图片生成 API](https://www.volcengine.com/docs/82379/1666946?lang=zh)、[模型列表](https://www.volcengine.com/docs/82379/1330310?lang=zh)、[创建视频生成任务](https://www.volcengine.com/docs/82379/1520757?lang=zh)
- 计费：[火山方舟定价](https://www.volcengine.com/pricing?product=ark_bd&tab=1)、[模型价格](https://www.volcengine.com/docs/82379/1544106)中的视频 token 规则。预估与结算分别见 `lib/backends/video_backends/ark.py::ArkVideoBackend.estimate_video_tokens` 和 `lib/backends/video_backends/ark.py::ArkVideoBackend.metered_usage`。
- 下线：[模型下线公告](https://docs.volcengine.com/docs/ark/model-deprecation-notice?lang=zh)
- 简介来源（`lib/i18n/<locale>/providers.py` 的 `provider_desc_ark`，只写平台是什么，不列模型与能力）：[Wikipedia: 火山引擎](https://zh.wikipedia.org/wiki/火山引擎)、[火山方舟发布公告](https://www.volcengine.com/docs/6281/1110759)
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["ark"]`、`lib/backends/text_backends/ark.py::ArkTextBackend`、`lib/backends/image_backends/ark.py::ArkImageBackend`、`lib/backends/video_backends/ark.py::ArkVideoBackend`（计量：`ArkVideoBackend.metered_usage`）
- 素材库控制面：[ListAssets](https://docs.volcengine.com/docs/ark/list-assets-api?lang=zh)、[CreateAsset](https://docs.volcengine.com/docs/ark/create-asset-api?lang=zh)、[GetAsset](https://docs.volcengine.com/docs/ark/get-asset-api?lang=zh)、[CreateAssetGroup](https://docs.volcengine.com/docs/ark/create-asset-group-api?lang=zh)、[ListAssetGroups](https://docs.volcengine.com/docs/ark/list-asset-groups-api?lang=zh)、[签名方法](https://docs.volcengine.com/docs/6369/67269)。状态与错误处理实现位于 `lib/asset_library/control_plane.py`，云侧作用域见 `lib/asset_library/sites.py::SITES` 与 `lib/asset_library/bindings.py::resolve_bound_scope`。中转存储参考 [TOS Python SDK](https://www.volcengine.com/docs/6349/92785) 与 `lib/object_storage/client.py::ObjectStorageClient`。
- 素材引用：[Seedance 人像素材使用指南](https://docs.volcengine.com/docs/ark/seedance-portrait-asset-guide?lang=zh)中的请求示例。支持的引用角色与端点见 `lib/backends/video_backends/ark.py::SeedanceEndpoint`，推理凭证与链接选择见 `lib/asset_library/generation_inputs.py::choose_reference_image_assets` 和 [ADR 0113](../../adr/0113-generation-selects-provider-assets-by-actual-inference-credential.md)。
