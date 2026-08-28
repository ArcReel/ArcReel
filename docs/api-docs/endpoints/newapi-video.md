# newapi-video

- 协议：[视频生成 API](https://doc.newapi.pro/api/generate-video/)、[可灵与即梦格式](https://doc.newapi.pro/api/kling-jimeng/)
- 计费：[模型计费说明](https://doc.newapi.pro/guide/pricing/)；实际价格由部署方配置
- 代码：`lib/custom_provider/builtin_endpoints/newapi-video.json`、`lib/custom_provider/declarative_backend.py::DeclarativeVideoBackend`
- 任务状态与回包形状：状态串随底层厂商透传，过共享归一 `lib/video_backends/base.py::normalize_provider_status`；状态、视频地址与 metadata 按定义 `poll.extract` 的路径表并集探测，兼容扁平与 `data` 包装两种回包
