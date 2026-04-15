## ADDED Requirements

### Requirement: 百炼供应商配置字段声明
系统 MUST 将 `api_key` 声明为 `bailian` 供应商的必填配置字段，并将 `base_url`、`image_max_workers`、`video_max_workers` 声明为可选字段。系统 MUST 将 `api_key` 识别为敏感字段。

#### Scenario: 配置元数据包含必填与可选字段
- **WHEN** 系统读取 `bailian` 的 provider meta
- **THEN** 必填字段 MUST 包含 `api_key`
- **AND** 可选字段 MUST 包含 `base_url`、`image_max_workers`、`video_max_workers`
- **AND** 敏感字段 MUST 包含 `api_key`

### Requirement: 百炼供应商在配置界面可见
系统 MUST 在现有供应商配置列表中展示 `bailian`，并显示对应的名称、描述与模型能力，使用户可以通过现有设置页面完成配置。

#### Scenario: 配置页面展示百炼供应商
- **WHEN** 用户打开供应商配置页面
- **THEN** 页面 MUST 显示 `bailian` 供应商选项
- **AND** 页面 MUST 显示百炼的名称与描述信息

### Requirement: 百炼共享文件上传工具支持本地资源转临时 OSS URL
系统 MUST 提供共享工具，将本地文件上传到百炼临时 OSS 存储并返回 `oss://` URL，以支持图像和视频接口引用本地资源。该工具 MUST 先获取上传凭证，再执行文件上传。

#### Scenario: 本地文件成功上传并返回 OSS URL
- **WHEN** 图像或视频后端请求上传本地文件到百炼临时存储
- **THEN** 系统 MUST 先调用上传凭证接口获取 policy
- **AND** 系统 MUST 使用返回的凭证上传文件到 OSS
- **AND** 系统 MUST 返回可供后续接口引用的 `oss://` URL

### Requirement: 使用 OSS URL 时启用资源解析头
系统 MUST 在后续请求引用 `oss://` 资源时附加百炼要求的 `X-DashScope-OssResourceResolve: enable` 请求头，以确保远端服务正确解析临时 OSS 资源。

#### Scenario: 后端请求引用 OSS 资源
- **WHEN** 图像或视频生成请求中包含 `oss://` 资源 URL
- **THEN** 系统 MUST 在对应 HTTP 请求头中附加 `X-DashScope-OssResourceResolve: enable`
