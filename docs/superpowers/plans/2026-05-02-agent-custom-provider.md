# 智能体配置支持模型发现与复用自定义供应商 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让智能体配置页面能从自定义供应商一键导入凭据，并按 Anthropic 协议发现可用模型作为输入框 autocomplete 候选。

**Architecture:** 后端在 `lib/custom_provider/discovery.py` 新增 `discovery_format="anthropic"` 分支（复用已有 `_run_discover` 错误处理）；新增两条路由 `POST /custom-providers/discover-anthropic` 与 `GET /custom-providers/{id}/credentials`。前端在 `AgentConfigTab.tsx` 加两个按钮（导入 + 获取模型），发现结果通过浏览器原生 `<datalist>` 注入到 5 个 model input。

**Tech Stack:** Python 3.12 + FastAPI + httpx + pytest，前端 React 19 + TypeScript + i18next + Tailwind。

**Spec:** `docs/superpowers/specs/2026-05-02-agent-custom-provider-design.md`

---

## 文件结构

| 文件 | 类型 | 责任 |
|---|---|---|
| `lib/config/url_utils.py` | 修改 | 新增 `ensure_anthropic_base_url` |
| `tests/test_url_utils.py` | 新建 | 三个 normalizer 的单元测试 |
| `lib/custom_provider/discovery.py` | 修改 | 新增 `_discover_anthropic` + dispatch |
| `tests/test_model_discovery.py` | 修改 | 新增 anthropic 发现用例 |
| `lib/i18n/zh/errors.py` + `lib/i18n/en/errors.py` | 修改 | 新增 `anthropic_discovery_no_key` |
| `server/routers/custom_providers.py` | 修改 | 新增 2 条路由 + Pydantic 模型 |
| `tests/test_custom_providers_api.py` | 修改 | 新增两个端点用例 |
| `frontend/src/api.ts` | 修改 | 新增 `discoverAnthropicModels` + `getCustomProviderCredentials` |
| `frontend/src/types/custom-provider.ts` | 修改 | 新增 response 类型 |
| `frontend/src/i18n/{zh,en}/dashboard.ts` | 修改 | 新增 4 个 UI key（`discover_models` 已存在复用） |
| `frontend/src/components/pages/AgentConfigTab.tsx` | 修改 | 导入按钮 + 获取模型按钮 + datalist + state |
| `frontend/src/components/pages/AgentConfigTab.test.tsx` | 修改 | 导入 + 发现交互用例 |

---

## Task 1：`ensure_anthropic_base_url` URL 规范化

**Files:**
- Modify: `lib/config/url_utils.py`
- Create: `tests/test_url_utils.py`

- [ ] **Step 1：新建测试文件 `tests/test_url_utils.py`，写 5 个失败用例**

```python
"""URL 规范化工具单元测试。"""

from __future__ import annotations

import pytest

from lib.config.url_utils import ensure_anthropic_base_url


class TestEnsureAnthropicBaseUrl:
    def test_official_root_unchanged(self):
        assert ensure_anthropic_base_url("https://api.anthropic.com") == "https://api.anthropic.com"

    def test_strips_trailing_v1(self):
        assert ensure_anthropic_base_url("https://example.com/v1") == "https://example.com"

    def test_strips_trailing_v1_messages(self):
        assert ensure_anthropic_base_url("https://example.com/v1/messages") == "https://example.com"

    def test_strips_trailing_slash_after_v1_messages(self):
        assert ensure_anthropic_base_url("https://example.com/v1/messages/") == "https://example.com"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_blank_returns_none(self, value):
        assert ensure_anthropic_base_url(value) is None
```

- [ ] **Step 2：运行测试验证全部失败**

Run: `uv run python -m pytest tests/test_url_utils.py -v`
Expected: FAIL — `ImportError: cannot import name 'ensure_anthropic_base_url'`

- [ ] **Step 3：实现 `ensure_anthropic_base_url`**

在 `lib/config/url_utils.py` 末尾追加（保留现有函数）：

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
    s = re.sub(r"/v\d+(?:/messages)?$", "", s)
    s = re.sub(r"/messages$", "", s)
    return s
```

- [ ] **Step 4：运行测试验证通过**

Run: `uv run python -m pytest tests/test_url_utils.py -v`
Expected: PASS（5 个用例全部通过）

- [ ] **Step 5：lint + 提交**

```bash
uv run ruff check lib/config/url_utils.py tests/test_url_utils.py
uv run ruff format lib/config/url_utils.py tests/test_url_utils.py
git add lib/config/url_utils.py tests/test_url_utils.py
git commit -m "feat(url_utils): add ensure_anthropic_base_url"
```

---

## Task 2：`_discover_anthropic` + dispatch

**Files:**
- Modify: `lib/custom_provider/discovery.py`
- Modify: `tests/test_model_discovery.py`

- [ ] **Step 1：在 `tests/test_model_discovery.py` 末尾追加测试类**

```python
# ---------------------------------------------------------------------------
# discover_models — Anthropic format
# ---------------------------------------------------------------------------


