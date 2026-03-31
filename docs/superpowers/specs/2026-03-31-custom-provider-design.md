# 自定义供应商设计文档

> 日期：2026-03-31  
> 分支：feature/custom-provider  
> 关联：docs/proposal-openai-and-custom-provider.md 第二部分、Issue #189

---

## 概述

支持用户自行添加「自定义供应商」，通过 API 格式 + Base URL + API Key + 模型列表接入任意兼容服务。本次支持两种 API 格式：OpenAI 兼容、Google 兼容（仅 API Key 认证模式）。

同时处理 Issue #189 中除限流外的 3 项 OpenAI 预置供应商改进。

## 范围

### 自定义供应商（主体）

- 自定义供应商的 CRUD
- 动态模型列表管理（自动发现 + 手动添加）
- 用户自定义定价
- 轻量 Backend 包装类（复用现有 OpenAI/Gemini 后端）
- 与 ConfigResolver、CostCalculator、用量统计的集成
- 完整前端 UI

### #189 遗留改进项

- Instructor fallback 结构化输出降级
- quality 参数传递链
- Video resolution 参数映射

### 不含

- RPM / request_gap 限流
- Google 兼容格式的 Vertex AI 认证模式

---

## 架构方案：平行轨道

预置供应商保持现有 `PROVIDER_REGISTRY` 不变。自定义供应商有独立的 API 端点、Service、前端区域。两者在以下节点汇合：

1. **Backend 选择** — ConfigResolver 解析默认 backend 时同时查询预置和自定义供应商
2. **模型选择下拉框** — `/api/v1/system-config/options` 合并两者的可用模型
3. **费用记录** — ApiCall 表通过 `provider` 字段（`custom-{id}`）统一记录
4. **用量统计** — 后端 API 返回 `display_name`，预置供应商从 `PROVIDER_REGISTRY` 取，自定义供应商 join `custom_provider` 表取

---

## 1. 数据模型

### 新增表 `custom_provider`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int PK | 自增主键，内部标识为 `custom-{id}` |
| `display_name` | str | 用户可见名称，如「我的 NewAPI」 |
| `api_format` | str | `"openai"` 或 `"google"` |
| `base_url` | str | API 基础地址 |
| `api_key` | str | 敏感字段，DB 存原文，API 响应时掩蔽（复用现有 `mask_secret()`） |
| `created_at` / `updated_at` | datetime | 时间戳 |

设计选择：`api_key` 和 `base_url` 直接存在供应商表中，不复用 `provider_credential` 表。自定义供应商是「一个供应商 = 一个中转站地址 + 一个 key」的简单模型，不需要多凭证切换。

### 新增表 `custom_provider_model`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int PK | 自增主键 |
| `provider_id` | int FK → custom_provider.id | 所属供应商 |
| `model_id` | str | 模型标识，如 `deepseek-v3` |
| `display_name` | str | 显示名称 |
| `media_type` | str | `"text"` / `"image"` / `"video"` |
| `is_default` | bool | 该供应商下该媒体类型的默认模型 |
| `is_enabled` | bool | 是否启用（用户勾选） |
| `price_unit` | str NULL | 计费单位：`"token"` / `"image"` / `"second"` |
| `price_input` | float NULL | 输入价格（text: /百万 token，image: /张，video: /秒） |
| `price_output` | float NULL | 输出价格（仅 text 有，其他为 NULL） |
| `currency` | str NULL | `"USD"` / `"CNY"` |
| `created_at` / `updated_at` | datetime | 时间戳 |

唯一约束：`(provider_id, model_id)`。

`is_default` 约束：每个 `(provider_id, media_type)` 组合最多一个 `is_default=True`，应用层保证。

价格可选：全部 NULL 表示不计费（Ollama 等本地场景）。

---

## 2. Backend 层

### 轻量包装类

三个包装类（`CustomTextBackend`、`CustomImageBackend`、`CustomVideoBackend`），每个约 30 行。持有一个内部 delegate（现有的 OpenAI/Gemini Backend 实例），覆盖 `name` 和 `model` 属性：

```python
# lib/custom_provider/backends.py
class CustomTextBackend:
    def __init__(self, *, provider_id: str, delegate: TextBackend, model: str):
        self._provider_id = provider_id   # "custom-3"
        self._delegate = delegate
        self._model = model

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return self._delegate.capabilities

    async def generate(self, request):
        return await self._delegate.generate(request)
```

### 构建流程

自定义 backend 不注册进 `_BACKEND_FACTORIES`，而是按需构建：

