## 1. 供应商注册与共享能力

- [x] 1.1 在 `lib/providers.py` 中添加 `PROVIDER_BAILIAN` 常量
- [x] 1.2 在 `lib/config/registry.py` 中注册 `bailian` 供应商元数据、模型列表和能力声明
- [x] 1.3 新增 `lib/bailian_shared.py`，实现本地文件上传到临时 OSS 并返回 `oss://` URL 的共享工具
- [x] 1.4 在共享上传和媒体请求流程中支持 `X-DashScope-OssResourceResolve: enable` 请求头

## 2. 文本后端实现

- [x] 2.1 新增 `lib/text_backends/bailian.py`，基于 OpenAI 兼容接口实现百炼文本后端
- [x] 2.2 在百炼文本后端中支持默认 `base_url` 和自定义 `base_url` 配置
- [x] 2.3 在百炼文本后端中复用 structured output schema 错误降级逻辑
- [x] 2.4 在百炼文本后端中支持视觉输入消息构造与结果解析

## 3. 图像后端实现

- [x] 3.1 新增 `lib/image_backends/bailian.py`，实现图像异步任务提交、轮询和结果下载
- [x] 3.2 在图像后端中实现 `aspect_ratio` 到百炼 `size` 参数的映射
- [x] 3.3 在图像后端中支持首版核心参数映射：prompt、生成数量、watermark 和参考图输入
- [x] 3.4 在图像后端中接入本地文件上传能力，以支持参考图转换为 `oss://` 资源

## 4. 视频后端实现

- [x] 4.1 新增 `lib/video_backends/bailian.py`，实现首版 `wan2.7-t2v` 和 `wan2.7-i2v` 的异步任务提交流程
- [x] 4.2 在视频后端中实现时长、输入类型和分辨率的本地校验
- [x] 4.3 在视频后端结果中保留 `task_id` 和 `request_id` 元数据
- [x] 4.4 在视频后端中接入本地文件上传能力，以支持首帧图像等本地资源输入

## 5. 国际化与集成验证

- [x] 5.1 在 `lib/i18n/zh/providers.py` 和 `lib/i18n/en/providers.py` 中添加百炼供应商名称与描述
- [x] 5.2 将百炼后端接入现有工厂/注册机制，确保文本、图像、视频能力可被系统实例化
- [x] 5.3 对修改的 Python 文件执行 `uv run ruff check` 并修复 error 级别问题
- [x] 5.4 对修改的 Python 文件执行 `uv run ruff format`，并验证百炼供应商配置与基础生成流程可正常工作
