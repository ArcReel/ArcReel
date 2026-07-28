# Seedance 2.0 模型支持实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 注册 Seedance 2.0 和 2.0 Fast 两个视频模型，添加定价规则和 per-model 能力映射，使用户可在配置中选用这两个模型进行 t2v/i2v 生成。

**Architecture:** 在现有 Ark 视频后端基础上扩展：registry 添加模型条目，backend 用映射表替代写死的 capabilities，cost calculator 添加定价条目。不改动 `generate()` 方法和 SDK 调用逻辑。

**Tech Stack:** Python, pytest, volcenginesdkarkruntime

---

### Task 1: 模型注册 — 添加 Seedance 2.0 到 config registry

**Files:**
- Modify: `lib/config/registry.py:189-196` (ark models 的 video 部分)
- Test: `tests/test_config_registry_models.py`

- [ ] **Step 1: 写失败测试 — 验证 ark 有 3 个视频模型**

在 `tests/test_config_registry_models.py` 的 `TestProviderRegistry` 类末尾添加：

```python
def test_ark_video_models_include_seedance_2(self):
    meta = PROVIDER_REGISTRY["ark"]
    video_models = {mid: m for mid, m in meta.models.items() if m.media_type == "video"}
    assert len(video_models) == 3
    assert "doubao-seedance-2-0-260128" in video_models
    assert "doubao-seedance-2-0-fast-260128" in video_models
    # 2.0 系列应声明 video_extend 但不声明 flex_tier
    for mid in ("doubao-seedance-2-0-260128", "doubao-seedance-2-0-fast-260128"):
        caps = video_models[mid].capabilities
        assert "video_extend" in caps
        assert "flex_tier" not in caps
    # 1.5 Pro 仍然是默认模型
    assert video_models["doubao-seedance-1-5-pro-251215"].default is True
    assert video_models["doubao-seedance-2-0-260128"].default is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_config_registry_models.py::TestProviderRegistry::test_ark_video_models_include_seedance_2 -v`
Expected: FAIL — `assert 1 == 3`（当前只有 1 个视频模型）

- [ ] **Step 3: 实现 — 添加模型条目**

在 `lib/config/registry.py` 的 ark `models` 字典中，紧跟 `doubao-seedance-1-5-pro-251215` 条目之后（约第 195 行），插入：

```python
"doubao-seedance-2-0-260128": ModelInfo(
    display_name="Seedance 2.0",
    media_type="video",
    capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "video_extend"],
),
"doubao-seedance-2-0-fast-260128": ModelInfo(
    display_name="Seedance 2.0 Fast",
    media_type="video",
    capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "video_extend"],
),
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_config_registry_models.py -v`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add lib/config/registry.py tests/test_config_registry_models.py
git commit -m "feat: 注册 Seedance 2.0 和 2.0 Fast 视频模型到 Ark 供应商"
```

---

### Task 2: 能力映射 — ArkVideoBackend 按模型区分 capabilities

**Files:**
- Modify: `lib/video_backends/ark.py:20-39` (类定义和 `__init__`)
- Test: `tests/test_video_backend_ark.py`

- [ ] **Step 1: 写失败测试 — 验证 2.0 模型能力**

在 `tests/test_video_backend_ark.py` 中添加新的测试类，放在 `TestArkProperties` 之后：

```python
class TestArkModelCapabilities:
    """测试不同模型的能力映射。"""

    def test_seedance_2_has_video_extend(self):
        with patch("lib.video_backends.ark.create_ark_client", return_value=MagicMock()):
            b = ArkVideoBackend(api_key="test", model="doubao-seedance-2-0-260128")
        caps = b.capabilities
        assert VideoCapability.VIDEO_EXTEND in caps
        assert VideoCapability.FLEX_TIER not in caps

    def test_seedance_2_fast_has_video_extend(self):
        with patch("lib.video_backends.ark.create_ark_client", return_value=MagicMock()):
            b = ArkVideoBackend(api_key="test", model="doubao-seedance-2-0-fast-260128")
        caps = b.capabilities
        assert VideoCapability.VIDEO_EXTEND in caps
        assert VideoCapability.FLEX_TIER not in caps

    def test_seedance_1_5_has_flex_tier(self):
        with patch("lib.video_backends.ark.create_ark_client", return_value=MagicMock()):
            b = ArkVideoBackend(api_key="test", model="doubao-seedance-1-5-pro-251215")
        caps = b.capabilities
        assert VideoCapability.FLEX_TIER in caps
        assert VideoCapability.VIDEO_EXTEND not in caps

    def test_unknown_model_gets_default_capabilities(self):
        with patch("lib.video_backends.ark.create_ark_client", return_value=MagicMock()):
            b = ArkVideoBackend(api_key="test", model="some-future-model")
        caps = b.capabilities
        assert VideoCapability.FLEX_TIER in caps
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_video_backend_ark.py::TestArkModelCapabilities -v`
Expected: FAIL — 2.0 模型获得的是默认 capabilities（包含 FLEX_TIER，不含 VIDEO_EXTEND）

- [ ] **Step 3: 实现 — 添加模型能力映射表**

在 `lib/video_backends/ark.py` 的 `ArkVideoBackend` 类中，替换 `__init__` 里写死的 capabilities。在 `DEFAULT_MODEL` 行之后、`__init__` 之前添加映射表，并修改 `__init__`：

```python
class ArkVideoBackend:
    """Ark (火山方舟) 视频生成后端。"""

    DEFAULT_MODEL = "doubao-seedance-1-5-pro-251215"

    _MODEL_CAPABILITIES: dict[str, set[VideoCapability]] = {
        "doubao-seedance-2-0-260128": {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
            VideoCapability.GENERATE_AUDIO,
            VideoCapability.SEED_CONTROL,
            VideoCapability.VIDEO_EXTEND,
        },
        "doubao-seedance-2-0-fast-260128": {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
            VideoCapability.GENERATE_AUDIO,
            VideoCapability.SEED_CONTROL,
            VideoCapability.VIDEO_EXTEND,
        },
    }

    _DEFAULT_CAPABILITIES: set[VideoCapability] = {
        VideoCapability.TEXT_TO_VIDEO,
        VideoCapability.IMAGE_TO_VIDEO,
        VideoCapability.GENERATE_AUDIO,
        VideoCapability.SEED_CONTROL,
        VideoCapability.FLEX_TIER,
    }

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._client = create_ark_client(api_key=api_key)
        self._model = model or self.DEFAULT_MODEL
        self._capabilities = self._MODEL_CAPABILITIES.get(self._model, self._DEFAULT_CAPABILITIES)
