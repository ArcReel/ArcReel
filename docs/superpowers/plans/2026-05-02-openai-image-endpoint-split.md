# OpenAI 图像端点按能力拆分 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `OpenAIImageBackend` 单条 endpoint 拆成 `openai-images`（通配）/ `openai-images-generations`（仅 T2I）/ `openai-images-edits`（仅 I2I），并把"默认图像模型"语义从 media_type 维度细化到 image capability 维度（T2I / I2I），让只支持 `/v1/images/generations` 的中转模型也能正常生图。

**Architecture:** 在 `EndpointSpec` 上新增 `image_capabilities` 字段，`OpenAIImageBackend` 接收 `mode` 参数派生 `capabilities`，调用前在 `MediaGenerator` 层做 capability gating，不匹配时抛 `ImageCapabilityError(code, **params)`，由路由层用 `_t(code, params)` 渲染。系统/项目级"图像默认模型"配置从单字段拆为 `_t2i` / `_i2i` 两组，旧字段做 lazy 升级，setting key 用 alembic data migration。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2.x async、Alembic、React 19 + TypeScript + Vite、zustand、i18next、pytest、vitest。

**Spec:** `docs/superpowers/specs/2026-05-02-openai-image-endpoint-split-design.md`

---

## 文件结构

### 后端 — 新增

- `lib/image_backends/base.py` — 新增 `ImageCapabilityError(RuntimeError)`（已有文件，新增类）
- `alembic/versions/<rev>_split_default_image_backend_setting.py` — data migration
- `tests/test_media_generator_image_capability.py` — generator gating 测试

### 后端 — 修改

- `lib/custom_provider/endpoints.py` — `EndpointSpec` + `image_capabilities` + 新两条注册项 + helper
- `lib/image_backends/openai.py` — `OpenAIImageBackend` mode 化、capabilities 派生、删除旧 fallback、生成前 gating
- `lib/media_generator.py` — `generate_image_async` capability gating
- `lib/config/service.py` — `get_default_image_backend` 拆 t2i/i2i
- `lib/config/resolver.py` — `default_image_backend` 拆两个；旧 `image_provider` 字段 lazy 升级
- `lib/db/repositories/custom_provider_repo.py` — `get_default_model` 增 capability 维度变体（保留旧 media_type 变体仅供文本/视频）
- `lib/i18n/zh/errors.py` / `lib/i18n/en/errors.py` — 4 条新 key
- `server/routers/custom_providers.py` — `EndpointDescriptor` 增 `image_capabilities`、`_check_unique_defaults` 重写为按能力交集
- `server/routers/generate.py` — `project_image_backend` 改读两组字段
- `server/services/generation_tasks.py` — `_snapshot_image_backend` 写两份；`_resolve_effective_image_backend` 返回 `(t2i_pair, i2i_pair)`；调用点按 ref 图选用
- `server/services/cost_estimation.py` — 适配新两组字段

### 前端 — 新增

- `frontend/src/components/shared/ImageModelDualSelect.tsx` — 单/双下拉组件
- `frontend/src/components/shared/ImageModelDualSelect.test.tsx` — 组件测试

### 前端 — 修改

- `frontend/src/types/custom-provider.ts` — `EndpointDescriptor` 增 `image_capabilities`
- `frontend/src/stores/endpoint-catalog-store.ts` — 派生 `endpointToImageCapabilities` map
- `frontend/src/components/pages/settings/customProviderHelpers.ts` — `toggleDefaultReducer` 升级
- `frontend/src/components/pages/settings/EndpointSelect.tsx` — 选项后方追加 capability 标签
- `frontend/src/components/shared/ModelConfigSection.tsx` — 替换 image 单选为 `ImageModelDualSelect`
- `frontend/src/i18n/zh/dashboard.ts` / `frontend/src/i18n/en/dashboard.ts` — 新增显示名 / capability 标签 / dual-select label
- `frontend/src/i18n/zh/errors.ts` / `frontend/src/i18n/en/errors.ts` — 4 条错误 key
- 测试文件：
  - `frontend/src/stores/endpoint-catalog-store.test.ts`
  - `frontend/src/components/pages/settings/customProviderHelpers.test.ts`

---

## Task 1: 在 base.py 引入 `ImageCapabilityError`

**Files:**
- Modify: `lib/image_backends/base.py`
- Test: `tests/test_image_capability_error.py`（新增）

- [ ] **Step 1: 写失败测试**

新增文件 `tests/test_image_capability_error.py`：

```python
"""ImageCapabilityError 携带稳定 code + 上下文 params。"""

from lib.image_backends.base import ImageCapabilityError


def test_carries_code_and_params():
    err = ImageCapabilityError("image_endpoint_mismatch_no_i2i", model="dall-e-3")
    assert err.code == "image_endpoint_mismatch_no_i2i"
    assert err.params == {"model": "dall-e-3"}
    assert isinstance(err, RuntimeError)


def test_str_is_code_for_logging():
    err = ImageCapabilityError("image_capability_missing_t2i", provider="x", model="y")
    assert str(err) == "image_capability_missing_t2i"
```

- [ ] **Step 2: 跑测试看失败**

```bash
uv run pytest tests/test_image_capability_error.py -v
```

预期：`ImportError: cannot import name 'ImageCapabilityError'`。

- [ ] **Step 3: 在 base.py 末尾追加类定义**

`lib/image_backends/base.py` 末尾：

```python
class ImageCapabilityError(RuntimeError):
    """图像后端能力不匹配（endpoint mismatch / generator gating 共用）。

    不携带本地化字符串，只带稳定 code + 上下文 params；
    路由层捕获后用 _t(code, **params) 渲染。
    """

    def __init__(self, code: str, **params) -> None:
        self.code = code
        self.params = params
        super().__init__(code)
```

- [ ] **Step 4: 跑测试看通过**

```bash
uv run pytest tests/test_image_capability_error.py -v
```

预期：2 passed。

- [ ] **Step 5: lint + commit**

```bash
uv run ruff check lib/image_backends/base.py tests/test_image_capability_error.py
uv run ruff format lib/image_backends/base.py tests/test_image_capability_error.py
git add lib/image_backends/base.py tests/test_image_capability_error.py
git commit -m "feat(image-backends): 引入 ImageCapabilityError"
```

---

## Task 2: 在 EndpointSpec 加 `image_capabilities` 字段

**Files:**
- Modify: `lib/custom_provider/endpoints.py`
- Test: `tests/test_custom_provider_endpoints.py`

> 此任务**只**给 EndpointSpec 加字段并把现有两条 image entry（`openai-images` / `gemini-image`）的 `image_capabilities` 填上 `{T2I, I2I}`，**不**新增两条单能力 entry（留到 Task 4）。

- [ ] **Step 1: 写失败测试**

`tests/test_custom_provider_endpoints.py` 末尾追加：

```python
def test_existing_image_endpoints_have_full_capabilities():
    """EndpointSpec 新增 image_capabilities 字段；已存在的 image entry 默认填两个能力。"""
    from lib.custom_provider.endpoints import (
        ENDPOINT_REGISTRY,
        endpoint_to_image_capabilities,
    )
    from lib.image_backends.base import ImageCapability

    full = frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE})
    assert ENDPOINT_REGISTRY["openai-images"].image_capabilities == full
    assert ENDPOINT_REGISTRY["gemini-image"].image_capabilities == full
    assert ENDPOINT_REGISTRY["openai-chat"].image_capabilities is None
    assert endpoint_to_image_capabilities("openai-images") == full

    import pytest
    with pytest.raises(ValueError):
        endpoint_to_image_capabilities("openai-chat")
```

- [ ] **Step 2: 跑测试看失败**

```bash
uv run pytest tests/test_custom_provider_endpoints.py::test_existing_image_endpoints_have_full_capabilities -v
```

预期：`AttributeError` 或 `ImportError`。

- [ ] **Step 3: 修改 endpoints.py**

在 `lib/custom_provider/endpoints.py` 顶部 import 区添加：

```python
from lib.image_backends.base import ImageCapability
```

修改 `EndpointSpec`：

```python
@dataclass(frozen=True)
class EndpointSpec:
    key: str
    media_type: str
    family: str
    display_name_key: str
    request_method: str
    request_path_template: str
    image_capabilities: frozenset[ImageCapability] | None  # 新增；非 image 类置 None
    build_backend: Callable[[CustomProvider, str], CustomTextBackend | CustomImageBackend | CustomVideoBackend]
```

注册表里非 image 类全部加 `image_capabilities=None`，image 类填 `{T2I, I2I}`。例如：

```python
"openai-chat": EndpointSpec(
    ...
    image_capabilities=None,
    build_backend=_build_openai_chat,
),
"openai-images": EndpointSpec(
    ...
    image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
    build_backend=_build_openai_images,
),
"gemini-image": EndpointSpec(
    ...
    image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
    build_backend=_build_gemini_image,
),
```

新增 helper（在 `endpoint_to_media_type` 旁边）：

```python
def endpoint_to_image_capabilities(endpoint: str) -> frozenset[ImageCapability]:
    """返回 image 类 endpoint 的 capability 集合。非 image 类抛 ValueError。"""
    spec = get_endpoint_spec(endpoint)
    if spec.image_capabilities is None:
        raise ValueError(f"endpoint {endpoint!r} is not an image endpoint")
    return spec.image_capabilities
```

修改 `endpoint_spec_to_dict`，把 `image_capabilities` 序列化为 `list[str] | None`：

```python
def endpoint_spec_to_dict(spec: EndpointSpec) -> dict:
    data = asdict(spec)
    data.pop("build_backend", None)
    if spec.image_capabilities is not None:
        data["image_capabilities"] = sorted(c.value for c in spec.image_capabilities)
    else:
        data["image_capabilities"] = None
    return data
```

- [ ] **Step 4: 跑全套 endpoints 测试**

```bash
uv run pytest tests/test_custom_provider_endpoints.py -v
```

预期：原有测试 + 新测试全过。

- [ ] **Step 5: lint + commit**

```bash
uv run ruff check lib/custom_provider/endpoints.py tests/test_custom_provider_endpoints.py
uv run ruff format lib/custom_provider/endpoints.py tests/test_custom_provider_endpoints.py
git add lib/custom_provider/endpoints.py tests/test_custom_provider_endpoints.py
git commit -m "feat(custom-provider): EndpointSpec 增 image_capabilities 字段"
```

