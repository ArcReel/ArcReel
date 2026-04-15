## Why

ArcReel 目前仅支持国际供应商（OpenAI、Google、Anthropic 等），在国内网络环境下访问受限，且缺乏对国产大模型的支持。阿里云百炼（DashScope）提供通义千问文本模型、万相图像生成和视频生成能力，适合国内用户使用，能够显著提升国内用户的使用体验和服务稳定性。

## What Changes

- 新增阿里云百炼供应商支持，包含文本、图像、视频三种媒体类型
- 实现 Qwen 3.6 Plus 系列文本生成后端（基于 OpenAI 兼容模式）
- 实现 Wan 2.7 Image 系列图像生成后端（异步任务模式）
- 实现 Wan 2.7 Video 系列视频生成后端（T2V、I2V、R2V、VideoEdit）
- 在供应商注册表中添加百炼供应商元数据和模型配置
- 添加中英文国际化翻译
- 参考 [docs/bailian-provider-integration.md](../docs/bailian-provider-integration.md) 文档

## Capabilities

### New Capabilities

- `bailian-text-generation`: 基于通义千问模型的文本生成能力，支持 structured output 和 vision
- `bailian-image-generation`: 基于万相模型的图像生成能力，支持文生图和图生图
- `bailian-video-generation`: 基于万相模型的视频生成能力，支持文生视频、图生视频、参考视频生成和视频编辑
- `bailian-provider-config`: 百炼供应商的配置管理，包括 API Key、base_url、并发控制等

### Modified Capabilities

无。此变更不修改现有能力的需求，仅新增供应商支持。

## Impact

**新增文件**：
- `lib/providers.py` — 添加 `PROVIDER_BAILIAN` 常量
- `lib/text_backends/bailian.py` — 文本生成后端实现
- `lib/image_backends/bailian.py` — 图像生成后端实现
- `lib/video_backends/bailian.py` — 视频生成后端实现
- `lib/i18n/zh/providers.py` — 中文翻译
- `lib/i18n/en/providers.py` — 英文翻译

**修改文件**：
- `lib/config/registry.py` — 注册百炼供应商元数据和模型信息

**依赖**：
- 复用现有 `AsyncOpenAI` 客户端（文本生成）
- 复用现有 `httpx` 异步 HTTP 客户端（图像和视频生成）
- 复用现有重试机制（`lib/retry.py`）

**API 影响**：
- 前端配置页面将显示新的供应商选项
- 用户可在 `/settings` 页面配置百炼 API Key 和相关参数
