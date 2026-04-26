# 自定义供应商：协议归属下沉到模型层（endpoint）设计

**日期**：2026-04-26
**状态**：设计稿（待用户复核）
**关联分支**：`feature/custom-provider-model-endpoint`

## 背景

ArcReel 的自定义供应商当前以 `CustomProvider.api_format ∈ {openai, google, newapi}` 在供应商层决定调用协议。这隐含了「一个供应商 = 一种协议」的假设。

中转站生态的现实是：一个 NewAPI/OneAPI 实例同时暴露多种协议——文本走 Chat Completions，图片走 Images API，视频走 NewAPI 统一视频端点或 OpenAI/Gemini 原生，Claude 模型走 Anthropic Messages。**用户的同一个 API key 能访问所有这些协议，差别只在每个 model id 该走哪条路。**

强制按协议拆 provider 的后果：
- 同一中转站要建 3–5 个 provider，重复填 `base_url + api_key`
- 模型管理分散在多个页面
- 模型选择器无法在一个 key 下汇总所有可用模型

本设计将"协议"维度下沉到 **模型** 层级，并将其抽象为一组命名的 **endpoint**（参考 NewAPI 模型卡式呈现）。

## 范围

经过澄清确认：

| 决策项 | 结论 |
|---|---|
| 重构范围 | 协议下沉到模型层 + 按媒体类型细分 endpoint |
| Endpoint 阵容 | 现有 7 条；本轮**不引入** Anthropic Messages，留枚举位 |
| Provider 层 `api_format` | 改名 `discovery_format`，UI 弱化（仅用于 discovery / 连通测试） |
| 模型层 `media_type` | 删除字段，由 `endpoint` 在运行时推导（单一真相源） |

## §1 数据模型

### Schema 改动

`custom_provider`（变更前 → 变更后）：

```diff
  id: int
  display_name: str
- api_format: str ∈ {openai, google, newapi}
+ discovery_format: str ∈ {openai, google}
  base_url: text
  api_key: text
```

`custom_provider_model`（变更前 → 变更后）：

```diff
  id: int
  provider_id: FK
  model_id: str
  display_name: str
- media_type: str ∈ {text, image, video}
+ endpoint: str ∈ ENDPOINT_REGISTRY
  is_default: bool
  is_enabled: bool
  price_unit, price_input, price_output, currency
  supported_durations, resolution
```

### ENDPOINT_REGISTRY（单一真相源）

新文件 `lib/custom_provider/endpoints.py`：

```python
@dataclass(frozen=True)
class EndpointSpec:
    key: str                  # "openai-chat"
    media_type: str           # "text" | "image" | "video"
    family: str               # "openai" | "google" | "newapi"
    display_name_key: str     # i18n key（dashboard ns）
    build_backend: Callable[[CustomProvider, str], CustomTextBackend | CustomImageBackend | CustomVideoBackend]


ENDPOINT_REGISTRY: dict[str, EndpointSpec] = {
    "openai-chat":     EndpointSpec(...),
    "gemini-generate": EndpointSpec(...),
    "openai-images":   EndpointSpec(...),
    "gemini-image":    EndpointSpec(...),
    "openai-video":    EndpointSpec(...),
    "gemini-video":    EndpointSpec(...),
    "newapi-video":    EndpointSpec(...),
}
```

新增辅助：
- `get_endpoint_spec(key) -> EndpointSpec`（unknown 抛 ValueError）
- `endpoint_to_media_type(key) -> str`
- `list_endpoints_by_media_type(media_type) -> list[EndpointSpec]`

### 一次性数据迁移（alembic）

新 revision：rename + add + 数据回填 + drop。

`provider.api_format → discovery_format`：

| 旧 `api_format` | 新 `discovery_format` |
|---|---|
| `openai` | `openai` |
| `google` | `google` |
| `newapi` | `openai`（NewAPI 列模型本质即 OpenAI 兼容） |

`model.(api_format, media_type) → endpoint`：

| `api_format` | `text` | `image` | `video` |
|---|---|---|---|
| `openai` | `openai-chat` | `openai-images` | `openai-video` |
| `google` | `gemini-generate` | `gemini-image` | `gemini-video` |
| `newapi` | `openai-chat` | `openai-images` | `newapi-video` |

迁移逻辑：先 add column → SELECT join 计算回填 → drop old columns。任何无法映射的组合 → fail loud（不静默丢失）。

> SQLite 不支持原生 `ALTER TABLE DROP COLUMN`（直到 3.35）；alembic op 用 `with op.batch_alter_table(...)` 重建表，PostgreSQL 自然支持原生 DDL。

`downgrade()` 反向重建 `api_format` / `media_type` 两列；endpoint 含历史枚举外的值（不会发生）→ fail loud。

## §2 运行时：Backend Factory + Discovery

### `lib/custom_provider/factory.py`

