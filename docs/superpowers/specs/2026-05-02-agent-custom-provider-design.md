# 智能体配置支持模型发现与复用自定义供应商

**日期**：2026-05-02
**作者**：Pollo（协助：Claude Code）
**状态**：设计草案

## 背景

智能体配置（`AgentConfigTab.tsx` + `server/routers/system_config.py`）当前要求用户手填：

- `anthropic_api_key`
- `anthropic_base_url`
- `anthropic_model` + 4 个 routing 模型（haiku / sonnet / opus / subagent）

这些值通过 `lib/config/service.py:sync_anthropic_env` 同步到 `ANTHROPIC_*` 环境变量供 Claude Agent SDK 使用。

但用户在"自定义供应商"（`lib/custom_provider/` + `server/routers/custom_providers.py`）里早已配置过中转站（OneAPI / NewAPI 等）的 `base_url` + `api_key`，并完成 OpenAI 协议下的模型发现。智能体配置场景下，用户被迫再次手动复制粘贴这些凭据。

调研结论（@anthropic-ai/sdk，CLI 内部调用）：
- 默认 baseURL `https://api.anthropic.com`，**不带** `/v1`
- 拼接逻辑 `baseURL + path`，path 总以 `/` 开头（`/v1/messages`、`/v1/models`），仅做"双 `/` 去重"
- 用户填的 `ANTHROPIC_BASE_URL` 必须是根级形态；带 `/v1` 后缀会拼出 `/v1/v1/messages` 报 404

中转站（OneAPI / NewAPI）普遍直接接受 `https://example.com` 根域名作为 Anthropic 协议入口，CLI 拼 `/v1/messages` 即可。

## 目标

1. **凭据复用**：智能体配置新增"从自定义供应商导入"快捷入口，把已有 provider 的 `base_url` + `api_key` 一键填入字段。保留手填，不替换。
2. **模型发现**：智能体配置新增"获取模型"按钮，基于当前 `base_url` + `api_key` 调用 Anthropic 协议 `GET /v1/models`，结果作为 `<datalist>` 候选注入到 5 个 model 字段。
3. **base_url 规范化**：新增 `ensure_anthropic_base_url()`，发现路径上对 base_url 做剥末尾 `/v1*` / `/messages` 等处理，与已有 `ensure_openai_base_url` / `ensure_google_base_url` 保持范式一致。

## 非目标

- 不引入新的 `anthropic-messages` endpoint family（不动 `lib/custom_provider/endpoints.py` ENDPOINT_REGISTRY）
- 不改自定义供应商对象本身（`discovery_format` 仍仅 `openai` / `google`）
- 不建立"智能体绑定到 provider"的引用关系——导入是值复制，后续解耦
- 不做 ANTHROPIC_BASE_URL 老数据迁移（写入 env 时仍按用户原值）
- 不改 routing 字段的"高级"折叠形态
- 不引入第三方 autocomplete 组件（用浏览器原生 `<datalist>`）

## 架构概览

```
┌────────────────────────────── 前端 ──────────────────────────────┐
│ AgentConfigTab.tsx                                              │
│  ├ [新] "从供应商导入"按钮 → ProviderImportPicker 下拉           │
│  │     选 provider → 把 base_url+api_key 填入 draft 字段        │
│  │                                                              │
│  ├ [新] "获取模型"按钮 (model 区域顶部)                          │
│  │     → API.discoverAnthropicModels(base_url, api_key)         │
│  │     → 写入 useState 的 modelCandidates: string[]             │
│  │                                                              │
│  └ 5 个 model input 全部挂同一个 <datalist id="anthropic-models">│
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────── 后端 ──────────────────────────────┐
│ server/routers/custom_providers.py                              │
│  ├ [新] POST /custom-providers/discover-anthropic               │
│  │     body: { base_url, api_key? }                             │
│  │     → discover_models(discovery_format="anthropic", ...)     │
│  │                                                              │
│  └ [新] GET /custom-providers/{id}/credentials                  │
│        返回 { base_url, api_key } 明文（CurrentUser 鉴权）       │
│                                                                 │
│ lib/custom_provider/discovery.py                                │
│  └ [新] _discover_anthropic(base_url, api_key)                  │
│        GET {normalized}/v1/models                               │
│        headers: x-api-key, anthropic-version: 2023-06-01        │
│                                                                 │
│ lib/config/url_utils.py                                         │
│  └ [新] ensure_anthropic_base_url(url)                          │
│        剥末尾 /v1*、/messages、/v1/messages；去 trailing slash   │
└──────────────────────────────────────────────────────────────────┘
```