```python
# lib/custom_provider/factory.py
async def create_custom_backend(provider_id: str, model_id: str, media_type: str):
    # 1. 从 DB 查询 custom_provider + custom_provider_model
    # 2. 根据 api_format 选择内部 delegate：
    #    - "openai" → OpenAITextBackend / OpenAIImageBackend / OpenAIVideoBackend
    #    - "google" → GeminiTextBackend / GeminiImageBackend / GeminiVideoBackend
    # 3. 用 base_url + api_key + model_id 初始化 delegate
    # 4. 包装为 CustomXxxBackend(provider_id=..., delegate=..., model=...)
```

### ConfigResolver 集成

`ConfigResolver._auto_resolve_backend()` 扩展为：先查预置供应商，找不到再查自定义供应商中已启用且 `is_default=True` 的模型。

显式设置的默认 backend（如 `"custom-3:deepseek-v3"`）直接走自定义工厂路径。

---

## 3. 费用计算

### CostCalculator 扩展

```python
def calculate_cost(self, provider, call_type, *, model, ...):
    if provider.startswith("custom-"):
        return self._calculate_custom_cost(provider, call_type, model=model, ...)
    # ... 现有预置供应商逻辑不变
```

`_calculate_custom_cost()` 从 DB 查询 `custom_provider_model` 的价格字段：

| 媒体类型 | 计算方式 | 所需参数 |
|---------|---------|---------|
| text | `input_tokens * price_input / 1M + output_tokens * price_output / 1M` | input_tokens, output_tokens |
| image | `count * price_input` | 图片数量（默认 1） |
| video | `duration_seconds * price_input` | 时长秒数 |

价格字段为 NULL 时返回 `(0.0, currency)`，不阻塞使用。

### UsageTracker 透传

ApiCall 记录中 `provider` 存 `custom-{id}`，`model` 存实际模型 ID。用量统计 API 返回时 join `custom_provider` 表取 `display_name`。

---

## 4. Service 层

### CustomProviderService (`lib/custom_provider/service.py`)

**CRUD 操作：**

- `create_provider(display_name, api_format, base_url, api_key, models: list)` → 一次性创建供应商 + 模型列表
- `update_provider(provider_id, ...)` → 更新配置
- `delete_provider(provider_id)` → 级联删除关联模型
- `list_providers()` → 返回所有自定义供应商及其状态
- `get_provider(provider_id)` → 单个供应商详情（含模型列表）

**模型管理：**

- `add_model(provider_id, model_id, display_name, media_type, ...)` → 手动添加
- `update_model(model_id, ...)` → 更新价格/媒体类型/启用状态/默认标记
- `remove_model(model_id)` → 删除模型
- `set_default_model(model_id)` → 设为该媒体类型默认

**无状态操作（不依赖已落表的供应商）：**

- `discover_models(api_format, base_url, api_key)` → 模型自动发现
- `test_connection(api_format, base_url, api_key)` → 连接测试

### 模型自动发现逻辑

```
discover_models(api_format, base_url, api_key):
  1. 按格式调用：
     - OpenAI: GET {base_url}/models → 返回模型 ID 列表
     - Google: genai.Client(api_key=...).models.list() → 返回模型列表
  2. 媒体类型推断（OpenAI 格式）：
     - 模型 ID 含 image/dall → "image"
     - 模型 ID 含 video/sora/kling/wan/seedance/cog/mochi → "video"
     - 其余 → "text"
  3. Google 格式：从模型 supported_generation_methods 推断
     （含 generateContent → text，含 generateImages → image，含 predictVideo → video），
     无法获取则回退关键词推断
  4. 每个媒体类型的第一个模型标记为 is_default
  5. 返回推断结果（不写 DB），前端展示后用户确认再保存
```

---

## 5. API 路由层

路由前缀 `/api/v1/custom-providers/`。

**供应商 CRUD：**

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/` | 列出所有自定义供应商（含模型列表和状态） |
| POST | `/` | 创建供应商 + 模型列表（一次性） |
| GET | `/{id}` | 单个供应商详情 |
| PATCH | `/{id}` | 更新供应商配置 |
| DELETE | `/{id}` | 删除供应商（级联删除模型） |

**模型管理：**

| 方法 | 端点 | 说明 |
|------|------|------|
| PUT | `/{id}/models` | 批量替换整个模型列表（删除旧列表，写入新列表） |
| POST | `/{id}/models` | 添加单个模型 |
| PATCH | `/{id}/models/{model_id}` | 更新单个模型 |
| DELETE | `/{id}/models/{model_id}` | 删除单个模型 |

**无状态操作：**

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/discover` | 模型发现 |
| POST | `/test` | 连接测试 |

**汇合点：** `/api/v1/system-config/options` 扩展，将自定义供应商中已启用的模型追加到对应媒体类型的选项列表，格式 `"custom-{id}:{model_id}"`。

---

## 6. 前端

### 页面结构

