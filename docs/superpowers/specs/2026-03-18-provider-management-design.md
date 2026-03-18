# 供应商管理页设计

> Issue: [#102](https://github.com/ArcReel/ArcReel/issues/102)（属于 [#98](https://github.com/ArcReel/ArcReel/issues/98) 子任务）
> 日期: 2026-03-18

## 概述

随着多供应商（Gemini AI Studio、Gemini Vertex AI、Seedance、Grok）的接入，需要：

1. 将系统配置存储从 JSON 文件迁移到数据库
2. 重构全局设置页为侧边栏布局，新增供应商管理与用量统计
3. API 按职责分层，新增 `/api/v1/providers` 路由
4. 修复项目设置页路由，支持项目级模型覆盖

## 1. 数据模型

### 1.1 供应商注册表（静态，代码维护）

每个供应商的元数据在代码中定义，不存数据库：

```python
PROVIDER_REGISTRY = {
    "gemini-aistudio": ProviderMeta(
        display_name="Gemini AI Studio",
        media_types=["video", "image"],
        required_keys=["api_key"],
        optional_keys=["base_url"],
        secret_keys=["api_key"],
    ),
    "gemini-vertex": ProviderMeta(
        display_name="Gemini Vertex AI",
        media_types=["video", "image"],
        required_keys=["credentials_path"],
        optional_keys=["gcs_bucket"],
        secret_keys=[],
    ),
    "seedance": ProviderMeta(
        display_name="Seedance",
        media_types=["video"],
        required_keys=["api_key"],
        optional_keys=["file_service_base_url"],
        secret_keys=["api_key"],
    ),
    "grok": ProviderMeta(
        display_name="Grok",
        media_types=["video"],
        required_keys=["api_key"],
        optional_keys=[],
        secret_keys=["api_key"],
    ),
}
```

每个 `ProviderMeta` 还包含 `capabilities` 字段，静态定义该供应商支持的能力列表（如 `text_to_video`, `image_to_video`, `generate_audio` 等）。这些值直接对应 `VideoBackend.capabilities` / `ImageBackend` 的能力枚举，但在 registry 中静态维护，无需实例化后端即可获取。

```python
# capabilities 示例（包含在 ProviderMeta 中）
# gemini-aistudio: [text_to_video, image_to_video, text_to_image, negative_prompt, video_extend]
# gemini-vertex:   [text_to_video, image_to_video, text_to_image, generate_audio, negative_prompt, video_extend]
# seedance:        [text_to_video, image_to_video, generate_audio, seed_control, flex_tier]
# grok:            [text_to_video, image_to_video]
```

### 1.2 数据库表

**`provider_config` — 供应商配置**

| 列 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | 自增主键 |
| provider | VARCHAR(32) NOT NULL | 供应商标识 (gemini-aistudio, gemini-vertex, seedance, grok) |
| key | VARCHAR(64) NOT NULL | 配置键 (api_key, base_url, credentials_path, gcs_bucket, file_service_base_url) |
| value | TEXT NOT NULL | 配置值 |
| is_secret | BOOLEAN NOT NULL DEFAULT false | 是否为敏感字段，控制 GET 响应掩码 |
| updated_at | DATETIME NOT NULL | 更新时间 |

唯一约束: `UNIQUE(provider, key)`

**`system_setting` — 全局系统设置**

| 列 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | 自增主键 |
| key | VARCHAR(64) UNIQUE NOT NULL | 设置键 |
| value | TEXT NOT NULL | 设置值 |
| updated_at | DATETIME NOT NULL | 更新时间 |

system_setting 存储的键包括：
- `default_video_backend` — 格式 `{provider_id}/{model_id}`，如 `gemini-vertex/veo-3.1-fast-generate-001`
- `default_image_backend` — 格式同上，如 `gemini-aistudio/gemini-3.1-flash-image-preview`
- `video_generate_audio` — `true` / `false`
- `anthropic_api_key` — 智能体 API Key
- `anthropic_base_url` — 智能体代理地址
- 其他原 AdvancedConfigTab 管理的设置项

### 1.3 模块结构

```
lib/config/
├── models.py          # ORM: ProviderConfig, SystemSetting
├── repository.py      # 异步 CRUD: ProviderConfigRepository, SystemSettingRepository
├── service.py         # ConfigService 业务逻辑
├── registry.py        # PROVIDER_REGISTRY 静态元数据
└── migration.py       # JSON → DB 一次性迁移
```

### 1.4 ConfigService 接口

```python
class ConfigService:
    # 供应商配置
    async def get_provider_config(self, provider: str) -> dict[str, str]
    async def set_provider_config(self, provider: str, key: str, value: str) -> None
    async def delete_provider_config(self, provider: str, key: str) -> None
    async def get_all_providers_status(self) -> list[ProviderStatus]

    # 全局设置
    async def get_setting(self, key: str, default: str = "") -> str
    async def set_setting(self, key: str, value: str) -> None

    # 便捷方法
    async def get_default_video_backend(self) -> tuple[str, str]  # (provider_id, model_id)
    async def get_default_image_backend(self) -> tuple[str, str]
```

```python
@dataclass
class ProviderStatus:
    name: str                              # "gemini-aistudio"
    display_name: str                      # "Gemini AI Studio"
    status: Literal["ready", "unconfigured", "error"]
    media_types: list[str]                 # ["video", "image"]
    capabilities: list[str]                # ["text_to_video", ...]
    required_keys: list[str]               # ["api_key"]
    configured_keys: list[str]             # 已配置的 key 列表
    missing_keys: list[str]                # 缺失的必需 key
```

## 2. JSON → DB 迁移

### 2.1 触发条件

应用启动时检测 `projects/.system_config.json` 是否存在。

### 2.2 迁移映射

| 原 JSON 字段 | 目标表 | provider / key |
|---|---|---|
| `gemini_api_key` | provider_config | gemini-aistudio / api_key |
| `gemini_base_url` | provider_config | gemini-aistudio / base_url |
| Vertex 凭证文件路径 | provider_config | gemini-vertex / credentials_path |
| `vertex_gcs_bucket` | provider_config | gemini-vertex / gcs_bucket |
| `ark_api_key` | provider_config | seedance / api_key |
| `file_service_base_url` | provider_config | seedance / file_service_base_url |
| `xai_api_key` | provider_config | grok / api_key |
| `image_backend` ("aistudio"/"vertex") | system_setting | default_image_backend → 转换为 `gemini-{value}/{当前 image_model}` |
| `video_backend` ("aistudio"/"vertex") | system_setting | default_video_backend → 转换为 `gemini-{value}/{当前 video_model}` |
| `video_model` | 参与 default_video_backend 组合 | — |
| `image_model` | 参与 default_image_backend 组合 | — |
| `video_generate_audio` | system_setting | video_generate_audio |
| `anthropic_api_key` | system_setting | anthropic_api_key |
| `anthropic_base_url` | system_setting | anthropic_base_url |
| `anthropic_model` | system_setting | anthropic_model |
| `anthropic_default_haiku_model` | system_setting | anthropic_default_haiku_model |
| `anthropic_default_opus_model` | system_setting | anthropic_default_opus_model |
| `anthropic_default_sonnet_model` | system_setting | anthropic_default_sonnet_model |
| `claude_code_subagent_model` | system_setting | claude_code_subagent_model |
| `gemini_image_rpm` | system_setting | gemini_image_rpm |
| `gemini_video_rpm` | system_setting | gemini_video_rpm |
| `gemini_request_gap` | system_setting | gemini_request_gap |
| `image_max_workers` | system_setting | image_max_workers |
| `video_max_workers` | system_setting | video_max_workers |
| 其他未列出的 override 键 | system_setting | 原键名直接写入 |

### 2.3 迁移完成

迁移成功后将 `.system_config.json` 重命名为 `.system_config.json.bak`，避免重复迁移。

## 3. API 设计

### 3.1 `/api/v1/providers` — 供应商管理

**GET /api/v1/providers**

返回所有供应商及状态。

```json
{
  "providers": [
    {
      "id": "gemini-aistudio",
      "display_name": "Gemini AI Studio",
      "status": "ready",
      "media_types": ["video", "image"],
      "capabilities": ["text_to_video", "image_to_video", "text_to_image", "negative_prompt", "video_extend"],
      "configured_keys": ["api_key"],
      "missing_keys": []
    }
  ]
}
```

**GET /api/v1/providers/{id}/config**

返回单个供应商的配置字段详情。

```json
{
  "id": "gemini-aistudio",
  "display_name": "Gemini AI Studio",
  "status": "ready",
  "fields": [
    {
      "key": "api_key",
      "label": "API Key",
      "type": "secret",
      "required": true,
      "value_masked": "AIza…••••",
      "is_set": true
    },
    {
      "key": "base_url",
      "label": "Base URL",
      "type": "url",
      "required": false,
      "value": "",
      "is_set": false,
      "placeholder": "默认官方地址"
    }
  ]
}
```

**PATCH /api/v1/providers/{id}/config**

更新供应商配置。`null` 值表示清除该字段。

```json
{ "api_key": "AIza-new-key", "base_url": null }
```

**POST /api/v1/providers/{id}/test**

连接测试，返回可用模型列表。各供应商测试策略不同：
- **gemini-aistudio / gemini-vertex**: 调用 list models API 验证凭证和连接
- **seedance / grok**: 若 API 不支持 list models，则发送轻量级验证请求（如获取账户信息或发送最小参数请求），返回成功/失败即可，`available_models` 为该供应商在 registry 中注册的模型列表

```json
{
  "success": true,
  "available_models": ["veo-3.1-generate-001", "veo-3.1-fast-generate-001"],
  "message": "连接成功，发现 2 个可用模型"
}
```

**POST /api/v1/providers/gemini-vertex/credentials**

Vertex AI 凭证文件上传（特殊端点），保持现有上传逻辑。

### 3.2 `/api/v1/system/config` — 全局设置

瘦身为只管非供应商配置。

**GET /api/v1/system/config**

```json
{
  "settings": {
    "default_video_backend": "gemini-vertex/veo-3.1-fast-generate-001",
    "default_image_backend": "gemini-aistudio/gemini-3.1-flash-image-preview",
    "video_generate_audio": false,
    "anthropic_api_key": { "is_set": true, "masked": "sk-…••••" },
    "anthropic_base_url": "https://xxx.com"
  },
  "options": {
    "video_backends": [
      "gemini-aistudio/veo-3.1-generate-001",
      "gemini-aistudio/veo-3.1-fast-generate-001",
      "gemini-vertex/veo-3.1-generate-001",
      "gemini-vertex/veo-3.1-fast-generate-001",
      "seedance/doubao-seedance-1-5-pro-251215",
      "grok/grok-imagine-video"
    ],
    "image_backends": [
      "gemini-aistudio/gemini-3.1-flash-image-preview",
      "gemini-vertex/gemini-3.1-flash-image-preview"
    ]
  }
}
```

`options` 中只列出 status=ready 的供应商下的模型。

**PATCH /api/v1/system/config**

```json
{ "default_video_backend": "seedance/doubao-seedance-1-5-pro-251215" }
```

### 3.3 `/api/v1/usage/stats` — 用量统计

扩展现有 usage API，增加筛选和分组。

**GET /api/v1/usage/stats?provider=gemini-vertex&start=2026-03-01&end=2026-03-18&group_by=provider**

```json
{
  "stats": [
    {
      "provider": "gemini-vertex",
      "call_type": "video",
      "total_calls": 42,
      "success_calls": 38,
      "total_cost_usd": 12.50,
      "total_duration_seconds": 380
    }
  ],
  "period": { "start": "2026-03-01", "end": "2026-03-18" }
}
```

### 3.4 API 职责总结

| 路由 | 职责 | 对应前端栏位 |
|---|---|---|
| `/api/v1/providers` | 供应商 CRUD、连接测试 | 供应商 |
| `/api/v1/system/config` | 全局默认设置 | 智能体 + 图片/视频 + 高级设置 |
| `/api/v1/usage/stats` | 用量统计查询 | 用量统计 |

## 4. 前端设计

### 4.1 全局设置页 — 侧边栏布局

`SystemConfigPage` 从 Tab 布局改为侧边栏导航布局：

```
┌──────────┬──────────────────────────────────┐
│  设置     │                                  │
│          │   (右侧内容区)                     │
│ 🤖 智能体 │                                  │
│ 🔌 供应商 │                                  │
│ 🎬 图片/视频│                                │
│ 📊 用量统计│                                 │
│ ⚙️ 高级设置│                                 │
│          │                                  │
└──────────┴──────────────────────────────────┘
```

- 侧边栏图标使用 `lucide-react`
- 路由参数控制活跃栏位：`/settings?section=providers`

### 4.2 供应商栏位 — 列表 + 详情布局

```
┌──────────┬──────────────┬───────────────────┐
│  设置     │ 供应商列表    │ 供应商详情          │
│          │              │                   │
│ 🤖 智能体 │ Gemini AS  🟢│ Gemini AI Studio   │
│ 🔌 供应商 │ Gemini VX  🔴│ 状态: 已就绪        │
│ 🎬 图片/  │ Seedance   🟢│                   │
│    视频   │ Grok       🔴│ API Key [*****]    │
│ 📊 用量   │              │ Base URL [     ]   │
│ ⚙️ 高级   │              │                   │
│          │              │ [测试连接]          │
└──────────┴──────────────┴───────────────────┘
```

- 供应商 logo 使用 `@lobehub/icons`
- 状态指示器：🟢 ready / 🔴 unconfigured / 🟡 error
- 敏感字段掩码显示，支持显示/隐藏切换
- 连接测试按钮内联在详情底部

### 4.3 图片/视频栏位 — 分组下拉选择

两个选择器：默认视频模型、默认图片模型。

下拉列表按供应商分组显示（仅 status=ready 的供应商）：

```
── Gemini AI Studio ──
   veo-3.1-generate-001
   veo-3.1-fast-generate-001
── Gemini Vertex AI ──
   veo-3.1-generate-001
   veo-3.1-fast-generate-001
── Seedance ──
   doubao-seedance-1-5-pro-251215
```

附加选项：
- `video_generate_audio` 开关（标注"仅部分供应商支持"）

### 4.4 用量统计栏位

- 按供应商分组展示用量数据
- 筛选器：时间范围、供应商、调用类型（video/image）
- 展示字段：调用次数、成功率、费用、时长

### 4.5 智能体栏位

保留现有 `AgentConfigTab` 内容（Anthropic API Key、Base URL），适配新的 API 响应结构（从 system_setting 读取）。

### 4.6 高级设置栏位

保留现有 `AdvancedConfigTab` 内容，适配新的 API 响应结构。

### 4.7 通用组件

**`ProviderModelSelect`** — 分组下拉选择组件

- 接收 `options: string[]`（`provider_id/model_id` 格式）和 `providerDisplayNames: Record<string, string>`
- 按 `/` 拆分，provider 作为分组标题，model 作为选项
- 复用于全局设置页和项目设置页

## 5. 项目设置页

### 5.1 路由与交互

- 路由：`/projects/:name/settings`
- 交互：全屏覆盖层（overlay），左上角返回按钮关闭，回到项目工作台
- 修复当前路由空白的问题

### 5.2 内容

支持覆盖全局默认的模型选择：

- **视频模型** — 分组下拉，顶部额外选项「跟随全局默认」（显示当前全局值作为 hint）
- **图片模型** — 同上
- **生成音频** — 三态：跟随全局 / 开启 / 关闭

选择 `null` 即跟随全局默认。

### 5.3 数据存储

项目级覆盖存储在 `project.json` 中：

```json
{
  "video_backend": "seedance/doubao-seedance-1-5-pro-251215",
  "image_backend": null
}
```

`null` 或字段不存在 = 跟随全局默认。

## 6. 调用方迁移

### 6.1 后端

| 模块 | 改动 |
|---|---|
| `lib/system_config.py` (SystemConfigManager) | 废弃，由 `lib/config/service.py` (ConfigService) 替代 |
| `server/routers/system_config.py` | 瘦身，读写改走 ConfigService |
| `server/routers/` 新增 `providers.py` | 供应商 CRUD + 连接测试 |
| `server/services/generation_tasks.py` | `os.environ.get()` → `config_service.get_provider_config()` |
| `lib/media_generator.py` | 接收 provider_id/model 参数，不再自行读 env |
| `lib/video_backends/*.py` | 构造参数不变，由上层从 ConfigService 取出后传入 |
| `server/routers/assistant.py` | `os.environ.get("ANTHROPIC_*")` → `config_service.get_setting()` |
| `server/routers/generate.py` | 生成入队时的配置读取改走 ConfigService |
| `server/auth.py` | 认证相关配置改走 ConfigService |
| `server/agent_runtime/session_manager.py` | Agent 相关配置改走 ConfigService |
| `lib/generation_worker.py` | Worker 初始化时的速率限制 / 并发配置改走 ConfigService |
| `lib/usage_tracker.py` / `server/routers/usage.py` | 扩展筛选参数 |

### 6.2 前端

| 组件 | 改动 |
|---|---|
| `SystemConfigPage.tsx` | Tab → 侧边栏布局 |
| `MediaConfigTab.tsx` | 废弃，拆分为 `ProviderSection.tsx` + `MediaModelSection.tsx` |
| `AgentConfigTab.tsx` | 保留，适配新 API |
| `AdvancedConfigTab.tsx` | 保留，适配新 API |
| `ApiKeysTab.tsx` | 废弃，合并到供应商配置 |
| `config-status-store.ts` | 改用 `/api/v1/providers` 判断配置状态 |
| 新增 `UsageStatsSection.tsx` | 用量统计栏位 |
| 新增 `ProviderModelSelect.tsx` | 分组下拉组件 |
| 项目设置页 | 修复路由为全屏 overlay + 模型覆盖 UI |

## 7. 不在本次范围

- `ImageBackend` 抽象层提取（#101）
- Seedance 2.0 接入（#42）
- `.env` 中部署相关配置（`DATABASE_URL` 等）仍从环境变量读取
