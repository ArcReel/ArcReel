# dashscope-vidu-image

- 协议：[Vidu 图像生成 API](https://help.aliyun.com/zh/model-studio/vidu-image-generation-api-reference)；本端点：北京地域业务空间域名，异步 `POST /api/v1/services/aigc/image-generation/generation`（须带 `X-DashScope-Async: enable`）+ `GET /api/v1/tasks/{task_id}`，支持文生图、参考生图与图片编辑（最多 14 张参考图）、`parameters.resolution`（1K/2K/4K）与 `parameters.aspect_ratio`
- 计费：[viduq3.1-fast_reference2image 原价](https://help.aliyun.com/zh/model-studio/viduq3-1-fast-reference2image)
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["dashscope-vidu-image"]`、`lib/backends/image_backends/dashscope.py::DashScopeViduImageBackend`