在 `ProviderSection` 底部新增「自定义供应商」分区，点击条目展示 `CustomProviderDetail`。

```
设置 → 供应商
  ┌─ 预置供应商 ─────────────────────┐
  │  Google AI Studio    ● 已配置    │
  │  ...                             │
  ├─ 自定义供应商 ───────────────────┤
  │  我的 NewAPI         ● 已连接    │
  │  本地 Ollama         ● 已连接    │
  │  [+ 添加自定义供应商]            │
  └──────────────────────────────────┘
```

### 新建/编辑表单

1. 基础信息：名称、API 格式（下拉）、Base URL、API Key
2. [获取模型列表] → 调用 `/discover`
3. 模型列表：勾选启用、修正媒体类型、标记默认、填写价格
4. [测试连接] → 调用 `/test`
5. [保存] → 一次性提交

### 集成点

- **模型选择器**：`system-config/options` 已包含自定义模型，`ProviderModelSelect` 无需特殊处理
- **ProviderIcon**：已有 fallback（显示首字母），自定义供应商自动适用
- **用量统计**：后端返回 `display_name`，前端改用 `display_name ?? provider`

### 新增文件

- `frontend/src/components/pages/settings/CustomProviderSection.tsx`
- `frontend/src/components/pages/settings/CustomProviderDetail.tsx`
- `frontend/src/components/pages/settings/CustomProviderForm.tsx`
- `frontend/src/types/custom-provider.ts`

**注意**：前端实现前须调用 `/frontend-design` skill。

---

## 7. #189 遗留改进项

### 7.1 Instructor fallback 结构化输出降级

文件：`lib/text_backends/openai.py`

参照 Gemini 后端模式：原生 `response_format` 失败时捕获异常，回退到 Instructor 库解析。此改进同时惠及自定义供应商（OpenAI 兼容的中转站可能不支持 `response_format`）。

### 7.2 quality 参数传递链

文件：`lib/image_backends/base.py`、`lib/image_backends/openai.py`、`lib/usage_tracker.py`

`ImageGenerationResult` 新增可选字段 `quality: str | None`，`OpenAIImageBackend` 填入实际值，`UsageTracker` 透传到 `CostCalculator`。

### 7.3 Video resolution 参数映射

文件：`lib/video_backends/openai.py`

根据 `(resolution, aspect_ratio)` 组合映射到精确的 VideoSize，而非仅依赖 aspect_ratio。

---

## 新增/修改文件清单

### 新增

| 文件 | 说明 |
|------|------|
| `lib/custom_provider/__init__.py` | 模块入口 |
| `lib/custom_provider/service.py` | CustomProviderService |
| `lib/custom_provider/factory.py` | 自定义 backend 构建 |
| `lib/custom_provider/backends.py` | 包装类（Custom{Text,Image,Video}Backend） |
| `lib/custom_provider/discovery.py` | 模型自动发现逻辑 |
| `lib/db/models/custom_provider.py` | ORM 模型 |
| `lib/db/repositories/custom_provider_repo.py` | 数据仓储 |
| `alembic/versions/xxx_add_custom_provider.py` | 数据库迁移 |
| `server/routers/custom_providers.py` | API 路由 |
| `frontend/src/types/custom-provider.ts` | TypeScript 类型 |
| `frontend/src/components/pages/settings/CustomProviderSection.tsx` | 列表 UI |
| `frontend/src/components/pages/settings/CustomProviderDetail.tsx` | 详情面板 |
| `frontend/src/components/pages/settings/CustomProviderForm.tsx` | 新建/编辑表单 |
| `tests/test_custom_provider_service.py` | Service 单元测试 |
| `tests/test_custom_provider_api.py` | API 集成测试 |

### 修改

| 文件 | 改动 |
|------|------|
| `lib/config/resolver.py` | `_auto_resolve_backend()` 扩展查询自定义供应商 |
| `lib/cost_calculator.py` | 新增 `_calculate_custom_cost()` 分支 |
| `lib/usage_tracker.py` | 透传 quality 参数 |
| `lib/text_backends/openai.py` | Instructor fallback |
| `lib/image_backends/openai.py` | quality 传递 |
| `lib/image_backends/base.py` | `ImageGenerationResult` 新增 quality 字段 |
| `lib/video_backends/openai.py` | resolution 映射 |
| `lib/db/repositories/usage_repo.py` | 用量统计 join display_name |
| `server/routers/system_config.py` | options 合并自定义模型 |
| `server/routers/usage.py` | 返回 display_name |
| `server/app.py` | 注册新路由 |
| `frontend/src/components/pages/settings/ProviderSection.tsx` | 集成自定义供应商分区 |
| `frontend/src/components/pages/settings/UsageStatsSection.tsx` | 显示 display_name |