```python
def create_custom_backend(
    *,
    provider: CustomProvider,
    model_id: str,
    endpoint: str,
) -> CustomTextBackend | CustomImageBackend | CustomVideoBackend:
    spec = ENDPOINT_REGISTRY.get(endpoint)
    if spec is None:
        raise ValueError(f"unknown endpoint: {endpoint!r}")
    return spec.build_backend(provider=provider, model_id=model_id)
```

每个 `EndpointSpec.build_backend` 闭包内复用现有 delegate（OpenAITextBackend / GeminiVideoBackend / NewAPIVideoBackend …），并用 `ensure_openai_base_url` / `ensure_google_base_url` 做 URL 规整。`Custom*Backend` 包装类不动。

### `server/services/generation_tasks.py`

`_create_custom_backend(provider_name, model_id, media_type)` 简化为 `_create_custom_backend(provider_name, model_id)`：

1. parse provider_id → 取 `CustomProvider`
2. 查 `CustomProviderModel` by `(provider_id, model_id)`
3. `create_custom_backend(provider, model_id, model.endpoint)`

调用方 165/226 两处合并为同一函数；media_type 不再作为参数传入。

### Discovery API

`POST /custom-providers/discover`：
```diff
- body: { api_format, base_url, api_key }
+ body: { discovery_format, base_url, api_key }
```

`lib/custom_provider/discovery.py`：

```python
async def discover_models(
    *,
    discovery_format: str,
    base_url: str | None,
    api_key: str,
) -> list[dict]:
    # 与原实现一致：openai 分支用 OpenAI SDK，google 分支用 google-genai
    # 不再有 newapi 分支（已折叠到 openai）

def infer_endpoint(model_id: str, discovery_format: str) -> str:
    # 1) 视频家族（kling/wan/seedance/veo/pika/minimax/hailuo/jimeng/runway/sora/cog/mochi）
    #    discovery_format=google → "gemini-video"  (Google AI Studio 直连只可能是 veo 系)
    #    discovery_format=openai：
    #       sora-* → "openai-video"
    #       其他视频家族 → "newapi-video"  (中转站最常见)
    # 2) 图像（含 image/dall/img/imagen/flux）
    #    discovery_format=google → "gemini-image" 否则 "openai-images"
    # 3) 文本（默认）
    #    discovery_format=google → "gemini-generate" 否则 "openai-chat"
```

发现项返回 `{ model_id, display_name, endpoint, is_default, is_enabled }`（去掉 `media_type`）。`is_default` 仍按推算 `media_type` 分组取每组首项为 default。

### 连通测试 / Auto-resolve / ConfigResolver

- `POST /custom-providers/test` & `POST /custom-providers/{id}/test`：参数改用 `discovery_format`，`newapi` 特殊分支删除
- `ConfigResolver._resolve_video_capabilities_from_project`：custom 分支按 `model.endpoint` 推 media_type，校验 `media_type == "video"` 否则 raise `endpoint_media_type_mismatch`
- `ConfigResolver._auto_resolve_backend`：列举 enabled custom 模型时通过 `endpoint_to_media_type(model.endpoint)` 过滤

## §3 前端 UI

### Provider 顶部表单

`frontend/src/components/pages/settings/CustomProviderForm.tsx`：

- 移除 prominent `apiFormat` `<select>` 控件
- 在 base_url + api_key 之下加一行**小字行**：
  > 模型发现协议：[OpenAI 兼容（默认） / Google AI Studio]　— 仅用于"发现模型"和"连通测试"，不影响调用
- 字段名 `apiFormat` → `discoveryFormat`，去掉 `newapi` 选项
- URL 预览（urlPreview）当前根据 `apiFormat` 区分；改为根据 `discoveryFormat` 区分（OpenAI 加 `/v1/models`，Google 加 `/v1beta/models`）

### 模型列表行（核心改动）

- 删除 media_type `<select>`
- 新增 endpoint `<select>`，按媒体类型分组：

| 分组 | 选项（i18n display_name） | 枚举值 |
|---|---|---|
| 📝 文本 | OpenAI Chat Completions | `openai-chat` |
| 📝 文本 | Google Gemini | `gemini-generate` |
| 🖼 图片 | OpenAI Images API | `openai-images` |
| 🖼 图片 | Google Gemini Image | `gemini-image` |
| 🎬 视频 | NewAPI Unified Video | `newapi-video` |
| 🎬 视频 | OpenAI Video (Sora) | `openai-video` |
| 🎬 视频 | Google Veo | `gemini-video` |

`ModelRow` 类型改名 `media_type` → `endpoint`；`MEDIA_TYPE_OPTIONS` 改为 `ENDPOINT_OPTIONS`（按 group 排序）。

### 联动 / 校验

