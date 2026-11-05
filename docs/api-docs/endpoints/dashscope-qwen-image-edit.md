# dashscope-qwen-image-edit

- 协议：[阿里云百炼图像生成](https://help.aliyun.com/zh/model-studio/image-generation)、[Qwen-Image API](https://help.aliyun.com/zh/model-studio/qwen-image-api)、[Qwen-Image-Edit API](https://help.aliyun.com/zh/model-studio/qwen-image-edit-guide)、[Wan 图像生成与编辑 API](https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference)；本端点：Qwen 编辑请求，宽高均不超过 2048
- 计费：[模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["dashscope-qwen-image-edit"]`、`lib/backends/image_backends/dashscope.py::DashScopeQwenEditImageBackend`
