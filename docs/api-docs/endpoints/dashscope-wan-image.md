# dashscope-wan-image

- 协议：[阿里云百炼图像生成](https://help.aliyun.com/zh/model-studio/image-generation)、[Qwen-Image API](https://help.aliyun.com/zh/model-studio/qwen-image-api)、[Qwen-Image-Edit API](https://help.aliyun.com/zh/model-studio/qwen-image-edit-guide)、[Wan 图像生成与编辑 API](https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference)；本端点：Wan 任意像素请求，带参考图时只用标准像素预算
- 计费：[模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["dashscope-wan-image"]`、`lib/backends/image_backends/dashscope.py::DashScopeWanImageBackend`