---

## Task 3: `OpenAIImageBackend` mode 化

**Files:**
- Modify: `lib/image_backends/openai.py`
- Test: `tests/test_openai_image_backend.py`

- [ ] **Step 1: 写失败测试（在文件末尾追加）**

`tests/test_openai_image_backend.py` 末尾追加：

```python
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from lib.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.image_backends.openai import OpenAIImageBackend


class TestModeCapabilities:
    def test_default_mode_is_both(self):
        with patch("lib.image_backends.openai.create_openai_client"):
            b = OpenAIImageBackend(api_key="x", model="m")
            assert ImageCapability.TEXT_TO_IMAGE in b.capabilities
            assert ImageCapability.IMAGE_TO_IMAGE in b.capabilities

    def test_generations_only_mode(self):
        with patch("lib.image_backends.openai.create_openai_client"):
            b = OpenAIImageBackend(api_key="x", model="m", mode="generations_only")
            assert b.capabilities == {ImageCapability.TEXT_TO_IMAGE}

    def test_edits_only_mode(self):
        with patch("lib.image_backends.openai.create_openai_client"):
            b = OpenAIImageBackend(api_key="x", model="m", mode="edits_only")
            assert b.capabilities == {ImageCapability.IMAGE_TO_IMAGE}


class TestModeGating:
    @pytest.mark.asyncio
    async def test_generations_only_with_refs_raises(self, tmp_path):
        ref = tmp_path / "r.png"
        ref.write_bytes(b"\x89PNG")
        with patch("lib.image_backends.openai.create_openai_client"):
            b = OpenAIImageBackend(api_key="x", model="m", mode="generations_only")
            req = ImageGenerationRequest(
                prompt="p",
                output_path=tmp_path / "o.png",
                reference_images=[ReferenceImage(path=str(ref))],
            )
            with pytest.raises(ImageCapabilityError) as excinfo:
                await b.generate(req)
            assert excinfo.value.code == "image_endpoint_mismatch_no_i2i"
            assert excinfo.value.params == {"model": "m"}

    @pytest.mark.asyncio
    async def test_edits_only_without_refs_raises(self, tmp_path):
        with patch("lib.image_backends.openai.create_openai_client"):
            b = OpenAIImageBackend(api_key="x", model="m", mode="edits_only")
            req = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png")
            with pytest.raises(ImageCapabilityError) as excinfo:
                await b.generate(req)
            assert excinfo.value.code == "image_endpoint_mismatch_no_t2i"
```

- [ ] **Step 2: 跑测试看失败**

```bash
uv run pytest tests/test_openai_image_backend.py::TestModeCapabilities tests/test_openai_image_backend.py::TestModeGating -v
```

预期：`TypeError: ... got an unexpected keyword argument 'mode'`。

- [ ] **Step 3: 改 `lib/image_backends/openai.py`**

顶部 import 处加：

```python
from typing import Literal

from lib.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,           # 新增
    ImageGenerationRequest,
    ImageGenerationResult,
    save_image_from_response_item,
)
```

修改类签名与 `__init__`：

```python
class OpenAIImageBackend:
    """OpenAI 图片生成后端，按 mode 决定支持 T2I / I2I / 两者。"""

    Mode = Literal["both", "generations_only", "edits_only"]

    _MODE_TO_CAPS: dict[str, set[ImageCapability]] = {
        "both": {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE},
        "generations_only": {ImageCapability.TEXT_TO_IMAGE},
        "edits_only": {ImageCapability.IMAGE_TO_IMAGE},
    }

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        mode: Mode = "both",
    ):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        self._model = model or DEFAULT_MODEL
        self._mode = mode
        self._capabilities = set(self._MODE_TO_CAPS[mode])
```

替换 `generate` 方法（保留 `with_retry_async` 装饰器）：

```python
    @with_retry_async(retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        has_refs = bool(request.reference_images)
        if has_refs and ImageCapability.IMAGE_TO_IMAGE not in self._capabilities:
            raise ImageCapabilityError("image_endpoint_mismatch_no_i2i", model=self._model)
        if not has_refs and ImageCapability.TEXT_TO_IMAGE not in self._capabilities:
            raise ImageCapabilityError("image_endpoint_mismatch_no_t2i", model=self._model)
        return await (self._generate_edit(request) if has_refs else self._generate_create(request))
```

删除 `_generate_edit` 内"所有 ref 图打不开 → 回退 T2I"的旧 fallback：

```python
    async def _generate_edit(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        refs = request.reference_images
        if len(refs) > _MAX_REFERENCE_IMAGES:
            logger.warning("参考图数量 %d 超过上限 %d，截断", len(refs), _MAX_REFERENCE_IMAGES)
            refs = refs[:_MAX_REFERENCE_IMAGES]

        def _open_refs() -> tuple[ExitStack, list]:
            stack = ExitStack()
            try:
                files = []
                for ref in refs:
                    ref_path = Path(ref.path)
                    try:
                        files.append(stack.enter_context(open(ref_path, "rb")))
                    except FileNotFoundError:
                        logger.warning("参考图不存在，跳过: %s", ref_path)
                return stack.pop_all(), files
            except BaseException:
                stack.close()
                raise

        stack, image_files = await asyncio.to_thread(_open_refs)
        try:
            if not image_files:
                # 旧版会回退到 T2I；新语义下若所有 ref 图都打不开，让 SDK 抛上去
                # （等价于用户提交了 i2i 请求但没有有效素材，应该是错误而非降级）
                raise ImageCapabilityError(
                    "image_endpoint_mismatch_no_i2i",
                    model=self._model,
                    detail="all reference images failed to open",
                )
            response = await self._client.images.edit(
                model=self._model,
                image=image_files,
                prompt=request.prompt,
            )
        finally:
            stack.close()
        return await self._save_and_return(response, request)
```

- [ ] **Step 4: 跑新测试**

```bash
uv run pytest tests/test_openai_image_backend.py::TestModeCapabilities tests/test_openai_image_backend.py::TestModeGating -v
```

预期：5 passed。

- [ ] **Step 5: 跑全文件回归**

```bash
uv run pytest tests/test_openai_image_backend.py -v
```

预期：原有 mode=both 行为相关测试全过。**若**有测试覆盖"所有 ref 图打不开 → 回退 T2I"的旧行为，把它改写为"抛 `ImageCapabilityError(code='image_endpoint_mismatch_no_i2i')`"。

- [ ] **Step 6: lint + commit**

```bash
uv run ruff check lib/image_backends/openai.py tests/test_openai_image_backend.py
uv run ruff format lib/image_backends/openai.py tests/test_openai_image_backend.py
git add lib/image_backends/openai.py tests/test_openai_image_backend.py
git commit -m "feat(image-backends): OpenAIImageBackend 引入 mode 与 capability gating"
```

---

## Task 4: ENDPOINT_REGISTRY 新增 `openai-images-generations` / `openai-images-edits`

**Files:**
- Modify: `lib/custom_provider/endpoints.py`
- Modify: `frontend/src/i18n/zh/dashboard.ts`、`frontend/src/i18n/en/dashboard.ts`（仅追加 2 条 i18n key，前端组件下个 phase 用）
- Test: `tests/test_custom_provider_endpoints.py`、`tests/test_custom_provider_factory.py`

- [ ] **Step 1: 写失败测试**

`tests/test_custom_provider_endpoints.py`：

```python
def test_image_endpoint_registry_has_three_entries():
    from lib.custom_provider.endpoints import ENDPOINT_KEYS_BY_MEDIA_TYPE

    image_keys = set(ENDPOINT_KEYS_BY_MEDIA_TYPE["image"])
    assert image_keys == {"openai-images", "openai-images-generations", "openai-images-edits", "gemini-image"}


def test_split_endpoints_have_single_capability():
    from lib.custom_provider.endpoints import endpoint_to_image_capabilities
    from lib.image_backends.base import ImageCapability

    assert endpoint_to_image_capabilities("openai-images-generations") == frozenset(
        {ImageCapability.TEXT_TO_IMAGE}
    )
    assert endpoint_to_image_capabilities("openai-images-edits") == frozenset(
        {ImageCapability.IMAGE_TO_IMAGE}
    )
```

`tests/test_custom_provider_factory.py`（追加测试，参考已有的 `test_openai_images`）：

```python
def test_openai_images_generations_factory(self, mock_cls=None):
    from unittest.mock import patch
    from lib.custom_provider.factory import create_custom_backend
    from lib.db.models.custom_provider import CustomProvider

    provider = CustomProvider(id=1, display_name="x", discovery_format="openai", base_url="https://api.example.com", api_key="k")
    with patch("lib.image_backends.openai.create_openai_client"):
        wrapper = create_custom_backend(provider=provider, model_id="dall-e-3", endpoint="openai-images-generations")
    delegate = wrapper._delegate
    assert delegate._mode == "generations_only"
    from lib.image_backends.base import ImageCapability
    assert delegate.capabilities == {ImageCapability.TEXT_TO_IMAGE}


def test_openai_images_edits_factory(self):
    from unittest.mock import patch
    from lib.custom_provider.factory import create_custom_backend
    from lib.db.models.custom_provider import CustomProvider

    provider = CustomProvider(id=1, display_name="x", discovery_format="openai", base_url="https://api.example.com", api_key="k")
    with patch("lib.image_backends.openai.create_openai_client"):
        wrapper = create_custom_backend(provider=provider, model_id="dall-e-3", endpoint="openai-images-edits")
    delegate = wrapper._delegate
    assert delegate._mode == "edits_only"
    from lib.image_backends.base import ImageCapability
    assert delegate.capabilities == {ImageCapability.IMAGE_TO_IMAGE}
```

> 这两个 test 函数若已存在 `class TestCustomProviderFactory:`，把它们作为方法加进去（`self` 参数即可）。否则放在模块顶层。

- [ ] **Step 2: 跑测试看失败**

```bash
uv run pytest tests/test_custom_provider_endpoints.py tests/test_custom_provider_factory.py -v
```

预期：新加的几个失败（KeyError / unknown endpoint）。

- [ ] **Step 3: 修改 endpoints.py，新增两条 build_backend 闭包**

在 `lib/custom_provider/endpoints.py` 现有 `_build_openai_images` 旁边加：