- 切换 endpoint → 自动 `is_default = false`（避免跨 media_type 默认冲突；用户可重新点亮）
- `priceLabel(endpoint)` 取代 `priceLabel(mediaType)`：内部仍按推算的 media_type 切换 per-image / per-second / per-M-token
- Resolution 行只在推算 media_type ∈ {image, video} 时显示
- `existingToRow` / `discoveredToRow` / `rowToInput` 字段同步改造
- 全选按钮、过滤、URL 预览等其余逻辑不变

## §4 错误处理 / 测试 / i18n

### API 校验

`server/routers/custom_providers.py`：

- `ModelInput.endpoint` 必填；不在 `ENDPOINT_REGISTRY` → 422 `unknown_endpoint`
- `CreateProviderRequest.discovery_format` 不在 `{openai, google}` → 422 `unknown_discovery_format`
- 启用模型缺 endpoint → 422 `endpoint_required`
- `_check_unique_defaults` 改用 `endpoint_to_media_type(m.endpoint)` 分组

### 运行时错误

- `create_custom_backend`：unknown endpoint → ValueError → 502 `backend_creation_failed`
- `ConfigResolver.video_capabilities`：endpoint 推算 media_type 与期望不符 → ValueError `endpoint_media_type_mismatch`

### 迁移期容错

- `alembic upgrade`：任何无法映射的 `(api_format, media_type)` → fail loud
- `alembic downgrade`：endpoint 含枚举外值 → fail loud

### 测试矩阵

新增 / 重写：

| 文件 | 覆盖点 |
|---|---|
| `tests/test_custom_provider_endpoints.py`（新） | ENDPOINT_REGISTRY 完整性、`endpoint_to_media_type`、`list_endpoints_by_media_type` |
| `tests/test_custom_provider_factory.py` | 7 条 endpoint 都能 build_backend；未知 endpoint raise |
| `tests/test_custom_providers_api.py` | 422 校验；is_default 跨 endpoint 冲突；PUT 全量更新；新字段名 |
| `tests/test_custom_provider_resolution.py` | discovery_format=google 全路径；video_capabilities 走 endpoint |
| `tests/test_model_discovery.py` | `infer_endpoint` 启发式（kling / sora / veo / imagen / dall / 普通文本） |
| `tests/test_alembic_custom_provider_endpoint.py`（新） | 9 种历史组合 → upgrade → endpoint 正确；downgrade → `(api_format, media_type)` 复原 |
| `tests/test_custom_provider_repo.py` | `list_enabled_models_by_media_type` 改读 endpoint |

前端：

- 新增 vitest：endpoint 切换触发 `is_default` 重置；价格标签按推算 media_type 切换；URL 预览按 discovery_format 切换
- `test_i18n_consistency.py` 自动覆盖新增 zh/en key 漂移

### i18n 新增 key

后端 `lib/i18n/{zh,en}/errors.py`：
- `unknown_endpoint`、`unknown_discovery_format`、`endpoint_required`
- `endpoint_media_type_mismatch`、`backend_creation_failed`

前端 `frontend/src/i18n/{zh,en}/dashboard.json`：
- `endpoint_label`、`endpoint_help_text`
- `discovery_format_label`、`discovery_format_help`
- `endpoint_text_group`、`endpoint_image_group`、`endpoint_video_group`
- 7 条 endpoint 的展示名（`endpoint_openai_chat_display` 等）
- 旧 `api_format_label` / `media_type_label` 在所有引用点改造完后清理

### 向后兼容

- API 形态 breaking：`CreateProviderRequest` / `FullUpdateProviderRequest` / `ProviderResponse` / discover & test 请求体字段名变更
- 仅内置前端调用，按 ArcReel 风格不留 backwards-compat shim
- `project.json` 的 `video_backend` / `default_video_backend` / `text_backend_*` 等保持 `provider_id/model_id` 格式不变

## §5 实施次序（提示给 writing-plans 阶段）

1. `endpoints.py` registry + `EndpointSpec` 闭包 + 单测
2. `factory.create_custom_backend` 改造按 endpoint 分发 + 单测
3. `discovery.discover_models` + `infer_endpoint` + 单测
4. ORM 模型 + repository 改字段
5. Alembic 一次性迁移 + 双向迁移测试
6. Router + Pydantic schema 字段改名 + 校验更新
7. `ConfigResolver` 改读 endpoint 推 media_type
8. `generation_tasks._create_custom_backend` 简化签名
9. 前端 types + Form 组件改造 + vitest
10. i18n key 增删
11. 端到端集成测试 + ruff + pytest cov ≥ 80%

## 不在范围内

- Anthropic Messages 协议适配（留 endpoint 枚举扩展位，未来单独 PR）
- 拆分 `openai-chat` / `openai-responses` / `openai-chat-multimodal` 等子 endpoint
- 自动跨协议 discovery（同时探测 OpenAI 与 Google 合并去重）
- 模型卡式 UI v2（折叠分组 / 详细端点路径展示）