## 后端设计

### 1. `lib/config/url_utils.py` — 新增 `ensure_anthropic_base_url`

参考已有 `ensure_google_base_url`（剥末尾版本路径）的范式：

```python
def ensure_anthropic_base_url(url: str | None) -> str | None:
    """规范化 Anthropic base_url。

    @anthropic-ai/sdk 内部会拼接 /v1/messages、/v1/models 等，所以
    base_url 必须是根级形态。如用户填了 https://example.com/v1 或
    /v1/messages 等带版本前缀的形式，需要剥掉，否则会拼出
    /v1/v1/messages 报 404。
    """
    if not url:
        return None
    s = url.strip().rstrip("/")
    if not s:
        return None
    s = re.sub(r"/v\d+(?:/messages)?$", "", s)  # 剥末尾 /v1 或 /v1/messages
    s = re.sub(r"/messages$", "", s)            # 兜底剥单独的 /messages
    return s
```

测试用例覆盖：
- `https://api.anthropic.com` → `https://api.anthropic.com`
- `https://example.com/v1` → `https://example.com`
- `https://example.com/v1/messages` → `https://example.com`
- `https://example.com/v1/messages/` → `https://example.com`
- `None` / `""` / `"   "` → `None`

### 2. `lib/custom_provider/discovery.py` — 新增 anthropic 分支

```python
async def discover_models(
    *,
    discovery_format: str,
    base_url: str | None,
    api_key: str,
) -> list[dict]:
    if discovery_format == "openai":
        return await _discover_openai(base_url, api_key)
    elif discovery_format == "google":
        return await _discover_google(base_url, api_key)
    elif discovery_format == "anthropic":
        return await _discover_anthropic(base_url, api_key)
    raise ValueError(
        f"不支持的 discovery_format: {discovery_format!r}，"
        f"支持: 'openai', 'google', 'anthropic'"
    )


async def _discover_anthropic(base_url: str | None, api_key: str) -> list[dict]:
    from lib.config.url_utils import ensure_anthropic_base_url
    from lib.httpx_shared import get_http_client

    normalized = ensure_anthropic_base_url(base_url) or "https://api.anthropic.com"
    resp = await get_http_client().get(
        f"{normalized}/v1/models",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    # Anthropic schema: {"data": [{"id": "claude-sonnet-4-5", "display_name": "..."}]}
    models = sorted(data.get("data", []), key=lambda m: m.get("id", ""))
    return _build_result_list([
        (m["id"], "anthropic-messages") for m in models if m.get("id")
    ])
```

注意：返回的 `endpoint` 字段是占位 `"anthropic-messages"`，**不参与** ENDPOINT_REGISTRY 派发——前端只读 `model_id` 那一列拿候选名单。`_build_result_list` 已有对未知 endpoint 的容错（取 `endpoint_to_media_type` 失败时设为 `unknown`）；为避免触发现有 ENDPOINT_REGISTRY 校验，实现时改为构造与 `_build_result_list` 同形态但跳过 media_type 计算的简化结果列表，仅返回 `model_id` 即可。

### 3. `server/routers/custom_providers.py` — 新增两条路由

```python
class DiscoverAnthropicRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None  # 省略时使用已存的 anthropic_api_key


@router.post("/custom-providers/discover-anthropic")
async def discover_anthropic_models(
    req: DiscoverAnthropicRequest,
    _user: CurrentUser,
    _t: Translator,
    svc: Annotated[ConfigService, Depends(get_config_service)],
) -> dict[str, Any]:
    api_key = req.api_key
    if not api_key:
        api_key = (await svc.get_setting("anthropic_api_key", "")).strip()
    if not api_key:
        raise HTTPException(status_code=400, detail=_t("anthropic_discovery_no_key"))

    base_url = req.base_url
    if base_url is None:
        base_url = (await svc.get_setting("anthropic_base_url", "")).strip() or None

    try:
        models = await discover_models(
            discovery_format="anthropic",
            base_url=base_url,
            api_key=api_key,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=400,
            detail=_t("anthropic_discovery_http_error", code=e.response.status_code),
        ) from e
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=400,
            detail=_t("anthropic_discovery_network_error", message=str(e)),
        ) from e
    return {"models": [m["model_id"] for m in models]}


@router.get("/custom-providers/{provider_id}/credentials")
async def get_custom_provider_credentials(
    provider_id: str,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    """返回明文 base_url + api_key，供智能体配置导入复用。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("custom_provider_not_found"))
    return {
        "base_url": provider.base_url or "",
        "api_key": provider.api_key or "",
    }
```