```python
def _build_openai_images_generations(provider, model_id: str) -> CustomImageBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIImageBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        mode="generations_only",
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openai_images_edits(provider, model_id: str) -> CustomImageBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIImageBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        mode="edits_only",
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)
```

`ENDPOINT_REGISTRY` 在 `"openai-images"` 下方插入：

```python
"openai-images-generations": EndpointSpec(
    key="openai-images-generations",
    media_type="image",
    family="openai",
    display_name_key="endpoint_openai_images_generations_display",
    request_method="POST",
    request_path_template="/v1/images/generations",
    image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE}),
    build_backend=_build_openai_images_generations,
),
"openai-images-edits": EndpointSpec(
    key="openai-images-edits",
    media_type="image",
    family="openai",
    display_name_key="endpoint_openai_images_edits_display",
    request_method="POST",
    request_path_template="/v1/images/edits",
    image_capabilities=frozenset({ImageCapability.IMAGE_TO_IMAGE}),
    build_backend=_build_openai_images_edits,
),
```

- [ ] **Step 4: 在前端 i18n 加显示名（zh/en）**

`frontend/src/i18n/zh/dashboard.ts` 在 `endpoint_openai_images_display` 旁加：

```typescript
'endpoint_openai_images_display': 'OpenAI Images API',
'endpoint_openai_images_generations_display': 'OpenAI Images（仅文生图）',
'endpoint_openai_images_edits_display': 'OpenAI Images（仅图生图）',
```

`frontend/src/i18n/en/dashboard.ts` 同位置：

```typescript
'endpoint_openai_images_display': 'OpenAI Images API',
'endpoint_openai_images_generations_display': 'OpenAI Images (T2I only)',
'endpoint_openai_images_edits_display': 'OpenAI Images (I2I only)',
```

- [ ] **Step 5: 跑后端测试**

```bash
uv run pytest tests/test_custom_provider_endpoints.py tests/test_custom_provider_factory.py -v
```

预期：全过。

- [ ] **Step 6: 跑前端 i18n 一致性测试**

```bash
uv run pytest tests/test_i18n_consistency.py -v
```

预期：通过（zh/en key 数一致）。

- [ ] **Step 7: lint + commit**

```bash
uv run ruff check lib/custom_provider/endpoints.py tests/test_custom_provider_endpoints.py tests/test_custom_provider_factory.py
uv run ruff format lib/custom_provider/endpoints.py tests/test_custom_provider_endpoints.py tests/test_custom_provider_factory.py
git add lib/custom_provider/endpoints.py tests/test_custom_provider_endpoints.py tests/test_custom_provider_factory.py frontend/src/i18n/zh/dashboard.ts frontend/src/i18n/en/dashboard.ts
git commit -m "feat(custom-provider): 新增 openai-images-generations / -edits 两条 endpoint"
```

---

## Task 5: GET `/custom-providers/endpoints` 暴露 `image_capabilities`

**Files:**
- Modify: `server/routers/custom_providers.py`
- Test: `tests/test_custom_providers_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_custom_providers_api.py`（找已有的 endpoints catalog 用例旁边）追加：

```python
def test_endpoints_catalog_exposes_image_capabilities(client_with_auth):
    """GET /endpoints 在每个 entry 上返回 image_capabilities。"""
    resp = client_with_auth.get("/api/v1/custom-providers/endpoints")
    assert resp.status_code == 200
    by_key = {e["key"]: e for e in resp.json()["endpoints"]}
    assert by_key["openai-chat"]["image_capabilities"] is None
    assert sorted(by_key["openai-images"]["image_capabilities"]) == ["image_to_image", "text_to_image"]
    assert by_key["openai-images-generations"]["image_capabilities"] == ["text_to_image"]
    assert by_key["openai-images-edits"]["image_capabilities"] == ["image_to_image"]
```

> 若 `client_with_auth` fixture 命名不同，按现有用例风格调整。

- [ ] **Step 2: 跑测试看失败**

```bash
uv run pytest tests/test_custom_providers_api.py -k endpoints_catalog -v
```

预期：失败，`image_capabilities` 字段不在响应里（或 422 因为 Pydantic 不识别）。

- [ ] **Step 3: 改 `server/routers/custom_providers.py`**

修改 `EndpointDescriptor`：

```python
class EndpointDescriptor(BaseModel):
    key: str
    media_type: str
    family: str
    display_name_key: str
    request_method: str
    request_path_template: str
    image_capabilities: list[str] | None = None  # image 类填能力字符串列表，其他为 None
```

`endpoint_spec_to_dict` 已在 Task 2 改好，直接返回的字典含此字段，无需再改 `list_endpoint_catalog`。

- [ ] **Step 4: 跑测试看通过**

```bash
uv run pytest tests/test_custom_providers_api.py -k endpoints_catalog -v
```

预期：1 passed。

- [ ] **Step 5: 跑全文件回归**

```bash
uv run pytest tests/test_custom_providers_api.py -v
```

预期：全过。

- [ ] **Step 6: commit**

```bash
uv run ruff check server/routers/custom_providers.py
uv run ruff format server/routers/custom_providers.py
git add server/routers/custom_providers.py tests/test_custom_providers_api.py
git commit -m "feat(custom-providers): /endpoints 暴露 image_capabilities"
```

---

## Task 6: `_check_unique_defaults` 重写为按 image capability 交集互斥

**Files:**
- Modify: `server/routers/custom_providers.py`
- Test: `tests/test_custom_providers_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_custom_providers_api.py` 追加：

```python
def test_check_unique_defaults_allows_split_image_endpoints():
    """同 provider 内 -generations 与 -edits 两条都设默认 → 允许（capability 不交叠）。"""
    from server.routers.custom_providers import ModelInput, _check_unique_defaults

    models = [
        ModelInput(model_id="m1", display_name="m1", endpoint="openai-images-generations", is_default=True),
        ModelInput(model_id="m2", display_name="m2", endpoint="openai-images-edits", is_default=True),
    ]

    def t(key, **params):
        return f"{key}:{params}"

    # 不应抛
    _check_unique_defaults(models, t)


def test_check_unique_defaults_rejects_two_generations_defaults():
    """同 provider 内两条 -generations 都设默认 → 422。"""
    from fastapi import HTTPException
    import pytest
    from server.routers.custom_providers import ModelInput, _check_unique_defaults

    models = [
        ModelInput(model_id="m1", display_name="m1", endpoint="openai-images-generations", is_default=True),
        ModelInput(model_id="m2", display_name="m2", endpoint="openai-images-generations", is_default=True),
    ]

    def t(key, **params):
        return f"{key}:{params}"

    with pytest.raises(HTTPException) as excinfo:
        _check_unique_defaults(models, t)
    assert excinfo.value.status_code == 422


def test_check_unique_defaults_rejects_wildcard_with_split():
    """通配 + -generations 同时默认 → 不允许（通配占 T2I 槽与 -generations 冲突）。"""
    from fastapi import HTTPException
    import pytest
    from server.routers.custom_providers import ModelInput, _check_unique_defaults

    models = [
        ModelInput(model_id="m1", display_name="m1", endpoint="openai-images", is_default=True),
        ModelInput(model_id="m2", display_name="m2", endpoint="openai-images-generations", is_default=True),
    ]

    def t(key, **params):
        return f"{key}:{params}"

    with pytest.raises(HTTPException):
        _check_unique_defaults(models, t)
```

- [ ] **Step 2: 跑测试看失败**

```bash
uv run pytest tests/test_custom_providers_api.py::test_check_unique_defaults_allows_split_image_endpoints tests/test_custom_providers_api.py::test_check_unique_defaults_rejects_two_generations_defaults tests/test_custom_providers_api.py::test_check_unique_defaults_rejects_wildcard_with_split -v
```

预期：第 1 条 fail（旧逻辑按 media_type 互斥，会拒绝两条都默认）；第 2、3 条 pass（旧逻辑也拒）。

- [ ] **Step 3: 改 `_check_unique_defaults`**

替换 `server/routers/custom_providers.py` 里 `_check_unique_defaults` 函数：

```python
def _check_unique_defaults(models: list[ModelInput], _t: Callable[..., str]) -> None:
    """校验默认模型互斥：

    - 非 image endpoint：同 media_type 至多 1 个默认（保留旧规则）。
    - image endpoint：image capability 集合两两不相交（即同一 capability 至多 1 个默认）。
    """
    from lib.custom_provider.endpoints import endpoint_to_image_capabilities

    text_video_defaults: dict[str, list[str]] = {}
    image_defaults: list[tuple[str, frozenset[ImageCapability]]] = []
    for m in models:
        if not m.is_default:
            continue
        try:
            mt = endpoint_to_media_type(m.endpoint)
        except ValueError:
            continue
        if mt != "image":
            text_video_defaults.setdefault(mt, []).append(m.model_id)
            continue
        try:
            caps = endpoint_to_image_capabilities(m.endpoint)
        except ValueError:
            continue
        image_defaults.append((m.model_id, caps))

    duplicates: dict[str, list[str]] = {}
    for mt, ids in text_video_defaults.items():
        if len(ids) > 1:
            duplicates[mt] = ids

    # image：找出任意两条 caps 有交集的模型
    conflict_ids: list[str] = []
    for i in range(len(image_defaults)):
        for j in range(i + 1, len(image_defaults)):
            id_i, caps_i = image_defaults[i]
            id_j, caps_j = image_defaults[j]
            if caps_i & caps_j:
                conflict_ids.extend([id_i, id_j])
    if conflict_ids:
        duplicates["image"] = sorted(set(conflict_ids))

    if duplicates:
        parts = [f"{mt}({', '.join(ids)})" for mt, ids in duplicates.items()]
        raise HTTPException(
            status_code=422,
            detail=_t("default_model_conflict", conflict="; ".join(parts)),
        )
```

需要补 import：

```python
from lib.image_backends.base import ImageCapability
```

- [ ] **Step 4: 跑全部测试**

```bash
uv run pytest tests/test_custom_providers_api.py -v
```

预期：3 个新测试 + 现有测试全过（旧"两条 image 都默认"测试若按 media_type 期望 422，需要改成允许或调整 endpoint 区分；阅读现有测试做最小调整）。

- [ ] **Step 5: lint + commit**

```bash
uv run ruff check server/routers/custom_providers.py tests/test_custom_providers_api.py
uv run ruff format server/routers/custom_providers.py tests/test_custom_providers_api.py
git add server/routers/custom_providers.py tests/test_custom_providers_api.py
git commit -m "feat(custom-providers): is_default 互斥按 image capability 交集"
```