class TestDiscoverModelsAnthropic:
    @patch("lib.custom_provider.discovery.get_http_client")
    async def test_basic_discovery(self, mock_get_client):
        """Anthropic 协议返回的模型按 id 排序，仅保留 model_id。"""
        from unittest.mock import AsyncMock

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "claude-opus-4-7", "display_name": "Opus 4.7"},
                {"id": "claude-haiku-4-5", "display_name": "Haiku 4.5"},
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        from lib.custom_provider.discovery import discover_models

        result = await discover_models(
            discovery_format="anthropic",
            base_url="https://example.com/v1",  # 故意带 /v1，验证规范化
            api_key="sk-ant-test",
        )

        ids = [m["model_id"] for m in result]
        assert ids == ["claude-haiku-4-5", "claude-opus-4-7"]
        # URL 规范化：/v1 应被剥掉，请求 path 为 /v1/models
        called_url = mock_client.get.call_args.args[0]
        assert called_url == "https://example.com/v1/models"
        # headers 携带 anthropic 鉴权
        headers = mock_client.get.call_args.kwargs["headers"]
        assert headers["x-api-key"] == "sk-ant-test"
        assert headers["anthropic-version"] == "2023-06-01"

    @patch("lib.custom_provider.discovery.get_http_client")
    async def test_default_base_url_when_none(self, mock_get_client):
        """base_url 缺省时使用官方 https://api.anthropic.com。"""
        from unittest.mock import AsyncMock

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": []}
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        from lib.custom_provider.discovery import discover_models

        await discover_models(discovery_format="anthropic", base_url=None, api_key="key")

        called_url = mock_client.get.call_args.args[0]
        assert called_url == "https://api.anthropic.com/v1/models"

    @patch("lib.custom_provider.discovery.get_http_client")
    async def test_skips_entries_without_id(self, mock_get_client):
        """data 中 id 缺失的条目被跳过。"""
        from unittest.mock import AsyncMock

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [{"id": "claude-x"}, {"display_name": "no id"}],
        }
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        from lib.custom_provider.discovery import discover_models

        result = await discover_models(discovery_format="anthropic", base_url=None, api_key="k")
        assert [m["model_id"] for m in result] == ["claude-x"]

    async def test_unknown_format_raises(self):
        """anthropic 仍是已知 format；未知 format 抛 ValueError 含 anthropic。"""
        from lib.custom_provider.discovery import discover_models

        with pytest.raises(ValueError, match="anthropic"):
            await discover_models(discovery_format="bogus", base_url=None, api_key="k")
```

- [ ] **Step 2：运行测试验证失败**

Run: `uv run python -m pytest tests/test_model_discovery.py::TestDiscoverModelsAnthropic -v`
Expected: FAIL — `discover_models` 不识别 `"anthropic"`，进入 ValueError 分支但 message 不含 anthropic

- [ ] **Step 3：在 `lib/custom_provider/discovery.py` 增加 dispatch + `_discover_anthropic`**

文件顶部 import 区追加：

```python
from lib.config.url_utils import ensure_anthropic_base_url
from lib.httpx_shared import get_http_client
```

修改 `discover_models` 函数 dispatch（找到 `else: raise ValueError(...)` 那行）：

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
    else:
        raise ValueError(
            f"不支持的 discovery_format: {discovery_format!r}，"
            f"支持: 'openai', 'google', 'anthropic'"
        )
```

在 `_discover_google` 后追加 `_discover_anthropic`：

```python
async def _discover_anthropic(base_url: str | None, api_key: str) -> list[dict]:
    """Anthropic Messages 协议 GET /v1/models 发现可用模型。

    返回 dict 与 OpenAI/Google 路径同形态，但 endpoint 字段为空字符串
    （anthropic 不参与 ENDPOINT_REGISTRY 派发，前端只读 model_id）。
    """
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
    entries = sorted(
        (m for m in data.get("data", []) if m.get("id")),
        key=lambda m: m["id"],
    )
    return [
        {
            "model_id": m["id"],
            "display_name": m.get("display_name") or m["id"],
            "endpoint": "",
            "is_default": False,
            "is_enabled": True,
        }
        for m in entries
    ]
```

- [ ] **Step 4：运行测试验证通过**

Run: `uv run python -m pytest tests/test_model_discovery.py::TestDiscoverModelsAnthropic -v`
Expected: PASS（4 个用例全部通过）

