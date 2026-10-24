# higgsfield

Higgsfield 图片与视频媒体 provider（聚合 API，一个模型一组固定端点）。

- 总入口：[Higgsfield API 文档](https://docs.higgsfield.ai/docs/index.md)、[Quickstart](https://docs.higgsfield.ai/docs/quickstart.md)
- 鉴权与请求头：[Authentication](https://docs.higgsfield.ai/docs/authentication.md)（`Authorization: Key <key-id>:<key-secret>`）、[Headers](https://docs.higgsfield.ai/docs/api-reference/headers.md)（`Idempotency-Key`、`Hf-Expires-After`）
- 请求生命周期与轮询：[Requests and lifecycle](https://docs.higgsfield.ai/docs/concepts/requests.md)、[Polling](https://docs.higgsfield.ai/docs/concepts/polling.md)、[Get request status](https://docs.higgsfield.ai/docs/api-reference/requests/get-request-status.md)
- 素材上传（素材字段只收公开 URL）：[File uploads](https://docs.higgsfield.ai/docs/concepts/file-uploads.md)
- 错误与重试：[Errors and retries](https://docs.higgsfield.ai/docs/concepts/errors.md)、[Rate limits](https://docs.higgsfield.ai/docs/concepts/rate-limits.md)
- 模型端点与参数：[Soul 2](https://docs.higgsfield.ai/docs/models/soul-2/generate.md)、[Qwen Image 3 文生图](https://docs.higgsfield.ai/docs/models/qwen-image-3/text-to-image.md)、[Qwen Image 3 编辑](https://docs.higgsfield.ai/docs/models/qwen-image-3/edit.md)、[Kling 3.0](https://docs.higgsfield.ai/docs/models/kling-3.md)（std / pro / 4k 各有 text-to-video 与 image-to-video）、[Kling O3](https://docs.higgsfield.ai/docs/models/kling-o3.md)（first-last-frame、image-reference）、[Seedance 2.5](https://docs.higgsfield.ai/docs/models/seedance-2-5.md)（text-to-video、image-to-video、reference-to-video）
- 计费：[Billing and retention](https://docs.higgsfield.ai/docs/concepts/billing-and-retention.md)（成功请求按账户余额扣费，失败与 nsfw 不扣费；文档不公开单价，以控制台为准）；出厂快照条目的价目表仍是各模型厂商主站价
- 代码：`lib/config/registry.py::PROVIDER_REGISTRY["higgsfield"]`、`lib/backends/higgsfield_shared.py`、`lib/backends/image_backends/higgsfield.py::HiggsfieldImageBackend`、`lib/backends/video_backends/higgsfield.py::HiggsfieldVideoBackend`（计量：`HiggsfieldVideoBackend.metered_usage`）