---

## Task 7: `MediaGenerator` capability gating

**Files:**
- Modify: `lib/media_generator.py`
- Test: `tests/test_media_generator_image_capability.py`（新增）

- [ ] **Step 1: 写失败测试**

新增 `tests/test_media_generator_image_capability.py`：

```python
"""MediaGenerator 在调用 image backend 前 gating；不匹配抛 ImageCapabilityError。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.image_backends.base import ImageCapability, ImageCapabilityError


def _make_backend(caps: set[ImageCapability]) -> MagicMock:
    backend = MagicMock()
    backend.name = "fake"
    backend.model = "fake-1"
    backend.capabilities = caps
    backend.generate = AsyncMock()
    return backend


@pytest.mark.asyncio
async def test_t2i_call_with_i2i_only_backend_raises(tmp_path):
    from lib.media_generator import MediaGenerator

    backend = _make_backend({ImageCapability.IMAGE_TO_IMAGE})
    g = MediaGenerator(
        project_name="p",
        project_root=tmp_path,
        image_backend=backend,
    )
    with pytest.raises(ImageCapabilityError) as excinfo:
        await g.generate_image_async(
            prompt="x", resource_type="characters", resource_id="A",
            reference_images=None,
        )
    assert excinfo.value.code == "image_capability_missing_t2i"
    backend.generate.assert_not_called()


@pytest.mark.asyncio
async def test_i2i_call_with_t2i_only_backend_raises(tmp_path):
    from lib.media_generator import MediaGenerator

    backend = _make_backend({ImageCapability.TEXT_TO_IMAGE})
    g = MediaGenerator(
        project_name="p",
        project_root=tmp_path,
        image_backend=backend,
    )
    with pytest.raises(ImageCapabilityError) as excinfo:
        await g.generate_image_async(
            prompt="x", resource_type="characters", resource_id="A",
            reference_images=[tmp_path / "ref.png"],
        )
    assert excinfo.value.code == "image_capability_missing_i2i"
    backend.generate.assert_not_called()
```

> 若 `MediaGenerator` 构造签名与上面不一致，按当前实际签名调整 fixture（参见 `lib/media_generator.py` 顶部 `__init__`）。

- [ ] **Step 2: 跑测试看失败**

```bash
uv run pytest tests/test_media_generator_image_capability.py -v
```

预期：测试运行但断言失败 / `AttributeError`，因为现在没 gating。

- [ ] **Step 3: 在 `generate_image_async` 顶部添加 gating**

`lib/media_generator.py` 的 `generate_image_async` 函数体里，紧跟在 `if self._image_backend is None:` 之后插入：

```python
        # Capability gating：上层 resolver 应当已经选到对的 backend，
        # 这里是兜底（防御调用方手工拼 backend 或配置漂移）。
        from lib.image_backends.base import ImageCapability, ImageCapabilityError

        needed = (
            ImageCapability.IMAGE_TO_IMAGE
            if reference_images
            else ImageCapability.TEXT_TO_IMAGE
        )
        if needed not in self._image_backend.capabilities:
            raise ImageCapabilityError(
                "image_capability_missing_i2i"
                if needed == ImageCapability.IMAGE_TO_IMAGE
                else "image_capability_missing_t2i",
                provider=self._image_backend.name,
                model=self._image_backend.model,
            )
```

- [ ] **Step 4: 跑测试看通过**

```bash
uv run pytest tests/test_media_generator_image_capability.py -v
```

预期：2 passed。

- [ ] **Step 5: 跑 media_generator 全部测试避免回归**

```bash
uv run pytest tests/test_media_generator*.py -v
```

预期：全过。

- [ ] **Step 6: commit**

```bash
uv run ruff check lib/media_generator.py tests/test_media_generator_image_capability.py
uv run ruff format lib/media_generator.py tests/test_media_generator_image_capability.py
git add lib/media_generator.py tests/test_media_generator_image_capability.py
git commit -m "feat(media-generator): image capability gating"
```

---

## Task 8: `lib/i18n/{zh,en}/errors.py` 加 4 条 key

**Files:**
- Modify: `lib/i18n/zh/errors.py`
- Modify: `lib/i18n/en/errors.py`
- Test: `tests/test_i18n_consistency.py`

- [ ] **Step 1: 直接编辑**

`lib/i18n/zh/errors.py` `MESSAGES` dict 末尾追加：

```python
    "image_endpoint_mismatch_no_i2i": "模型 {model} 仅支持文生图（不支持 /v1/images/edits）；请去掉参考图或换一个支持图生图的模型",
    "image_endpoint_mismatch_no_t2i": "模型 {model} 仅支持图生图（必须传参考图）；请提供参考图或换一个支持文生图的模型",
    "image_capability_missing_i2i": "{provider}/{model} 不支持图生图；请配置一个支持图生图的默认模型",
    "image_capability_missing_t2i": "{provider}/{model} 不支持文生图；请配置一个支持文生图的默认模型",
```

`lib/i18n/en/errors.py` 对应位置追加（key 名相同，文案英文化）：

```python
    "image_endpoint_mismatch_no_i2i": "Model {model} only supports text-to-image (no /v1/images/edits); remove reference images or pick a model that supports image edits",
    "image_endpoint_mismatch_no_t2i": "Model {model} only supports image-to-image (reference images required); supply reference images or pick a model that supports text-to-image",
    "image_capability_missing_i2i": "{provider}/{model} does not support image-to-image; configure a default model that supports image edits",
    "image_capability_missing_t2i": "{provider}/{model} does not support text-to-image; configure a default model that supports text-to-image",
```

- [ ] **Step 2: 跑 i18n 一致性测试**

```bash
uv run pytest tests/test_i18n_consistency.py -v
```

预期：通过。

- [ ] **Step 3: commit**

```bash
git add lib/i18n/zh/errors.py lib/i18n/en/errors.py
git commit -m "feat(i18n): 新增图像能力相关 4 条错误 key"
```

---

## Task 9: `ConfigService` & `Resolver` 拆 `default_image_backend` 为 t2i / i2i

**Files:**
- Modify: `lib/config/service.py`
- Modify: `lib/config/resolver.py`
- Test: `tests/test_config_resolver.py`、`tests/test_config_service.py`（若存在）

- [ ] **Step 1: 写失败测试**

`tests/test_config_resolver.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_default_image_backend_t2i_reads_dedicated_setting(setup_config):
    """新 setting key default_image_backend_t2i 优先于旧 default_image_backend。"""
    from lib.config.resolver import ConfigResolver

    # setup_config 是已有 fixture；按其 API 写入 setting
    await setup_config.set_setting("default_image_backend", "openai/legacy")
    await setup_config.set_setting("default_image_backend_t2i", "openai/gpt-image-1")

    resolver = ConfigResolver(...)  # 按现有测试构造方式
    assert await resolver.default_image_backend_t2i() == ("openai", "gpt-image-1")


@pytest.mark.asyncio
async def test_default_image_backend_t2i_falls_back_to_legacy(setup_config):
    await setup_config.set_setting("default_image_backend", "openai/legacy")

    resolver = ConfigResolver(...)
    assert await resolver.default_image_backend_t2i() == ("openai", "legacy")
    assert await resolver.default_image_backend_i2i() == ("openai", "legacy")
```

> 上面用了 placeholder `setup_config`、`ConfigResolver(...)`，请按现有测试中已有的 fixture 与构造模式落实（参考已存在的 `test_default_image_backend_*` 用例）。

- [ ] **Step 2: 跑测试看失败**

```bash
uv run pytest tests/test_config_resolver.py -k default_image_backend_t2i -v
```

预期：`AttributeError: 'ConfigResolver' object has no attribute 'default_image_backend_t2i'`。

- [ ] **Step 3: 改 `lib/config/service.py`**

替换 `get_default_image_backend`：

```python
    async def get_default_image_backend_t2i(self) -> tuple[str, str]:
        raw = await self._setting_repo.get(
            "default_image_backend_t2i",
            await self._setting_repo.get("default_image_backend", _DEFAULT_IMAGE_BACKEND),
        )
        return self._parse_backend(raw, _DEFAULT_IMAGE_BACKEND)

    async def get_default_image_backend_i2i(self) -> tuple[str, str]:
        raw = await self._setting_repo.get(
            "default_image_backend_i2i",
            await self._setting_repo.get("default_image_backend", _DEFAULT_IMAGE_BACKEND),
        )
        return self._parse_backend(raw, _DEFAULT_IMAGE_BACKEND)

    # 删除旧 get_default_image_backend；
    # 调用方已在 Task 11/12/13 切换为 t2i/i2i 变体
```

- [ ] **Step 4: 改 `lib/config/resolver.py`**

替换 `default_image_backend` 与 `_resolve_default_image_backend`：

```python
    async def default_image_backend_t2i(self) -> tuple[str, str]:
        async with self._open_session() as (session, svc):
            return await self._resolve_default_image_backend(svc, session, "t2i")

    async def default_image_backend_i2i(self) -> tuple[str, str]:
        async with self._open_session() as (session, svc):
            return await self._resolve_default_image_backend(svc, session, "i2i")

    async def _resolve_default_image_backend(
        self, svc: ConfigService, session: AsyncSession, capability: str
    ) -> tuple[str, str]:
        assert capability in ("t2i", "i2i")
        key = f"default_image_backend_{capability}"
        raw = await svc.get_setting(key, "")
        if not raw:
            # 旧 key 兼容：老安装可能还没迁
            raw = await svc.get_setting("default_image_backend", "")
        if raw and "/" in raw:
            return ConfigService._parse_backend(raw, _DEFAULT_IMAGE_BACKEND)
        return await self._auto_resolve_backend(svc, session, "image")
```

> 删除旧 `default_image_backend()` 方法；后续 task 12/13 调用方迁到新方法。

- [ ] **Step 5: 跑测试看通过**

```bash
uv run pytest tests/test_config_resolver.py tests/test_config_service.py -v
```

预期：新测试通过；旧 `test_default_image_backend_*` 用例需要改名 / 迁到 t2i 变体（同时改）。

- [ ] **Step 6: commit**