**路由位置选择**：放在 `custom_providers.py` 是为了和 openai/google discovery 内聚（与现有 `discover_models` 收口一致）。`discover-anthropic` 不绑定 provider id——它接受任意 base_url+api_key，因为前端调用时凭据可能是用户手填的（导入只是预填手段，导入后用户可改）。

### 4. i18n keys

`lib/i18n/{zh,en}/errors.py` 新增：
- `anthropic_discovery_no_key` — `"未配置 API Key，无法发现模型"` / `"API Key not configured, cannot discover models"`
- `anthropic_discovery_http_error` — `"模型发现请求失败 (HTTP {code})"` / `"Model discovery failed (HTTP {code})"`
- `anthropic_discovery_network_error` — `"模型发现网络错误：{message}"` / `"Model discovery network error: {message}"`
- `custom_provider_not_found` — 已存在则复用，否则新增

## 前端设计

### 1. 类型 + API client

`frontend/src/api/index.ts`（或对应模块）：
```typescript
discoverAnthropicModels(payload: {
  base_url?: string;
  api_key?: string;
}): Promise<{ models: string[] }>;

getCustomProviderCredentials(id: string): Promise<{
  base_url: string;
  api_key: string;
}>;
```

`frontend/src/types/custom-provider.ts`：补充对应 response 类型。

### 2. `AgentConfigTab.tsx` 改动

新增 state：
```typescript
const [importPickerOpen, setImportPickerOpen] = useState(false);
const [providers, setProviders] = useState<CustomProviderListItem[]>([]);
const [importingProviderId, setImportingProviderId] = useState<string | null>(null);

const [modelCandidates, setModelCandidates] = useState<string[]>([]);
const [discoverState, setDiscoverState] = useState<"idle"|"loading"|"error">("idle");
const [discoverError, setDiscoverError] = useState<string | null>(null);
```

#### 2.1 导入按钮

位置：API Key 卡片顶部右侧，与"current_label"/clear 按钮同一行的右侧（或卡片 header 右上）。

文案：`{t("import_from_provider")}`

交互：
1. 点按钮 → 弹下拉（`Popover`/`Listbox`，参考现有 `useDropdown` 模式或 headless 实现）
2. 列表来源：组件 mount 时拉取 `API.listCustomProviders()`，过滤掉 `api_key` 为空的（用 `masked_api_key` 是否非空判断）
3. 点中一项 → 闭合下拉 → 调用 `getCustomProviderCredentials(id)` → `setDraft({ ...draft, anthropicKey: cred.api_key, anthropicBaseUrl: cred.base_url })`
4. **不立即 PATCH 后端**——遵循现有 draft 模式，等用户点底部"保存"再写库（与现有 base_url/model 字段一致）
5. 错误处理：网络/404 用 `pushToast(errMsg(err), "error")`；下拉为空显示 `{t("import_no_providers")}`

#### 2.2 获取模型按钮

位置：Model Configuration section 卡片顶部，与 SectionHeading 同一行右侧。

文案：`{t("discover_models")}`，loading 时显示 `<Loader2 className="animate-spin" />` + 文案。

```tsx
<div className="flex items-center justify-between">
  <SectionHeading title={t("model_config")} description={t("model_config_desc")} />
  <button
    type="button"
    onClick={handleDiscover}
    disabled={discoverState === "loading"}
    className="inline-flex items-center gap-1.5 rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-300 hover:border-gray-600 hover:bg-gray-800/50 disabled:opacity-50"
  >
    {discoverState === "loading"
      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
      : <Search className="h-3.5 w-3.5" />}
    {t("discover_models")}
  </button>
</div>
{discoverError && (
  <p className="mt-1 text-xs text-rose-400">{discoverError}</p>
)}
```

#### 2.3 handleDiscover

