# dashscope-pixverse-video

- 协议：[PixVerse 文生视频 API](https://help.aliyun.com/zh/model-studio/pixverse-text-to-video-api-reference)、[PixVerse 图生视频（基于首帧）API](https://help.aliyun.com/zh/model-studio/pixverse-image-to-video-api-reference)；本端点：北京地域业务空间域名，异步 `POST /api/v1/services/aigc/video-generation/video-synthesis` + `GET /api/v1/tasks/{task_id}`，文生视频用 `parameters.aspect_ratio`、首帧图生视频用 `input.media[{type: "image_url"}]`，支持 360P/540P/720P/1080P、1–15 秒时长与 `parameters.audio` 音轨开关
- 计费：[pixverse-v6-max-t2v 原价](https://help.aliyun.com/zh/model-studio/pixverse-v6-max-t2v)、[pixverse-v6-max-it2v 原价](https://help.aliyun.com/zh/model-studio/pixverse-v6-max-it2v)
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["dashscope-pixverse-video"]`、`lib/backends/video_backends/dashscope.py::DashScopePixVerseVideoBackend`