```bash
uv run ruff check lib/config/service.py lib/config/resolver.py tests/test_config_resolver.py
uv run ruff format lib/config/service.py lib/config/resolver.py tests/test_config_resolver.py
git add lib/config/service.py lib/config/resolver.py tests/test_config_resolver.py
git commit -m "feat(config): default_image_backend 拆 t2i / i2i 两条 setting key"
```

---

## Task 10: Alembic data migration（旧 setting key → 两条新 key）

**Files:**
- Create: `alembic/versions/<rev>_split_default_image_backend_setting.py`
- Test: `tests/test_alembic_split_image_backend.py`（新增）

> Alembic head 是 `eedf0aa985e6`。生成迁移：

- [ ] **Step 1: 写失败测试（先建测试）**

新增 `tests/test_alembic_split_image_backend.py`：

```python
"""验证 split_default_image_backend_setting 迁移把旧 setting 复制到 t2i / i2i 两条新 key。"""

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_migration_copies_legacy_setting_to_two_new_keys(async_engine):
    """前置：写入旧 default_image_backend；执行迁移；验证两条新 key 同值。"""
    # 这里用 async_engine fixture（若不存在，按现有测试套路如 test_alembic_*.py 风格构造）
    async with async_engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO system_setting (key, value) VALUES ('default_image_backend', 'openai/gpt-image-1') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        ))

    # 跑迁移（按现有测试 helper 风格）
    from alembic import command
    from alembic.config import Config
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")

    async with async_engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT key, value FROM system_setting WHERE key IN "
            "('default_image_backend', 'default_image_backend_t2i', 'default_image_backend_i2i')"
        ))).fetchall()
    settings = {r.key: r.value for r in rows}
    assert settings.get("default_image_backend_t2i") == "openai/gpt-image-1"
    assert settings.get("default_image_backend_i2i") == "openai/gpt-image-1"
    # 旧 key 保留
    assert settings.get("default_image_backend") == "openai/gpt-image-1"
```

> 若现有 `tests/test_alembic_*.py` 套路不同，参考最近的 `test_alembic_custom_provider_endpoint.py` 写法对齐。

- [ ] **Step 2: 跑测试看失败**

```bash
uv run pytest tests/test_alembic_split_image_backend.py -v
```

预期：失败（迁移文件不存在）。

- [ ] **Step 3: 生成空 alembic 迁移**

```bash
uv run alembic revision -m "split default_image_backend setting into t2i and i2i"
```

记录新生成文件的 revision id，例如 `abcd1234efgh`。

- [ ] **Step 4: 编辑生成的迁移文件**

打开 `alembic/versions/<rev>_split_default_image_backend_setting.py`，替换 `upgrade` / `downgrade`：

```python
"""split default_image_backend setting into t2i and i2i

Revision ID: <rev>
Revises: eedf0aa985e6
Create Date: <auto>
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "<rev>"
down_revision: str | None = "eedf0aa985e6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """把已有 default_image_backend 的值复制到 _t2i / _i2i 两条新 key（若新 key 已存在则跳过该条）。"""
    bind = op.get_bind()
    legacy_row = bind.execute(
        sa.text("SELECT value FROM system_setting WHERE key = 'default_image_backend'")
    ).fetchone()
    if legacy_row is None:
        return  # 新安装无旧值

    legacy_value = legacy_row[0]

    for new_key in ("default_image_backend_t2i", "default_image_backend_i2i"):
        existing = bind.execute(
            sa.text("SELECT 1 FROM system_setting WHERE key = :k").bindparams(k=new_key)
        ).fetchone()
        if existing:
            continue
        bind.execute(
            sa.text("INSERT INTO system_setting (key, value) VALUES (:k, :v)").bindparams(
                k=new_key, v=legacy_value
            )
        )


def downgrade() -> None:
    """回滚仅删除新 key；旧 key 始终保留。"""
    op.execute(
        "DELETE FROM system_setting WHERE key IN "
        "('default_image_backend_t2i', 'default_image_backend_i2i')"
    )
```

> `system_setting` 表名按当前 schema 实际名称调整；可通过 `grep -n 'class.*Setting\|__tablename__' lib/db/models/*.py` 确认。

- [ ] **Step 5: 跑测试看通过**

```bash
uv run pytest tests/test_alembic_split_image_backend.py -v
```

预期：通过。

- [ ] **Step 6: 整套迁移回归**

```bash
uv run pytest tests/test_alembic_*.py -v
```

预期：全部通过。

- [ ] **Step 7: commit**

```bash
uv run ruff check alembic/versions/<rev>_split_default_image_backend_setting.py tests/test_alembic_split_image_backend.py
uv run ruff format alembic/versions/<rev>_split_default_image_backend_setting.py tests/test_alembic_split_image_backend.py
git add alembic/versions/<rev>_split_default_image_backend_setting.py tests/test_alembic_split_image_backend.py
git commit -m "feat(alembic): split default_image_backend setting into t2i/i2i"
```

---

## Task 11: ProjectManager 读层 lazy 升级 `image_provider` → 两组字段

**Files:**
- Modify: `lib/project_manager.py`（找读取项目 dict 的方法）
- Test: `tests/test_project_manager_image_provider_split.py`（新增）

- [ ] **Step 1: 写失败测试**

新增 `tests/test_project_manager_image_provider_split.py`：

```python
"""读取一个含旧 image_provider 字段的项目时，
返回的 dict 应同时包含 image_provider_t2i / image_provider_i2i。"""

import json

import pytest


def test_load_legacy_project_lazy_upgrades(tmp_path):
    from lib.project_manager import ProjectManager

    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    (proj_root / "demo").mkdir()
    project_json = proj_root / "demo" / "project.json"
    project_json.write_text(json.dumps({
        "title": "demo",
        "image_provider": "openai/gpt-image-1",
    }))

    pm = ProjectManager(root=proj_root)
    data = pm.load_project("demo")
    assert data.get("image_provider_t2i") == "openai/gpt-image-1"
    assert data.get("image_provider_i2i") == "openai/gpt-image-1"


def test_load_project_with_split_fields_no_change(tmp_path):
    from lib.project_manager import ProjectManager

    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    (proj_root / "demo").mkdir()
    project_json = proj_root / "demo" / "project.json"
    project_json.write_text(json.dumps({
        "title": "demo",
        "image_provider_t2i": "openai/gpt-image-1",
        "image_provider_i2i": "openai/gpt-image-1-edit",
    }))

    pm = ProjectManager(root=proj_root)
    data = pm.load_project("demo")
    assert data.get("image_provider_t2i") == "openai/gpt-image-1"
    assert data.get("image_provider_i2i") == "openai/gpt-image-1-edit"
```

> 实际 `ProjectManager` 构造签名按现有代码调整。

- [ ] **Step 2: 跑测试看失败**

```bash
uv run pytest tests/test_project_manager_image_provider_split.py -v
```

预期：测试 1 失败（dict 没有 t2i/i2i）。

- [ ] **Step 3: 在 `load_project`（或读取 dict 的最终步骤）注入 lazy 升级**

`lib/project_manager.py` 的 `load_project`（或等价方法）返回前调用：

```python
def _lazy_upgrade_image_provider(data: dict) -> dict:
    """读到旧 image_provider 时填充 _t2i / _i2i 两个字段（不写盘）。"""
    legacy = data.get("image_provider")
    if not isinstance(legacy, str) or "/" not in legacy:
        return data
    data.setdefault("image_provider_t2i", legacy)
    data.setdefault("image_provider_i2i", legacy)
    return data
```

并在 `load_project` 返回前调用：`return _lazy_upgrade_image_provider(data)`。

- [ ] **Step 4: 跑测试看通过**

```bash
uv run pytest tests/test_project_manager_image_provider_split.py -v
```

预期：2 passed。

- [ ] **Step 5: 跑 project_manager 全套**

```bash
uv run pytest tests/test_project_manager*.py -v
```

预期：通过。

- [ ] **Step 6: commit**

```bash
uv run ruff check lib/project_manager.py tests/test_project_manager_image_provider_split.py
uv run ruff format lib/project_manager.py tests/test_project_manager_image_provider_split.py
git add lib/project_manager.py tests/test_project_manager_image_provider_split.py
git commit -m "feat(project-manager): image_provider lazy 升级为 t2i/i2i 两字段"
```

---

## Task 12: `generation_tasks._snapshot_image_backend` & `_resolve_effective_image_backend` 改造

**Files:**
- Modify: `server/services/generation_tasks.py`
- Test: `tests/test_generation_tasks_service.py`

> 任务关键约束：单个生成任务内 shots 既可能 T2I 也可能 I2I；resolver 必须同时返回 `(t2i_pair, i2i_pair)` 两个二元组，per-shot 调用按是否带 ref 图选用。

- [ ] **Step 1: 阅读现有 `_snapshot_image_backend` 与 `_resolve_effective_image_backend`**

```bash
grep -n "_snapshot_image_backend\|_resolve_effective_image_backend\|image_provider" server/services/generation_tasks.py
```

把它们摘抄到剪贴板/笔记，理解当前 payload key 命名（应是 `image_provider`）和返回值。

- [ ] **Step 2: 写失败测试**

`tests/test_generation_tasks_service.py` 追加：

```python
@pytest.mark.asyncio
async def test_snapshot_writes_two_keys(monkeypatch, ...):
    """_snapshot_image_backend 应同时写 image_provider_t2i 与 image_provider_i2i。"""
    from server.services.generation_tasks import _snapshot_image_backend

    # 按现有测试风格 stub resolver / project；保证调用后 payload 含两条
    payload: dict = {}
    # ... 调用 _snapshot_image_backend(payload, project, ...)（按真实签名）
    assert "image_provider_t2i" in payload
    assert "image_provider_i2i" in payload


@pytest.mark.asyncio
async def test_resolve_returns_two_pairs(monkeypatch, ...):
    from server.services.generation_tasks import _resolve_effective_image_backend

    project = {"image_provider_t2i": "openai/gen-1", "image_provider_i2i": "openai/edit-1"}
    payload = {}
    t2i, i2i = await _resolve_effective_image_backend(project, payload)
    assert t2i == ("openai", "gen-1")
    assert i2i == ("openai", "edit-1")


@pytest.mark.asyncio
async def test_resolve_legacy_payload_image_provider_used_for_both():
    from server.services.generation_tasks import _resolve_effective_image_backend

    payload = {"image_provider": "openai/legacy"}
    project = {}
    t2i, i2i = await _resolve_effective_image_backend(project, payload)
    assert t2i == ("openai", "legacy")
    assert i2i == ("openai", "legacy")
```