```typescript
const handleDiscover = useCallback(async () => {
  // 优先用 draft 值；如 draft 空，让后端 fallback 到已存值
  const apiKey = draft.anthropicKey.trim() || undefined;
  const baseUrl = draft.anthropicBaseUrl.trim() || undefined;

  setDiscoverState("loading");
  setDiscoverError(null);
  try {
    const res = await API.discoverAnthropicModels({ base_url: baseUrl, api_key: apiKey });
    setModelCandidates(res.models);
    setDiscoverState("idle");
    if (res.models.length === 0) {
      setDiscoverError(t("discover_no_models"));
    }
  } catch (err) {
    setDiscoverError(errMsg(err));
    setDiscoverState("error");
  }
}, [draft.anthropicKey, draft.anthropicBaseUrl, t]);
```

**关键 UX 边界**：用户已保存 api_key 但 draft 为空（输入框是空的，masked 状态在外面显示）。此时点"获取模型"必须能用已保存的 key——后端通过 `req.api_key is None` 时 fallback 到 DB 已存值。前端无需特殊处理，传 `undefined` 即可。

#### 2.4 5 个 model input 挂 datalist

```tsx
<datalist id="anthropic-models">
  {modelCandidates.map(m => <option key={m} value={m} />)}
</datalist>

// anthropic_model input：
<input ... list="anthropic-models" />

// 4 个 routing input 同样：
<input ... list="anthropic-models" />
```

datalist 是浏览器原生 autocomplete：输入时模糊匹配，点击下拉箭头展示完整列表，零额外依赖。

### 3. i18n keys

`frontend/src/i18n/{zh,en}/dashboard.ts` 新增：
- `import_from_provider` — `"从供应商导入"` / `"Import from provider"`
- `import_no_providers` — `"暂无可导入的自定义供应商"` / `"No custom providers to import"`
- `discover_models` — `"获取模型"` / `"Discover models"`
- `discover_no_models` — `"未发现可用模型"` / `"No models found"`
- `import_provider_success` — `"已导入 {name} 的凭据"` / `"Imported credentials from {name}"`

## 数据流

### 导入流
```
组件 mount → GET /custom-providers (含 base_url + masked key)
  → setProviders([...])
用户点"从供应商导入" → 下拉打开
用户选 provider X
  → GET /custom-providers/X/credentials (返回明文 base_url + api_key)
  → setDraft({ anthropicKey: cred.api_key, anthropicBaseUrl: cred.base_url })
  → toast("import_provider_success", { name: provider.display_name })
用户在主页面看到字段被填充，可继续编辑
用户点"保存" → PATCH /system/config → sync_anthropic_env() → ANTHROPIC_* env
```

### 发现流
```
用户点"获取模型"
  → POST /custom-providers/discover-anthropic
       body: { base_url: draft 值或 undefined, api_key: draft 值或 undefined }
  → 后端：api_key/base_url 缺失时 fallback 到 DB 已存值
  → ensure_anthropic_base_url() 规范化
  → GET {normalized}/v1/models  (header: x-api-key, anthropic-version)
  → 返回 { models: ["claude-sonnet-4-5", ...] }
  → setModelCandidates([...])
  → 5 个 input 的 datalist 立刻可用
```

## 错误处理

| 场景 | 处理 |
|---|---|
| 发现接口 4xx/5xx (HTTPStatusError) | 后端 `HTTPException(400, _t("anthropic_discovery_http_error"))`；前端展示在按钮下方 |
| 发现接口网络异常 (RequestError) | 后端 `HTTPException(400, _t("anthropic_discovery_network_error"))`；前端同上 |
| 发现返回空列表 | 前端展示 `{t("discover_no_models")}`，不算错误（只是 hint） |
| 用户点导入但无 provider 列表 | 下拉显示 `{t("import_no_providers")}` 占位 |
| 用户改了 base_url 但未点发现 | 模型字段 datalist 仍是旧候选——可接受（候选只是 hint，不阻塞手填） |
| `getCustomProviderCredentials` 404 | `pushToast(errMsg, "error")`，不破坏页面状态 |
| draft + 已存值都没 api_key | 后端返回 `anthropic_discovery_no_key` |

## 测试策略

### 后端

- `tests/test_url_utils.py` — 新增 `test_ensure_anthropic_base_url`：5 类用例（裸根 / `/v1` / `/v1/messages` / 带 trailing slash / `None` 与空字符串）
- `tests/test_model_discovery.py` — 新增 `_discover_anthropic` 用例，mock `lib.httpx_shared.get_http_client`，覆盖：
  - 成功返回（schema 含 `data: [{id, display_name}]`）
  - HTTP 4xx/5xx → HTTPStatusError
  - 网络错误 → RequestError
  - base_url 为 None → 用默认 `https://api.anthropic.com`
