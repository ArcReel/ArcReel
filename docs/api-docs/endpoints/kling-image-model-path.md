# kling-image-model-path

适用于可灵新版 API（路径中包含模型名，仅支持 API Key 鉴权）的普通单图生成（文生图与多参考图生图，一次生成 1 张；不含多张独立图、关联组图与长图）。

- 协议：[API 更新公告](https://klingai.com/document-api/updates/api)中的 2026.11.17 更新条目；[Kling Image 4.0 图片生成](https://klingai.com/document-api/api/image/4-0/image-generation)中的创建任务（`POST /image-generation/kling-4.0`）与按任务 ID 查询任务（`GET /tasks?task_ids=`）；[Kling Image 4.0 使用手册](https://klingai.com/document-api/api/usage-guide/kling-image-4-0)（含旗舰版资源包或现金余额 > 0 的调用前提）
- 鉴权与域名：[接口鉴权（国内站）](https://klingai.com/document-api/api/get-started/authentication)（`https://api-beijing.klingai.com`）与[接口鉴权（国际站）](https://kling.ai/document-api/api/get-started/authentication)（`https://api-singapore.klingai.com`）中的 API Key 鉴权方式；Access Key / Secret Key 仅适用于旧版 API
- 能力：[图片能力地图](https://klingai.com/document-api/guides/capability-map/image)中的 Kling Image 4.0 单图生成约束（2K / 4K / 8K 分辨率、9 种比例含 `auto`、参考素材最多 10 个）
- 错误码：[错误码](https://klingai.com/document-api/api/get-started/error-codes)
- 计费：[图像计费（国内站 CNY）](https://klingai.com/document-api/pricing/base/image)与[图像计费（国际站 USD）](https://kling.ai/document-api/pricing/base/image)中的 Kling Image 4.0 单图计费条目（输入参考图按张计费 + 输出图按分辨率分档按张计费）
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["kling-image-model-path"]`、`lib/backends/image_backends/kling_model_path.py::KlingModelPathImageBackend`、`lib/backends/kling_shared.py::kling_model_path_image_urls`