> 真实 fixture / monkeypatch 按现有测试套路落实。`_resolve_effective_image_backend` 现签名是 `(project, payload)`，新签名仍然接收两参，只是返回值与读路径变化。

- [ ] **Step 3: 跑测试看失败**

```bash
uv run pytest tests/test_generation_tasks_service.py -k "snapshot_writes_two_keys or resolve_returns_two_pairs or resolve_legacy_payload" -v
```

预期：失败。

- [ ] **Step 4: 改 `_snapshot_image_backend`**

将原来注入 `payload["image_provider"]` 改为：

```python
def _snapshot_image_backend(payload: dict, project: dict, settings: ...) -> None:
    """把当前生效的 (T2I, I2I) backend 写入 payload，用于后续任务执行时锁定。"""
    t2i = _read_pair(project.get("image_provider_t2i") or project.get("image_provider"))
    i2i = _read_pair(project.get("image_provider_i2i") or project.get("image_provider"))
    if t2i is None:
        t2i = _read_pair(settings.get("default_image_backend_t2i") or settings.get("default_image_backend"))
    if i2i is None:
        i2i = _read_pair(settings.get("default_image_backend_i2i") or settings.get("default_image_backend"))
    if t2i:
        payload["image_provider_t2i"] = f"{t2i[0]}/{t2i[1]}"
    if i2i:
        payload["image_provider_i2i"] = f"{i2i[0]}/{i2i[1]}"


def _read_pair(raw: str | None) -> tuple[str, str] | None:
    if not raw or "/" not in raw:
        return None
    p, m = raw.split("/", 1)
    return p, m
```

> 实际签名 / 读取来源（resolver vs project vs settings）按现有 helper 调整。

- [ ] **Step 5: 改 `_resolve_effective_image_backend`**

```python
async def _resolve_effective_image_backend(
    project: dict | None,
    payload: dict | None,
) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
    """返回 (t2i_pair, i2i_pair)。任一槽未配置则该位置为 None。

    优先级：
    1. payload 显式 image_provider_<cap>
    2. payload 旧字段 image_provider（存量任务兼容；两个槽都用此值）
    3. project 显式 image_provider_<cap>
    4. project 旧字段 image_provider（lazy 升级路径）
    5. setting default_image_backend_<cap> 或旧 default_image_backend
    """

    def pick(payload_key: str, project_key: str, setting_method) -> tuple[str, str] | None:
        if payload:
            v = payload.get(payload_key) or payload.get("image_provider")
            if v and "/" in v:
                return tuple(v.split("/", 1))
        if project:
            v = project.get(project_key) or project.get("image_provider")
            if v and "/" in v:
                return tuple(v.split("/", 1))
        return setting_method()

    from lib.config.resolver import get_resolver
    r = get_resolver()
    t2i = pick("image_provider_t2i", "image_provider_t2i", lambda: None)
    i2i = pick("image_provider_i2i", "image_provider_i2i", lambda: None)
    if t2i is None:
        try:
            t2i = await r.default_image_backend_t2i()
        except Exception:
            t2i = None
    if i2i is None:
        try:
            i2i = await r.default_image_backend_i2i()
        except Exception:
            i2i = None
    return t2i, i2i
```

> 现有 `_resolve_effective_image_backend` 内的逻辑（含 `await r.default_image_backend()`、payload "image_provider" 注入路径）按上述结构重写。注意保留对 project 自定义场的支持。

- [ ] **Step 6: 改调用点（同 module 内）**

逐个 generate task 入口（grep `_resolve_effective_image_backend` 找到所有 caller）改为：

```python
t2i_pair, i2i_pair = await _resolve_effective_image_backend(project, payload)
# per-shot 循环里：
pair = i2i_pair if reference_images else t2i_pair
if pair is None:
    raise ImageCapabilityError(
        "image_capability_missing_i2i" if reference_images else "image_capability_missing_t2i",
        provider="<unconfigured>", model="<unconfigured>",
    )
image_provider_id, image_model_id = pair
```

> 逐个 caller（storyboard / video / character / clue / grid）按其循环结构落实。`resolve_resolution` 等下游调用仍传 `image_provider_id, image_model_id`。

- [ ] **Step 7: 跑测试**

```bash
uv run pytest tests/test_generation_tasks_service.py -v
```

预期：新测试 + 现有测试全过。若现有测试断言旧 payload key `image_provider`，更新为 `_t2i`/`_i2i`。

- [ ] **Step 8: commit**

```bash
uv run ruff check server/services/generation_tasks.py tests/test_generation_tasks_service.py
uv run ruff format server/services/generation_tasks.py tests/test_generation_tasks_service.py
git add server/services/generation_tasks.py tests/test_generation_tasks_service.py
git commit -m "feat(generation-tasks): image backend resolver 返回 (t2i, i2i) 二元组"
```

---

## Task 13: `server/routers/generate.py` 读两组字段

**Files:**
- Modify: `server/routers/generate.py`
- Test: `tests/test_generate_router.py`（如果存在；否则 grep 现有 image_provider router 测试）

- [ ] **Step 1: 阅读现状**

```bash
grep -n "project_image_backend\|image_provider" server/routers/generate.py
```

锁定第 88 行附近 `image_provider, image_model = project_image_backend.split("/", 1)` 路径。

- [ ] **Step 2: 改实现**

把读 `project_image_backend` 的位置改为根据当前请求是否带 `reference_images` 选两字段之一：

```python
# 读到 project dict 后：
needs_i2i = bool(reference_images)  # reference_images 来自请求参数
project_pair = project.get("image_provider_t2i") if not needs_i2i else project.get("image_provider_i2i")
project_pair = project_pair or project.get("image_provider")  # legacy fallback

if project_pair and "/" in project_pair:
    image_provider, image_model = project_pair.split("/", 1)
else:
    image_provider = _normalize_provider_id(project_pair) if project_pair else _normalize_provider_id("")
    image_model = ""

payload = {
    "image_provider_t2i": project.get("image_provider_t2i") or project.get("image_provider"),
    "image_provider_i2i": project.get("image_provider_i2i") or project.get("image_provider"),
    ...
}
```

> 现有 payload 注入用的是单字段 `image_provider`，按上面结构改成两字段；保留 legacy fallback。

- [ ] **Step 3: 跑相关测试**

```bash
uv run pytest tests/test_generate*.py -v
```

预期：通过；若已有断言对 payload `image_provider` key 直接相等的，更新为新两键。

- [ ] **Step 4: commit**

```bash
uv run ruff check server/routers/generate.py
uv run ruff format server/routers/generate.py
git add server/routers/generate.py
git commit -m "feat(generate-router): 入队 payload 改写两字段 image_provider_t2i/_i2i"
```

---

## Task 14: `server/services/cost_estimation.py` 适配两字段

**Files:**
- Modify: `server/services/cost_estimation.py`
- Test: `tests/test_cost_estimation*.py`

- [ ] **Step 1: 阅读 cost_estimation.py 中所有 `image_provider` 用法**

```bash
grep -n "image_provider\|default_image_backend" server/services/cost_estimation.py
```

- [ ] **Step 2: 写/改测试**

确保 cost estimation 在 project 仅有 t2i/i2i 字段（无旧 image_provider）时也能拿到合理 backend：估算优先 T2I，缺失则 I2I（spec §6 已规定）。

`tests/test_cost_estimation*.py` 追加（按现有用例风格）：

```python
def test_cost_estimation_uses_t2i_default_when_split_fields_present():
    project_data = {"image_provider_t2i": "openai/x", "image_provider_i2i": "openai/y"}
    # ... 调用 estimate API；断言 provider/model 用的是 'openai'/'x'
```

- [ ] **Step 3: 改 `cost_estimation.py`**

把 `project_data.get("image_provider")` 改为：

```python
project_image_provider = (
    project_data.get("image_provider_t2i")
    or project_data.get("image_provider_i2i")
    or project_data.get("image_provider")
)
```

把 `await r.default_image_backend()` 改为 `await r.default_image_backend_t2i()`（费用估算粗粒度，用 T2I 默认即可；若 T2I 解析失败则 fallback I2I）：

```python
try:
    image_provider, image_model = await r.default_image_backend_t2i()
except Exception:
    try:
        image_provider, image_model = await r.default_image_backend_i2i()
    except Exception:
        image_provider, image_model = "unknown", "unknown"
```

- [ ] **Step 4: 跑测试**

```bash
uv run pytest tests/test_cost_estimation*.py -v
```

预期：通过。

- [ ] **Step 5: commit**

```bash
uv run ruff check server/services/cost_estimation.py
uv run ruff format server/services/cost_estimation.py
git add server/services/cost_estimation.py tests/test_cost_estimation*.py
git commit -m "feat(cost-estimation): 适配 image_provider 两字段拆分"
```

---

## Task 15: 后端整体回归

**Files:** none

- [ ] **Step 1: 跑完整后端测试套**

```bash
uv run pytest -x
```

预期：全过。如有失败逐个修复。

- [ ] **Step 2: lint 全量**

```bash
uv run ruff check .
uv run ruff format --check .
```

- [ ] **Step 3: commit（仅当前任务有改动）**

无新改动则跳过。

---

## Task 16: 前端 i18n 错误 & dual-select 文案

**Files:**
- Modify: `frontend/src/i18n/zh/dashboard.ts`、`frontend/src/i18n/en/dashboard.ts`（capability 标签 / dual-select label）
- Modify: `frontend/src/i18n/zh/errors.ts`、`frontend/src/i18n/en/errors.ts`（4 条错误 key）

- [ ] **Step 1: 在 zh/dashboard.ts 追加**

```typescript
'image_capability_t2i': '文生图',
'image_capability_i2i': '图生图',
'image_capability_both': '文生图·图生图',
'image_model_t2i_label': '文生图模型',
'image_model_i2i_label': '图生图模型',
```

- [ ] **Step 2: 在 en/dashboard.ts 追加**

```typescript
'image_capability_t2i': 'T2I',
'image_capability_i2i': 'I2I',
'image_capability_both': 'T2I · I2I',
'image_model_t2i_label': 'Text-to-Image Model',
'image_model_i2i_label': 'Image-to-Image Model',
```

- [ ] **Step 3: 在 zh/errors.ts、en/errors.ts 追加 4 条与后端同名 key**