- `tests/test_custom_providers_api.py` — 新增 2 个端点用例：
  - `discover-anthropic`：显式 api_key / 省略走 DB / 都缺时 400 / 4xx 响应 i18n key 正确
  - `get_custom_provider_credentials`：成功返回明文 / 不存在 404 / 未鉴权 401

### 前端

`frontend/src/components/pages/AgentConfigTab.test.tsx` 扩展：
- 导入按钮：mock `listCustomProviders` + `getCustomProviderCredentials`，验证 draft 被正确填充、toast 文案正确
- 导入按钮：provider 列表为空时显示占位
- 获取模型按钮：mock `discoverAnthropicModels`，验证 `modelCandidates` 写入 + datalist `<option>` 渲染
- 获取模型按钮：loading/error 状态切换、空列表时 `discover_no_models` 提示
- 已存 api_key 但 draft 空时点发现：验证 payload 不带 api_key（让后端 fallback）

## 改动清单

| 文件 | 类型 |
|---|---|
| `lib/config/url_utils.py` | 新增 `ensure_anthropic_base_url` |
| `lib/custom_provider/discovery.py` | 新增 `_discover_anthropic` 分支 + `discover_models` anthropic dispatch |
| `server/routers/custom_providers.py` | 新增 `POST /custom-providers/discover-anthropic` + `GET /custom-providers/{id}/credentials` |
| `lib/i18n/{zh,en}/errors.py` | 4 个 i18n key |
| `frontend/src/api/index.ts` | `discoverAnthropicModels` + `getCustomProviderCredentials` |
| `frontend/src/types/custom-provider.ts` | 类型补充 |
| `frontend/src/components/pages/AgentConfigTab.tsx` | 导入按钮 + 获取模型按钮 + datalist + 状态 |
| `frontend/src/i18n/{zh,en}/dashboard.ts` | 5 个 UI key |
| `tests/test_url_utils.py` | 新增 anthropic 用例 |
| `tests/test_model_discovery.py` | 新增 anthropic 用例 |
| `tests/test_custom_providers_api.py` | 新增 2 个端点用例 |
| `frontend/src/components/pages/AgentConfigTab.test.tsx` | 导入 + 发现交互用例 |

## 风险与权衡

- **明文凭据接口**：新增 `GET /custom-providers/{id}/credentials` 暴露明文 api_key 给已认证用户。虽然现有架构里 `anthropic_api_key` 也通过 PATCH 发回明文（用户输入），但增加一条"读取明文"的端点扩大了攻击面。缓解：仅 `CurrentUser` 鉴权（与现有路由对齐），日志不打印 body，TODO 注释提醒未来多用户场景需评估细粒度授权。
- **datalist 在浏览器表现差异**：Safari/Firefox 下拉箭头位置和样式略不同；可接受，不引入第三方 autocomplete 库。
- **发现请求超时**：固定 15s。中转站偶尔卡顿；超时直接报错让用户重试，不做自动重试避免卡 UI。
- **未规范化的 ANTHROPIC_BASE_URL 已存在 DB**：老用户已存的 base_url 可能带 `/v1`。本次只规范化"发现"路径上的 base_url；写到 ANTHROPIC_BASE_URL env 时仍按用户原值传给 SDK。如果 SDK 调用失败，用户自行清理字段重填。**不做迁移脚本**，避免改动用户已生效的配置。
- **discover_models endpoint 占位 `"anthropic-messages"`**：返回值的 endpoint 字段不进 ENDPOINT_REGISTRY；为避免触发 `endpoint_to_media_type` 校验，`_discover_anthropic` 不复用 `_build_result_list`，自己构造 `[{"model_id": ..., "display_name": ..., "endpoint": "anthropic-messages", "is_default": False, "is_enabled": True}]`。前端只用 `model_id`。
- **导入与脱钩**：导入是值复制，provider 凭据后续若变更，智能体配置不会自动跟随。这是设计意图（保留手填，简单可控），用户重新导入即可。

## 非目标回顾

本设计不引入以下内容，需要时再开新 spec：

- 新的 `anthropic-messages` endpoint family（自定义供应商系统不增强）
- 智能体配置与 provider 的引用关系（绑定模式）
- 模型字段从 input 改为 select-only 形态
- `ANTHROPIC_BASE_URL` 已存值的迁移
- 发现结果的持久化缓存（每次按钮点击都重新拉取）