完整测试无回归：
Run: `uv run python -m pytest tests/test_model_discovery.py -v`
Expected: PASS（含原有 OpenAI / Google 用例）

- [ ] **Step 5：lint + 提交**

```bash
uv run ruff check lib/custom_provider/discovery.py tests/test_model_discovery.py
uv run ruff format lib/custom_provider/discovery.py tests/test_model_discovery.py
git add lib/custom_provider/discovery.py tests/test_model_discovery.py
git commit -m "feat(discovery): add anthropic discovery_format branch"
```

---

## Task 3：i18n key — `anthropic_discovery_no_key`

**Files:**
- Modify: `lib/i18n/zh/errors.py`
- Modify: `lib/i18n/en/errors.py`

`discovery_failed` 已存在并被 `_run_discover` 复用，仅需新增"无 API key"这一种业务错误 key（路由层校验）。

- [ ] **Step 1：在 `lib/i18n/zh/errors.py` 找到合适位置追加**

```python
"anthropic_discovery_no_key": "未配置 API Key，无法发现模型",
```

- [ ] **Step 2：在 `lib/i18n/en/errors.py` 同位置追加**

```python
"anthropic_discovery_no_key": "API Key not configured, cannot discover models",
```

- [ ] **Step 3：跑 i18n 一致性测试**

Run: `uv run python -m pytest tests/test_i18n_consistency.py -v`
Expected: PASS

- [ ] **Step 4：lint + 提交**

```bash
uv run ruff check lib/i18n/zh/errors.py lib/i18n/en/errors.py
uv run ruff format lib/i18n/zh/errors.py lib/i18n/en/errors.py
git add lib/i18n/zh/errors.py lib/i18n/en/errors.py
git commit -m "feat(i18n): add anthropic_discovery_no_key"
```

---

## Task 4：`POST /custom-providers/discover-anthropic` 路由

**Files:**
- Modify: `server/routers/custom_providers.py`
- Modify: `tests/test_custom_providers_api.py`

- [ ] **Step 1：在 `tests/test_custom_providers_api.py` 末尾追加测试类**

```python
# ---------------------------------------------------------------------------
# Anthropic discovery (智能体配置专用)
# ---------------------------------------------------------------------------


class TestDiscoverAnthropic:
    def test_explicit_credentials(self, client: TestClient):
        """显式传入 base_url + api_key，调用 _run_discover('anthropic', ...)。"""
        from unittest.mock import AsyncMock, patch

        mock_models = [
            {"model_id": "claude-x", "display_name": "X", "endpoint": "", "is_default": False, "is_enabled": True}
        ]
        with patch("server.routers.custom_providers._run_discover", new=AsyncMock()) as mock_run:
            from server.routers.custom_providers import DiscoverResponse
            mock_run.return_value = DiscoverResponse(models=mock_models)

            resp = client.post(
                "/api/v1/custom-providers/discover-anthropic",
                json={"base_url": "https://example.com", "api_key": "sk-ant"},
            )

        assert resp.status_code == 200
        assert [m["model_id"] for m in resp.json()["models"]] == ["claude-x"]
        # 调用参数：discovery_format=anthropic，凭据透传
        kwargs = mock_run.call_args.args
        assert kwargs[0] == "anthropic"
        assert kwargs[1] == "https://example.com"
        assert kwargs[2] == "sk-ant"

    def test_falls_back_to_stored_api_key(self, client: TestClient, session):
        """请求未带 api_key 时，从 anthropic_api_key 设置 fallback。"""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from lib.config.service import ConfigService

        async def _seed():
            svc = ConfigService(session)
            await svc.set_setting("anthropic_api_key", "sk-stored")
            await svc.set_setting("anthropic_base_url", "https://stored.example")
            await session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        with patch("server.routers.custom_providers._run_discover", new=AsyncMock()) as mock_run:
            from server.routers.custom_providers import DiscoverResponse
            mock_run.return_value = DiscoverResponse(models=[])

            resp = client.post("/api/v1/custom-providers/discover-anthropic", json={})

        assert resp.status_code == 200
        kwargs = mock_run.call_args.args
        assert kwargs[1] == "https://stored.example"
        assert kwargs[2] == "sk-stored"

    def test_returns_400_when_no_key_anywhere(self, client: TestClient):
        """请求未带 api_key 且 DB 也没有 → 400。"""
        resp = client.post("/api/v1/custom-providers/discover-anthropic", json={})
        assert resp.status_code == 400
        # i18n 默认 zh
        assert "API Key" in resp.json()["detail"]
```

- [ ] **Step 2：跑测试验证失败**

