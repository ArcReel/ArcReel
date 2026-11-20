# dashscope-wan-image

- 协议：[阿里云百炼图像生成](https://help.aliyun.com/zh/model-studio/image-generation)、[Qwen-Image API](https://help.aliyun.com/zh/model-studio/qwen-image-api)、[Qwen-Image-Edit API](https://help.aliyun.com/zh/model-studio/qwen-image-edit-guide)、[Wan 图像生成与编辑 API](https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference)；本端点用于发起 Wan 任意像素图像请求，携带参考图时仅使用标准像素预算
- 计费：[模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["dashscope-wan-image"]`、`lib/backends/image_backends/dashscope.py::DashScopeWanImageBackend`