```

- [ ] **Step 4: 运行全部 ark 后端测试确认通过**

Run: `uv run python -m pytest tests/test_video_backend_ark.py -v`
Expected: ALL PASS（新测试和已有测试均通过）

- [ ] **Step 5: 提交**

```bash
git add lib/video_backends/ark.py tests/test_video_backend_ark.py
git commit -m "feat: ArkVideoBackend 按模型区分 capabilities（Seedance 2.0 支持 video_extend）"
```

---

### Task 3: 定价 — 添加 Seedance 2.0 到 CostCalculator

**Files:**
- Modify: `lib/cost_calculator.py:87-94` (ARK_VIDEO_COST 字典)
- Test: `tests/test_cost_calculator.py`

- [ ] **Step 1: 写失败测试 — 验证 2.0 定价**

在 `tests/test_cost_calculator.py` 的 `TestArkCost` 类末尾添加：

```python
def test_seedance_2_cost(self):
    calculator = CostCalculator()
    amount, currency = calculator.calculate_ark_video_cost(
        usage_tokens=1_000_000,
        service_tier="default",
        generate_audio=True,
        model="doubao-seedance-2-0-260128",
    )
    assert currency == "CNY"
    assert amount == pytest.approx(46.00)

def test_seedance_2_cost_no_audio_same_price(self):
    calculator = CostCalculator()
    amount, _ = calculator.calculate_ark_video_cost(
        usage_tokens=1_000_000,
        service_tier="default",
        generate_audio=False,
        model="doubao-seedance-2-0-260128",
    )
    assert amount == pytest.approx(46.00)

def test_seedance_2_fast_cost(self):
    calculator = CostCalculator()
    amount, currency = calculator.calculate_ark_video_cost(
        usage_tokens=1_000_000,
        service_tier="default",
        generate_audio=True,
        model="doubao-seedance-2-0-fast-260128",
    )
    assert currency == "CNY"
    assert amount == pytest.approx(37.00)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_cost_calculator.py::TestArkCost::test_seedance_2_cost -v`
Expected: FAIL — 未知模型回退到 1.5 Pro 的 16.00 费率

- [ ] **Step 3: 实现 — 添加定价条目**

在 `lib/cost_calculator.py` 的 `ARK_VIDEO_COST` 字典中，在 `doubao-seedance-1-5-pro-251215` 条目之后添加：

```python
ARK_VIDEO_COST = {
    "doubao-seedance-1-5-pro-251215": {
        ("default", True): 16.00,
        ("default", False): 8.00,
        ("flex", True): 8.00,
        ("flex", False): 4.00,
    },
    "doubao-seedance-2-0-260128": {
        ("default", True): 46.00,
        ("default", False): 46.00,
    },
    "doubao-seedance-2-0-fast-260128": {
        ("default", True): 37.00,
        ("default", False): 37.00,
    },
}
```

- [ ] **Step 4: 运行全部费用测试确认通过**

Run: `uv run python -m pytest tests/test_cost_calculator.py -v`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add lib/cost_calculator.py tests/test_cost_calculator.py
git commit -m "feat: 添加 Seedance 2.0 / 2.0 Fast 视频生成定价规则"
```

---

### Task 4: 全量回归验证

**Files:** 无新改动，仅运行验证

- [ ] **Step 1: 运行全量测试**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: ALL PASS，无回归

- [ ] **Step 2: 运行 lint 和格式检查**

Run: `uv run ruff check lib/config/registry.py lib/video_backends/ark.py lib/cost_calculator.py && uv run ruff format --check lib/config/registry.py lib/video_backends/ark.py lib/cost_calculator.py`
Expected: 无问题

- [ ] **Step 3: 如有 lint 问题，修复并提交**

Run: `uv run ruff format lib/config/registry.py lib/video_backends/ark.py lib/cost_calculator.py`
然后提交（如果有变更）。