zh：

```typescript
'image_endpoint_mismatch_no_i2i': '模型 {{model}} 仅支持文生图（不支持 /v1/images/edits）',
'image_endpoint_mismatch_no_t2i': '模型 {{model}} 仅支持图生图（必须传参考图）',
'image_capability_missing_i2i': '{{provider}}/{{model}} 不支持图生图；请配置一个支持图生图的默认模型',
'image_capability_missing_t2i': '{{provider}}/{{model}} 不支持文生图；请配置一个支持文生图的默认模型',
```

en：

```typescript
'image_endpoint_mismatch_no_i2i': 'Model {{model}} only supports text-to-image (no /v1/images/edits)',
'image_endpoint_mismatch_no_t2i': 'Model {{model}} only supports image-to-image (reference images required)',
'image_capability_missing_i2i': '{{provider}}/{{model}} does not support image-to-image; configure a default model that supports image edits',
'image_capability_missing_t2i': '{{provider}}/{{model}} does not support text-to-image; configure a default model that supports text-to-image',
```

- [ ] **Step 4: 跑 i18n 一致性测试**

```bash
uv run pytest tests/test_i18n_consistency.py -v
cd frontend && pnpm check && cd ..
```

预期：通过。

- [ ] **Step 5: commit**

```bash
git add frontend/src/i18n/zh/dashboard.ts frontend/src/i18n/en/dashboard.ts frontend/src/i18n/zh/errors.ts frontend/src/i18n/en/errors.ts
git commit -m "feat(i18n): 前端 image capability 标签 + dual-select label + 4 条错误 key"
```

---

## Task 17: `endpoint-catalog-store` 暴露 `image_capabilities` 与派生 map

**Files:**
- Modify: `frontend/src/types/custom-provider.ts`
- Modify: `frontend/src/stores/endpoint-catalog-store.ts`
- Test: `frontend/src/stores/endpoint-catalog-store.test.ts`

- [ ] **Step 1: 改类型**

`frontend/src/types/custom-provider.ts`：找到 `EndpointDescriptor`（或同等 type）追加：

```ts
export type ImageCap = "text_to_image" | "image_to_image";

export interface EndpointDescriptor {
  key: EndpointKey;
  media_type: MediaType;
  family: string;
  display_name_key: string;
  request_method: string;
  request_path_template: string;
  image_capabilities: ImageCap[] | null;  // 新增
}
```

- [ ] **Step 2: 写失败测试**

`frontend/src/stores/endpoint-catalog-store.test.ts` 追加：

```ts
it("derives endpointToImageCapabilities from catalog", async () => {
  // mock fetch 返回带 image_capabilities 的 endpoints
  ...
  await useEndpointCatalogStore.getState().fetch();
  const map = useEndpointCatalogStore.getState().endpointToImageCapabilities;
  expect(map["openai-images-generations"]).toEqual(["text_to_image"]);
  expect(map["openai-images-edits"]).toEqual(["image_to_image"]);
  expect(map["openai-images"]).toEqual(["text_to_image", "image_to_image"]);
  expect(map["openai-chat"]).toBeUndefined();
});
```

- [ ] **Step 3: 跑测试看失败**

```bash
cd frontend && pnpm vitest run src/stores/endpoint-catalog-store.test.ts
```

预期：`endpointToImageCapabilities` 不存在 → fail。

- [ ] **Step 4: 改 store**

`frontend/src/stores/endpoint-catalog-store.ts`：

```ts
interface EndpointCatalogState {
  endpoints: EndpointDescriptor[];
  initialized: boolean;
  loading: boolean;
  error: string | null;
  endpointToMediaType: Record<EndpointKey, MediaType>;        // 已有
  endpointToImageCapabilities: Record<EndpointKey, ImageCap[]>; // 新增
  fetch: () => Promise<void>;
  refresh: () => Promise<void>;
}
```

`fetch` 内 `set({ endpoints, ... })` 时同时计算 caps map：

```ts
const endpointToImageCapabilities: Record<EndpointKey, ImageCap[]> = {};
for (const e of endpoints) {
  if (e.image_capabilities) endpointToImageCapabilities[e.key] = e.image_capabilities;
}
set({ endpoints, endpointToImageCapabilities, ... });
```

- [ ] **Step 5: 跑测试通过**

```bash
cd frontend && pnpm vitest run src/stores/endpoint-catalog-store.test.ts
```

- [ ] **Step 6: commit**

```bash
git add frontend/src/types/custom-provider.ts frontend/src/stores/endpoint-catalog-store.ts frontend/src/stores/endpoint-catalog-store.test.ts
git commit -m "feat(endpoint-catalog): 暴露 image_capabilities 与派生 map"
```

---

## Task 18: `customProviderHelpers.ts` `toggleDefaultReducer` 升级

**Files:**
- Modify: `frontend/src/components/pages/settings/customProviderHelpers.ts`
- Test: `frontend/src/components/pages/settings/customProviderHelpers.test.ts`

- [ ] **Step 1: 写失败测试**

在测试文件追加：

```ts
const ENDPOINT_TO_CAPS = {
  "openai-images": ["text_to_image", "image_to_image"],
  "openai-images-generations": ["text_to_image"],
  "openai-images-edits": ["image_to_image"],
  "gemini-image": ["text_to_image", "image_to_image"],
};

it("split image endpoints with no overlapping caps coexist as defaults", () => {
  const rows = [
    { key: "g", endpoint: "openai-images-generations", is_default: false },
    { key: "e", endpoint: "openai-images-edits", is_default: true },
  ];
  const next = toggleDefaultReducer(
    rows,
    "g",
    ENDPOINT_TO_CAPS as any,
    ENDPOINT_TO_MEDIA as any,
  );
  // -generations 设为 default，不应清掉 -edits
  expect(next.find(r => r.key === "g")?.is_default).toBe(true);
  expect(next.find(r => r.key === "e")?.is_default).toBe(true);
});

it("wildcard openai-images clears all other image rows", () => {
  const rows = [
    { key: "w", endpoint: "openai-images", is_default: false },
    { key: "g", endpoint: "openai-images-generations", is_default: true },
    { key: "e", endpoint: "openai-images-edits", is_default: true },
  ];
  const next = toggleDefaultReducer(
    rows,
    "w",
    ENDPOINT_TO_CAPS as any,
    ENDPOINT_TO_MEDIA as any,
  );
  expect(next.find(r => r.key === "w")?.is_default).toBe(true);
  expect(next.find(r => r.key === "g")?.is_default).toBe(false);
  expect(next.find(r => r.key === "e")?.is_default).toBe(false);
});
```

- [ ] **Step 2: 跑测试看失败**

```bash
cd frontend && pnpm vitest run src/components/pages/settings/customProviderHelpers.test.ts
```

预期：fail（旧签名不接受 caps map）。

- [ ] **Step 3: 改 `customProviderHelpers.ts`**

```ts
export function toggleDefaultReducer<T extends ModelLike>(
  rows: T[],
  targetKey: string,
  endpointToImageCaps: Record<EndpointKey, ImageCap[] | undefined>,
  endpointToMediaType: Record<EndpointKey, MediaType>,
): T[] {
  const target = rows.find((r) => r.key === targetKey);
  if (!target) return rows;
  const targetMedia = endpointToMediaType[target.endpoint];

  if (targetMedia === undefined) {
    return rows.map((r) => (r.key === targetKey ? { ...r, is_default: !r.is_default } : r));
  }

  if (targetMedia !== "image") {
    return rows.map((r) => {
      if (endpointToMediaType[r.endpoint] !== targetMedia) return r;
      if (r.key === targetKey) return { ...r, is_default: !r.is_default };
      return { ...r, is_default: false };
    });
  }

  const targetCaps = endpointToImageCaps[target.endpoint] ?? [];
  return rows.map((r) => {
    if (r.key === targetKey) return { ...r, is_default: !r.is_default };
    if (endpointToMediaType[r.endpoint] !== "image") return r;
    const rowCaps = endpointToImageCaps[r.endpoint] ?? [];
    const overlap = rowCaps.some((c) => targetCaps.includes(c));
    return overlap ? { ...r, is_default: false } : r;
  });
}
```

- [ ] **Step 4: 更新所有 toggleDefaultReducer 调用点（按 IDE 报错）**

```bash
cd frontend && grep -rn "toggleDefaultReducer" src --include="*.ts" --include="*.tsx"
```

逐个调用点把 endpointToCaps map 当第三个参数注入（来自 `useEndpointCatalogStore`）。

- [ ] **Step 5: 跑测试**

```bash
cd frontend && pnpm vitest run src/components/pages/settings/customProviderHelpers.test.ts
```

- [ ] **Step 6: commit**

```bash
git add frontend/src/components/pages/settings/customProviderHelpers.ts frontend/src/components/pages/settings/customProviderHelpers.test.ts
# 调用点改动
git add frontend/src/components/pages/settings/CustomProviderForm.tsx frontend/src/components/pages/settings/CustomProviderDetail.tsx
git commit -m "feat(custom-provider-helpers): toggleDefaultReducer 按 image capability 交集互斥"
```

---

## Task 19: `EndpointSelect.tsx` 增加 capability tag

**Files:**
- Modify: `frontend/src/components/pages/settings/EndpointSelect.tsx`

- [ ] **Step 1: 改 EndpointOption 与渲染**

在 `EndpointOption` 接口加：

```ts
interface EndpointOption {
  value: EndpointKey;
  labelKey: string;
  mediaType: MediaType;
  method: string;
  path: string;
  imageCaps: ImageCap[] | null;
}
```

`useMemo<EndpointOption[]>` 内 push 时把 `e.image_capabilities` 带上：

```ts
ordered.push({
  value: e.key,
  labelKey: e.display_name_key,
  mediaType: e.media_type,
  method: e.request_method,
  path: e.request_path_template,
  imageCaps: e.image_capabilities,
});
```

下拉列表项右侧渲染 capability tag（用 `t("image_capability_t2i")` 等）：

```tsx
{opt.imageCaps && (
  <span className="text-[10px] tracking-wide text-amber-300/80">
    {opt.imageCaps.length === 2
      ? t("image_capability_both")
      : opt.imageCaps[0] === "text_to_image"
        ? t("image_capability_t2i")
        : t("image_capability_i2i")}
  </span>
)}
```

> 视觉位置可放在 method/path 行末，与现有 emerald 路径文本同行右侧。