Run: `uv run python -m pytest tests/test_custom_providers_api.py::TestDiscoverAnthropic -v`
Expected: FAIL — 路由不存在 (404)

- [ ] **Step 3：在 `server/routers/custom_providers.py` 增加路由**

在文件顶部 import 区追加（如已存在则跳过）：

```python
from server.dependencies import get_config_service
from lib.config.service import ConfigService
```

在 `class DiscoverResponse(BaseModel):` 定义之后增加请求模型：

```python
class DiscoverAnthropicRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
```

在 `discover_models_endpoint` 之后插入新路由（约第 487 行）：

```python
@router.post("/discover-anthropic", response_model=DiscoverResponse)
async def discover_anthropic_models_endpoint(
    body: DiscoverAnthropicRequest,
    _user: CurrentUser,
    _t: Translator,
    svc: Annotated[ConfigService, Depends(get_config_service)],
):
    """Anthropic 协议模型发现：智能体配置专用。

    凭据缺失时 fallback 到 system settings 里已存的
    anthropic_base_url / anthropic_api_key。
    """
    api_key = body.api_key
    if not api_key:
        api_key = (await svc.get_setting("anthropic_api_key", "")).strip()
    if not api_key:
        raise HTTPException(status_code=400, detail=_t("anthropic_discovery_no_key"))

    base_url = body.base_url
    if base_url is None:
        base_url = (await svc.get_setting("anthropic_base_url", "")).strip() or None

    return await _run_discover("anthropic", base_url, api_key, _t)
```

- [ ] **Step 4：跑测试验证通过**

Run: `uv run python -m pytest tests/test_custom_providers_api.py::TestDiscoverAnthropic -v`
Expected: PASS（3 个用例）

无回归：
Run: `uv run python -m pytest tests/test_custom_providers_api.py -v`
Expected: PASS

- [ ] **Step 5：lint + 提交**

```bash
uv run ruff check server/routers/custom_providers.py tests/test_custom_providers_api.py
uv run ruff format server/routers/custom_providers.py tests/test_custom_providers_api.py
git add server/routers/custom_providers.py tests/test_custom_providers_api.py
git commit -m "feat(api): add POST /custom-providers/discover-anthropic"
```

---

## Task 5：`GET /custom-providers/{id}/credentials` 路由

**Files:**
- Modify: `server/routers/custom_providers.py`
- Modify: `tests/test_custom_providers_api.py`

- [ ] **Step 1：在 `tests/test_custom_providers_api.py` 末尾追加测试类**

```python
class TestGetProviderCredentials:
    def test_returns_plaintext(self, client: TestClient):
        """正常路径返回明文 base_url + api_key。"""
        # 先创建 provider
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "OneAPI",
                "discovery_format": "openai",
                "base_url": "https://oneapi.example.com",
                "api_key": "sk-secret",
                "models": [],
            },
        )
        assert create_resp.status_code == 201
        provider_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/custom-providers/{provider_id}/credentials")
        assert resp.status_code == 200
        body = resp.json()
        assert body["base_url"] == "https://oneapi.example.com"
        assert body["api_key"] == "sk-secret"

    def test_returns_404_for_unknown_provider(self, client: TestClient):
        resp = client.get("/api/v1/custom-providers/99999/credentials")
        assert resp.status_code == 404
```

- [ ] **Step 2：跑测试验证失败**

Run: `uv run python -m pytest tests/test_custom_providers_api.py::TestGetProviderCredentials -v`
Expected: FAIL — 路由 404

- [ ] **Step 3：在 `server/routers/custom_providers.py` 增加路由**

在 `class DiscoverAnthropicRequest` 之后增加 response 模型：

```python
class CredentialsResponse(BaseModel):
    base_url: str
    api_key: str
```

在 `get_provider` 路由之后（约第 320 行）插入：

```python
@router.get("/{provider_id}/credentials", response_model=CredentialsResponse)
async def get_provider_credentials(
    provider_id: int,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """返回明文 base_url + api_key，供智能体配置导入复用。

    仅 CurrentUser 鉴权，与现有 PATCH 接口对齐；日志不打印 body。
    多用户场景需重新评估细粒度授权。
    """
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    return CredentialsResponse(
        base_url=provider.base_url or "",
        api_key=provider.api_key or "",
    )
```

- [ ] **Step 4：跑测试验证通过**

Run: `uv run python -m pytest tests/test_custom_providers_api.py::TestGetProviderCredentials -v`
Expected: PASS

无回归：
Run: `uv run python -m pytest tests/test_custom_providers_api.py -v`
Expected: PASS

- [ ] **Step 5：lint + 提交**

