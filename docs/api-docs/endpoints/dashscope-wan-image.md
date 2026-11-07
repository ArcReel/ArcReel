# dashscope-wan-image

- Protocol: [DashScope image generation](https://help.aliyun.com/zh/model-studio/image-generation), [Qwen-Image API](https://help.aliyun.com/zh/model-studio/qwen-image-api), [Qwen-Image-Edit API](https://help.aliyun.com/zh/model-studio/qwen-image-edit-guide), [Wan image generation and editing API](https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference); this endpoint uses Wan arbitrary-pixel requests; requests with reference images use only the standard pixel budget
- Pricing: [Model pricing](https://help.aliyun.com/zh/model-studio/model-pricing)
- Code: `lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["dashscope-wan-image"]`, `lib/backends/image_backends/dashscope.py::DashScopeWanImageBackend`