- [ ] **Step 2: 视觉自查**

```bash
cd frontend && pnpm dev
```

打开自定义供应商详情，验证 image 三条都显示 capability 标签。

- [ ] **Step 3: typecheck + commit**

```bash
cd frontend && pnpm check && cd ..
git add frontend/src/components/pages/settings/EndpointSelect.tsx
git commit -m "feat(endpoint-select): 选项追加 image capability 标签"
```

---

## Task 20: 新组件 `ImageModelDualSelect.tsx`

**Files:**
- Create: `frontend/src/components/shared/ImageModelDualSelect.tsx`
- Create: `frontend/src/components/shared/ImageModelDualSelect.test.tsx`

- [ ] **Step 1: 写失败测试**

`ImageModelDualSelect.test.tsx`：

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ImageModelDualSelect } from "./ImageModelDualSelect";

describe("ImageModelDualSelect", () => {
  it("shows only one dropdown when chosen model has both capabilities", async () => {
    render(<ImageModelDualSelect
      models={[
        { value: "openai/gpt-image-1", label: "gpt-image-1", endpoint: "openai-images" },
      ]}
      valueT2I="openai/gpt-image-1"
      valueI2I="openai/gpt-image-1"
      onChange={() => {}}
    />);
    expect(screen.queryAllByRole("combobox").length).toBe(1);
  });

  it("shows two dropdowns when t2i pick is single-capability", async () => {
    render(<ImageModelDualSelect
      models={[
        { value: "x/gen", label: "gen", endpoint: "openai-images-generations" },
        { value: "x/edit", label: "edit", endpoint: "openai-images-edits" },
      ]}
      valueT2I="x/gen"
      valueI2I=""
      onChange={() => {}}
    />);
    expect(screen.queryAllByRole("combobox").length).toBe(2);
  });

  it("disables save when either slot empty", () => {
    const onValid = vi.fn();
    render(<ImageModelDualSelect
      models={[
        { value: "x/gen", label: "gen", endpoint: "openai-images-generations" },
      ]}
      valueT2I="x/gen"
      valueI2I=""
      onChange={() => {}}
      onValidityChange={onValid}
    />);
    expect(onValid).toHaveBeenCalledWith(false);
  });
});
```

- [ ] **Step 2: 跑测试看失败**

```bash
cd frontend && pnpm vitest run src/components/shared/ImageModelDualSelect.test.tsx
```

预期：组件不存在 → fail。

- [ ] **Step 3: 实现组件**

`ImageModelDualSelect.tsx`：

```tsx
import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";

export interface ImageModelOption {
  value: string;             // "<provider_id>/<model_id>"
  label: string;
  endpoint: string;
}

interface Props {
  models: ImageModelOption[];
  valueT2I: string;
  valueI2I: string;
  onChange: (next: { t2i: string; i2i: string }) => void;
  onValidityChange?: (valid: boolean) => void;
}

export function ImageModelDualSelect({
  models, valueT2I, valueI2I, onChange, onValidityChange,
}: Props) {
  const { t } = useTranslation("dashboard");
  const caps = useEndpointCatalogStore((s) => s.endpointToImageCapabilities);

  const valid = Boolean(valueT2I) && Boolean(valueI2I);
  useEffect(() => { onValidityChange?.(valid); }, [valid, onValidityChange]);

  const t2iCandidates = useMemo(
    () => models.filter((m) => (caps[m.endpoint] ?? []).includes("text_to_image")),
    [models, caps],
  );
  const i2iCandidates = useMemo(
    () => models.filter((m) => (caps[m.endpoint] ?? []).includes("image_to_image")),
    [models, caps],
  );

  const t2iSelected = models.find((m) => m.value === valueT2I);
  const i2iSelected = models.find((m) => m.value === valueI2I);
  const sameWildcard =
    t2iSelected && i2iSelected
    && t2iSelected.value === i2iSelected.value
    && (caps[t2iSelected.endpoint] ?? []).length === 2;

  // 通配且两槽相同 → 只渲染一个下拉，onChange 同时更新两槽
  if (sameWildcard) {
    return (
      <div>
        <label>{t("image_model_t2i_label")} / {t("image_model_i2i_label")}</label>
        <select
          value={valueT2I}
          onChange={(e) => onChange({ t2i: e.target.value, i2i: e.target.value })}
        >
          <option value="">--</option>
          {models
            .filter((m) => (caps[m.endpoint] ?? []).length === 2)
            .map((m) => <option key={m.value} value={m.value}>{m.label}</option>)
          }
        </select>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <label>{t("image_model_t2i_label")}</label>
        <select
          value={valueT2I}
          onChange={(e) => onChange({ t2i: e.target.value, i2i: valueI2I })}
        >
          <option value="">--</option>
          {t2iCandidates.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
      </div>
      <div>
        <label>{t("image_model_i2i_label")}</label>
        <select
          value={valueI2I}
          onChange={(e) => onChange({ t2i: valueT2I, i2i: e.target.value })}
        >
          <option value="">--</option>
          {i2iCandidates.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
      </div>
    </div>
  );
}
```

> 样式按现有 ModelConfigSection 风格调整。这里给出最小可工作版本。

- [ ] **Step 4: 跑测试通过**

```bash
cd frontend && pnpm vitest run src/components/shared/ImageModelDualSelect.test.tsx
```

- [ ] **Step 5: commit**

```bash
git add frontend/src/components/shared/ImageModelDualSelect.tsx frontend/src/components/shared/ImageModelDualSelect.test.tsx
git commit -m "feat(image-model-dual-select): 单/双下拉组件，按能力智能切换"
```

---

## Task 21: 替换 `ModelConfigSection.tsx` 中 image 单选为 `ImageModelDualSelect`

**Files:**
- Modify: `frontend/src/components/shared/ModelConfigSection.tsx`
- Modify: 任何使用 `ModelConfigSection` 接收 image config 的页面（系统设置 / 项目设置）

- [ ] **Step 1: 阅读现状**

```bash
cd frontend && grep -rn "ModelConfigSection\|image_provider\|imageProvider" src --include="*.tsx"
```

- [ ] **Step 2: 在 ModelConfigSection 内引入 ImageModelDualSelect**

把原来一个 image 单下拉的位置替换为 `<ImageModelDualSelect ... />`，把上层 `imageProviderId` 单值 prop 拆为 `imageProviderT2I` / `imageProviderI2I` 两个 prop（或一个 `image: { t2i, i2i }` 对象 prop）。onChange 回调写到 `image_provider_t2i` / `image_provider_i2i` 两个字段（项目级 → project.json；系统级 → setting）。

- [ ] **Step 3: 更新所有调用点**

按编译错误逐个迁移：

- 系统设置页（grep `default_image_backend`）
- 项目设置页（grep `image_provider`）

读写两条字段。setSetting / saveProject API 同步更新。

- [ ] **Step 4: 视觉验证**

```bash
cd frontend && pnpm dev
```

- 打开系统设置 → 图像默认模型选择，确认通配模型只一个下拉，单能力出两个。
- 打开项目设置 → 同上。

- [ ] **Step 5: typecheck + tests + commit**

```bash
cd frontend && pnpm check && cd ..
git add frontend/src/components/shared/ModelConfigSection.tsx <其它修改文件>
git commit -m "feat(settings): image 模型选择改用 ImageModelDualSelect"
```

---

## Task 22: 整体回归

- [ ] **Step 1: 后端测试**

```bash
uv run pytest -x
```

- [ ] **Step 2: 前端测试 + typecheck + build**

```bash
cd frontend && pnpm check && pnpm build && cd ..
```

- [ ] **Step 3: lint**

```bash
uv run ruff check .
```

- [ ] **Step 4: 端到端手测**

启动后端 + 前端：

```bash
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241
cd frontend && pnpm dev
```

测试场景：

1. 自定义供应商页面，添加一个 OpenAI 兼容 base_url（例如指向只支持 generations 的中转站）。
2. 模型管理：把某 image 模型 endpoint 改为 `openai-images-generations`，设为 T2I 默认；再加一个 `openai-images-edits` 模型，设为 I2I 默认。验证两条都能保存为默认。
3. 系统设置 → 图像默认模型：选 t2i / i2i 各一个。
4. 项目里触发一次纯 T2I 生成（无参考图） → 走 generations 端点成功；触发一次带参考图生成 → 走 edits 端点。
5. 把项目级图像 backend 配成 `-generations` 模型但调一次 i2i → 任务失败，错误信息显示翻译后的 `image_capability_missing_i2i` / `image_endpoint_mismatch_no_i2i`。

- [ ] **Step 5: 最终 commit（若有）**

任何手测引出的 polish 用单独 commit。

---

## 自查 — Spec → Plan 覆盖

| Spec 章节 | 任务 |
|---|---|
| §1 EndpointSpec + image_capabilities | Task 2、4 |
| §1 helper `endpoint_to_image_capabilities` | Task 2 |
| §1 catalog API 暴露 | Task 5 |
| §2 OpenAIImageBackend mode | Task 3 |
| §2 删除旧 fallback | Task 3 |
| §2 ImageCapabilityError 定义 | Task 1 |
| §2 build_backend 三个闭包 | Task 4 |
| §3 is_default 互斥重构（后端） | Task 6 |
| §3 toggleDefaultReducer（前端） | Task 18 |
| §4 setting key 拆分 | Task 9 |
| §4 project 字段 lazy 升级 | Task 11 |
| §4 写入校验（capability 适配的字段） | Task 18 + Task 21 表单层 |
| §4 alembic data migration | Task 10 |
| §5 MediaGenerator gating | Task 7 |
| §5 错误 i18n key（后端） | Task 8 |
| §5 错误 i18n key（前端） | Task 16 |
| §6 _resolve_effective_image_backend | Task 12 |
| §6 _snapshot_image_backend | Task 12 |
| §6 generate router | Task 13 |
| §6 cost_estimation | Task 14 |
| §7 endpoint-catalog-store | Task 17 |
| §7 EndpointSelect capability 标签 | Task 19 |
| §7 ImageModelDualSelect | Task 20 |
| §7 ModelConfigSection / 设置页替换 | Task 21 |
| §7 i18n（前端 dashboard / errors） | Task 16 |
| §8 discovery 默认值 | 无需改（spec 决议保留旧行为） |
| §8 测试改动清单 | 各 task 内含测试 |