```bash
uv run ruff check server/routers/custom_providers.py tests/test_custom_providers_api.py
uv run ruff format server/routers/custom_providers.py tests/test_custom_providers_api.py
git add server/routers/custom_providers.py tests/test_custom_providers_api.py
git commit -m "feat(api): add GET /custom-providers/{id}/credentials"
```

---

## Task 6：前端 API client 方法

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/types/custom-provider.ts`

- [ ] **Step 1：在 `frontend/src/types/custom-provider.ts` 末尾追加类型**

```typescript
export interface CustomProviderCredentials {
  base_url: string;
  api_key: string;
}

export interface AnthropicDiscoverRequest {
  base_url?: string;
  api_key?: string;
}

export interface AnthropicDiscoverResponse {
  models: Array<{
    model_id: string;
    display_name: string;
    endpoint: string;
    is_default: boolean;
    is_enabled: boolean;
  }>;
}
```

- [ ] **Step 2：在 `frontend/src/api.ts` 找到 `// ==================== 自定义供应商 API ====================` 区块，在 `testCustomConnectionById` 之后追加方法**

先在文件顶部 imports 加入新类型（如已 export *则跳过）：找到现有 `CustomProviderInfo` import 那一行，追加 `CustomProviderCredentials`、`AnthropicDiscoverRequest`、`AnthropicDiscoverResponse`。

然后追加方法：

```typescript
  static async getCustomProviderCredentials(id: number): Promise<CustomProviderCredentials> {
    return this.request(`/custom-providers/${id}/credentials`);
  }

  static async discoverAnthropicModels(
    data: AnthropicDiscoverRequest,
  ): Promise<AnthropicDiscoverResponse> {
    return this.request("/custom-providers/discover-anthropic", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }
```

- [ ] **Step 3：typecheck 通过**

Run: `cd frontend && pnpm check`
Expected: PASS（无类型错误）

- [ ] **Step 4：提交**

```bash
git add frontend/src/api.ts frontend/src/types/custom-provider.ts
git commit -m "feat(frontend): add anthropic discovery + credentials API client methods"
```

---

## Task 7：i18n keys

**Files:**
- Modify: `frontend/src/i18n/zh/dashboard.ts`
- Modify: `frontend/src/i18n/en/dashboard.ts`

`discover_models` 已存在（"获取模型列表"/"Discover Models"），直接复用。仅新增以下 4 个 key：

- [ ] **Step 1：在 `frontend/src/i18n/zh/dashboard.ts` 适当位置（按字母/语义分组）追加**

```typescript
'import_from_provider': '从供应商导入',
'import_no_providers': '暂无可导入的自定义供应商',
'import_provider_success': '已导入 {{name}} 的凭据',
'discover_no_models': '未发现可用模型',
'discover_needs_key': '请先填入 API Key 再获取模型',
```

- [ ] **Step 2：在 `frontend/src/i18n/en/dashboard.ts` 同位置追加**

```typescript
'import_from_provider': 'Import from provider',
'import_no_providers': 'No custom providers to import',
'import_provider_success': 'Imported credentials from {{name}}',
'discover_no_models': 'No models found',
'discover_needs_key': 'Please fill in API Key before discovering models',
```

- [ ] **Step 3：typecheck + i18n 一致性**

Run: `cd frontend && pnpm check`
Expected: PASS

后端 i18n 一致性（如有 zh/en 漂移会失败）：
Run: `uv run python -m pytest tests/test_i18n_consistency.py -v`
Expected: PASS

- [ ] **Step 4：提交**

```bash
git add frontend/src/i18n/zh/dashboard.ts frontend/src/i18n/en/dashboard.ts
git commit -m "feat(i18n): add provider import + model discovery keys"
```

---

## Task 8：AgentConfigTab — 导入按钮

**Files:**
- Modify: `frontend/src/components/pages/AgentConfigTab.tsx`
- Modify: `frontend/src/components/pages/AgentConfigTab.test.tsx`

- [ ] **Step 1：在 `AgentConfigTab.test.tsx` 末尾追加导入按钮测试**

（先快速检查现有测试文件结构）

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n"; // 调整为现有 test setup 路径
import { AgentConfigTab } from "./AgentConfigTab";
import { API } from "@/api";

vi.mock("@/api");

