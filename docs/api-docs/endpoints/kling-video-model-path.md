# kling-video-model-path

可灵新版 API（模型名写在路径里，只收 API Key）的文生视频与图生视频。

- 协议：[API 更新公告](https://klingai.com/document-api/updates/api)中 2026.06.17、2026.07.15、2026.10.28、2026.11.04 各条；[Kling 4.0 文生视频](https://klingai.com/document-api/api/video/4-0/text-to-video)与[图生视频](https://klingai.com/document-api/api/video/4-0/image-to-video)、[Kling 4.0 Flash 文生视频](https://klingai.com/document-api/api/video/4-0-flash/text-to-video)与[图生视频](https://klingai.com/document-api/api/video/4-0-flash/image-to-video)、[Kling 3.0 Turbo 文生视频](https://klingai.com/document-api/api/video/3-0-turbo/text-to-video)与[图生视频](https://klingai.com/document-api/api/video/3-0-turbo/image-to-video)中的创建任务与按任务 ID 查询任务
- 鉴权与域名：[接口鉴权](https://klingai.com/document-api/api/get-started/authentication)中的调用域名与 API Key；Access Key / Secret Key 只适用于旧版 API
- 能力：[视频能力地图](https://klingai.com/document-api/guides/capability-map/video)中的首尾帧、音画同出；[Kling 4.0 视频使用手册](https://klingai.com/document-api/api/usage-guide/kling-4-0)中音画同步默认开启、没有开关的说明
- 错误码：[错误码](https://klingai.com/document-api/api/get-started/error-codes)
- 计费：[视频计费（国内站）](https://klingai.com/document-api/pricing/base/video)中 Kling 4.0、Kling 4.0 Flash、Kling 3.0 Turbo 各行；任务查询响应的 `usage.output_duration`
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["kling-video-model-path"]`、`lib/backends/video_backends/kling_model_path.py::KlingModelPathVideoBackend`、`lib/backends/kling_shared.py::kling_model_path_task`