describe("AgentConfigTab — provider import", () => {
  beforeEach(() => {
    vi.mocked(API.getSystemConfig).mockResolvedValue({
      settings: {
        anthropic_api_key: { is_set: false, masked: null },
        anthropic_base_url: "",
        anthropic_model: "",
        anthropic_default_haiku_model: "",
        anthropic_default_opus_model: "",
        anthropic_default_sonnet_model: "",
        claude_code_subagent_model: "",
        // ...其它字段最小化
      },
      options: { video_backends: [], image_backends: [], text_backends: [], provider_names: {} },
    } as any);
    vi.mocked(API.listCustomProviders).mockResolvedValue({
      providers: [
        { id: 1, display_name: "OneAPI", discovery_format: "openai", base_url: "https://oneapi.example.com", masked_api_key: "sk-***", models: [] } as any,
      ],
    });
    vi.mocked(API.getCustomProviderCredentials).mockResolvedValue({
      base_url: "https://oneapi.example.com",
      api_key: "sk-secret",
    });
  });

  it("populates draft fields when provider is imported", async () => {
    render(
      <I18nextProvider i18n={i18n}>
        <AgentConfigTab visible />
      </I18nextProvider>
    );

    const user = userEvent.setup();
    await waitFor(() => screen.getByRole("button", { name: /从供应商导入|Import from provider/i }));

    await user.click(screen.getByRole("button", { name: /从供应商导入|Import from provider/i }));
    await user.click(await screen.findByText("OneAPI"));

    await waitFor(() => {
      const baseUrlInput = screen.getByLabelText(/api_base_url|API Base URL/i) as HTMLInputElement;
      expect(baseUrlInput.value).toBe("https://oneapi.example.com");
    });

    const apiKeyInput = screen.getByLabelText(/anthropic_api_key|API Key/i) as HTMLInputElement;
    expect(apiKeyInput.value).toBe("sk-secret");
  });
});
```

- [ ] **Step 2：跑测试验证失败**

Run: `cd frontend && pnpm vitest run src/components/pages/AgentConfigTab.test.tsx -t "provider import"`
Expected: FAIL — 按钮不存在

- [ ] **Step 3：实现导入按钮 + 弹下拉**

在 `AgentConfigTab.tsx` 顶部 import 区追加：

```typescript
import { Download, Search } from "lucide-react";
import type { CustomProviderInfo } from "@/types/custom-provider";
```

在组件 state 区（约 174 行附近）增加：

```typescript
const [providers, setProviders] = useState<CustomProviderInfo[]>([]);
const [importPickerOpen, setImportPickerOpen] = useState(false);
const [importing, setImporting] = useState(false);
```

在 `useEffect(() => { void load(); }, [load])` 之后追加：

```typescript
useEffect(() => {
  let cancelled = false;
  void (async () => {
    try {
      const res = await API.listCustomProviders();
      if (!cancelled) {
        // 过滤无凭据的（masked_api_key 为空字符串 / null 视为未配置）
        setProviders(res.providers.filter((p) => p.masked_api_key));
      }
    } catch {
      // 静默：导入是可选功能，不打断主流程
    }
  })();
  return () => {
    cancelled = true;
  };
}, []);
```

增加导入处理函数（在 `handleClearField` 旁边）：

```typescript
const handleImportProvider = useCallback(
  async (provider: CustomProviderInfo) => {
    setImporting(true);
    try {
      const cred = await API.getCustomProviderCredentials(provider.id);
      setDraft((prev) => ({
        ...prev,
        anthropicKey: cred.api_key,
        anthropicBaseUrl: cred.base_url,
      }));
      useAppStore
        .getState()
        .pushToast(t("import_provider_success", { name: provider.display_name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setImporting(false);
      setImportPickerOpen(false);
    }
  },
  [t],
);
```

在 API Key 卡片 SectionHeading 那行（约 312 行）之后插入按钮 + 弹层，例如在 `<SectionHeading>` 后包一个 flex container，把按钮放在右侧：

```tsx
<div className="mb-4 flex items-start justify-between">
  <SectionHeading
    title={t("api_credentials")}
    description={t("anthropic_key_required_desc")}
  />
  <div className="relative">
    <button
      type="button"
      onClick={() => setImportPickerOpen((v) => !v)}
      disabled={importing || saving}
      className="inline-flex items-center gap-1.5 rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-300 hover:border-gray-600 hover:bg-gray-800/50 disabled:opacity-50"
    >
      {importing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
      {t("import_from_provider")}
    </button>
    {importPickerOpen && (
      <div className="absolute right-0 top-full z-10 mt-1 w-64 rounded-lg border border-gray-700 bg-gray-900 py-1 shadow-lg">
        {providers.length === 0 ? (
          <div className="px-3 py-2 text-xs text-gray-500">{t("import_no_providers")}</div>
        ) : (
          providers.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => void handleImportProvider(p)}
              className="block w-full truncate px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800"
            >
              {p.display_name}
            </button>
          ))
        )}
      </div>
    )}
  </div>
</div>
```

注意：`SectionHeading` 原本带 `mb-4`；包到 flex container 后把 `mb-4` 移到外层 div，避免双倍间距。

- [ ] **Step 4：跑测试验证通过**

Run: `cd frontend && pnpm vitest run src/components/pages/AgentConfigTab.test.tsx -t "provider import"`
Expected: PASS

- [ ] **Step 5：提交**

```bash
git add frontend/src/components/pages/AgentConfigTab.tsx frontend/src/components/pages/AgentConfigTab.test.tsx
git commit -m "feat(agent-config): add import-from-provider button"
```

---

## Task 9：AgentConfigTab — 获取模型按钮 + datalist

**Files:**
- Modify: `frontend/src/components/pages/AgentConfigTab.tsx`
- Modify: `frontend/src/components/pages/AgentConfigTab.test.tsx`

- [ ] **Step 1：在 `AgentConfigTab.test.tsx` 追加发现模型测试**

```typescript
describe("AgentConfigTab — discover models", () => {
  beforeEach(() => {
    vi.mocked(API.getSystemConfig).mockResolvedValue({
      settings: {
        anthropic_api_key: { is_set: true, masked: "sk-ant-***" },
        anthropic_base_url: "https://example.com",
        anthropic_model: "",
        anthropic_default_haiku_model: "",
        anthropic_default_opus_model: "",
        anthropic_default_sonnet_model: "",
        claude_code_subagent_model: "",
      },
      options: { video_backends: [], image_backends: [], text_backends: [], provider_names: {} },
    } as any);
    vi.mocked(API.listCustomProviders).mockResolvedValue({ providers: [] });
    vi.mocked(API.discoverAnthropicModels).mockResolvedValue({
      models: [
        { model_id: "claude-haiku-4-5", display_name: "Haiku 4.5", endpoint: "", is_default: false, is_enabled: true },
        { model_id: "claude-opus-4-7", display_name: "Opus 4.7", endpoint: "", is_default: false, is_enabled: true },
      ],
    });
  });

  it("renders datalist options after clicking discover", async () => {
    render(
      <I18nextProvider i18n={i18n}>
        <AgentConfigTab visible />
      </I18nextProvider>,
    );

    const user = userEvent.setup();
    const btn = await screen.findByRole("button", { name: /获取模型|Discover Models/i });
    await user.click(btn);

    await waitFor(() => {
      const options = document.querySelectorAll("datalist#anthropic-models option");
      expect(options.length).toBe(2);
      expect(options[0].getAttribute("value")).toBe("claude-haiku-4-5");
    });
  });

  it("sends undefined api_key when draft is empty (lets backend fallback)", async () => {
    render(
      <I18nextProvider i18n={i18n}>
        <AgentConfigTab visible />
      </I18nextProvider>,
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /获取模型|Discover Models/i }));

    await waitFor(() => {
      expect(API.discoverAnthropicModels).toHaveBeenCalledWith({
        base_url: "https://example.com",
        api_key: undefined,
      });
    });
  });

  it("shows error when discovery fails", async () => {
    vi.mocked(API.discoverAnthropicModels).mockRejectedValueOnce(new Error("boom"));

    render(
      <I18nextProvider i18n={i18n}>
        <AgentConfigTab visible />
      </I18nextProvider>,
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /获取模型|Discover Models/i }));

    await waitFor(() => {
      expect(screen.getByText(/boom/)).toBeInTheDocument();
    });
  });
});
```

- [ ] **Step 2：跑测试验证失败**

Run: `cd frontend && pnpm vitest run src/components/pages/AgentConfigTab.test.tsx -t "discover models"`
Expected: FAIL — 按钮不存在

- [ ] **Step 3：实现获取模型按钮 + handler + datalist**

在组件 state 区追加：

```typescript
const [modelCandidates, setModelCandidates] = useState<string[]>([]);
const [discoverState, setDiscoverState] = useState<"idle" | "loading" | "error">("idle");
const [discoverError, setDiscoverError] = useState<string | null>(null);
```

在 `handleImportProvider` 旁边新增：

```typescript
const handleDiscoverModels = useCallback(async () => {
  const apiKey = draft.anthropicKey.trim() || undefined;
  const baseUrl = draft.anthropicBaseUrl.trim() || undefined;

  setDiscoverState("loading");
  setDiscoverError(null);
  try {
    const res = await API.discoverAnthropicModels({ base_url: baseUrl, api_key: apiKey });
    setModelCandidates(res.models.map((m) => m.model_id));
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

修改 Model Configuration section（约 449 行 `{/* Section 2: Model Configuration */}`），把 SectionHeading 包到 flex 容器并加获取模型按钮：

```tsx
<div className="mb-4 flex items-start justify-between">
  <SectionHeading title={t("model_config")} description={t("model_config_desc")} />
  <div>
    <button
      type="button"
      onClick={() => void handleDiscoverModels()}
      disabled={discoverState === "loading"}
      className="inline-flex items-center gap-1.5 rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-300 hover:border-gray-600 hover:bg-gray-800/50 disabled:opacity-50"
    >
      {discoverState === "loading" ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <Search className="h-3.5 w-3.5" />
      )}
      {t("discover_models")}
    </button>
    {discoverError && (
      <p className="mt-1 text-right text-xs text-rose-400">{discoverError}</p>
    )}
  </div>
</div>
```

在 component return 顶层（紧接 outer `<div>` 之后）插入 datalist：

```tsx
<datalist id="anthropic-models">
  {modelCandidates.map((m) => (
    <option key={m} value={m} />
  ))}
</datalist>
```

为 5 个 model input 都加 `list="anthropic-models"`：
- 主 model input（约 488 行 `id="agent-model"`）：加 `list="anthropic-models"`
- routing 4 个 input（在 `MODEL_ROUTING_FIELDS.map` 内的 `<input>` 约 566 行）：加 `list="anthropic-models"`

- [ ] **Step 4：跑测试验证通过**

Run: `cd frontend && pnpm vitest run src/components/pages/AgentConfigTab.test.tsx -t "discover models"`
Expected: PASS（3 个用例）

无回归：
Run: `cd frontend && pnpm vitest run src/components/pages/AgentConfigTab.test.tsx`
Expected: PASS

- [ ] **Step 5：build + 提交**

```bash
cd frontend && pnpm check
git add frontend/src/components/pages/AgentConfigTab.tsx frontend/src/components/pages/AgentConfigTab.test.tsx
git commit -m "feat(agent-config): add discover-models button + datalist"
```

---

## Task 10：手工 UI 验证 + 文档收尾

- [ ] **Step 1：启动后端**

```bash
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241
```

- [ ] **Step 2：启动前端**

```bash
cd frontend && pnpm dev
```

- [ ] **Step 3：浏览器手工验证**

打开 `http://localhost:5173/settings`，进入「智能体」标签：

1. 在自定义供应商管理页面新建一个供应商（base_url 任意 + api_key 任意），用于导入测试
2. 回到智能体标签 → 点「从供应商导入」 → 下拉里出现刚建的 provider
3. 选中 → API Key + Base URL 字段被填充；toast 出现"已导入"
4. 点「获取模型」按钮：
   - 若是真实 anthropic key + base_url：datalist 出现模型列表，点 model input 看到自动补全
   - 若 base_url 错误：错误信息显示在按钮下方（不阻塞 UI）
5. 字段清空 + 已存有 anthropic_api_key 时点「获取模型」：仍能成功（验证 fallback）
6. 整个面板"保存"按钮工作正常，刷新页面后字段持久化

- [ ] **Step 4：跑全量测试**

```bash
uv run python -m pytest -q
cd frontend && pnpm check
```
Expected: 全绿

- [ ] **Step 5：补一个收尾 commit（如果手工测试中发现微调）**

如手工测试一切顺利，无需 commit；否则修复后：

```bash
git add -p
git commit -m "fix(agent-config): manual QA fixes"
```

---

## 自审

**Spec coverage**：
- ✅ ensure_anthropic_base_url → Task 1
- ✅ _discover_anthropic + dispatch → Task 2
- ✅ POST /custom-providers/discover-anthropic → Task 4
- ✅ GET /custom-providers/{id}/credentials → Task 5
- ✅ 4 个 i18n key（spec 5 个，但 `discover_models` 已存在复用）→ Task 7
- ✅ 导入按钮 → Task 8
- ✅ 获取模型按钮 + datalist → Task 9
- ✅ 5 个 model input 挂 datalist → Task 9 Step 3
- ✅ 手工 UI 验证 → Task 10

**Spec 偏差**：
- spec 计划新增 3 个 i18n error key（`anthropic_discovery_no_key` / `_http_error` / `_network_error`），实际只新增 1 个——`_run_discover` 已包揽 4xx/5xx/网络错误用 `discovery_failed` 兜底
- spec 计划新增 5 个前端 i18n key，实际只新增 4 个——`discover_models` 已存在复用

**Placeholder scan**：无 TBD/TODO/「类似 Task X」等模糊指代。所有代码块均给出完整可粘贴内容。

**Type consistency**：
- `CustomProviderCredentials.base_url/api_key` (Task 6) ↔ `CredentialsResponse` (Task 5) — 字段名一致
- `AnthropicDiscoverResponse.models[i].model_id` (Task 6) ↔ `_discover_anthropic` 返回字典里的 `"model_id"` (Task 2) — 一致
- `discoverAnthropicModels({ base_url, api_key })` (Task 6) ↔ `DiscoverAnthropicRequest(base_url, api_key)` (Task 4) — 一致
