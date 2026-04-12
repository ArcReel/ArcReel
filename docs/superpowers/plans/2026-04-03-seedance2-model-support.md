# Seedance 2.0 Model Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register Seedance 2.0 and 2.0 Fast video models, add pricing rules and per-model capability mappings, so users can select these two models in configuration for t2v/i2v generation.

**Architecture:** Extend the existing Ark video backend: add model entries to the registry, replace hardcoded capabilities in the backend with a mapping table, and add pricing entries to the cost calculator. No changes to the `generate()` method or SDK call logic.

**Tech Stack:** Python, pytest, volcenginesdkarkruntime

---

### Task 1: Model Registration — Add Seedance 2.0 to Config Registry

**Files:**
- Modify: `lib/config/registry.py:189-196` (ark models video section)
- Test: `tests/test_config_registry_models.py`

- [ ] **Step 1: Write failing tests — verify ark has 3 video models**

Add to the end of the `TestProviderRegistry` class in `tests/test_config_registry_models.py`:

```python
def test_ark_video_models_include_seedance_2(self):
    meta = PROVIDER_REGISTRY["ark"]
    video_models = {mid: m for mid, m in meta.models.items() if m.media_type == "video"}
    assert len(video_models) == 3
    assert "doubao-seedance-2-0-260128" in video_models
    assert "doubao-seedance-2-0-fast-260128" in video_models
    # 2.0 series should declare video_extend but not flex_tier
    for mid in ("doubao-seedance-2-0-260128", "doubao-seedance-2-0-fast-260128"):
        caps = video_models[mid].capabilities
        assert "video_extend" in caps
        assert "flex_tier" not in caps
    # 1.5 Pro remains the default model
    assert video_models["doubao-seedance-1-5-pro-251215"].default is True
    assert video_models["doubao-seedance-2-0-260128"].default is False
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_config_registry_models.py::TestProviderRegistry::test_ark_video_models_include_seedance_2 -v`
Expected: FAIL — `assert 1 == 3` (currently only 1 video model)

- [ ] **Step 3: Implement — add model entries**

In the ark `models` dict in `lib/config/registry.py`, immediately after the `doubao-seedance-1-5-pro-251215` entry (around line 195), insert:

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

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_config_registry_models.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add lib/config/registry.py tests/test_config_registry_models.py
git commit -m "feat: register Seedance 2.0 and 2.0 Fast video models under the Ark provider"
```

---

### Task 2: Capability Mapping — ArkVideoBackend Differentiates Capabilities Per Model

**Files:**
- Modify: `lib/video_backends/ark.py:20-39` (class definition and `__init__`)
- Test: `tests/test_video_backend_ark.py`

- [ ] **Step 1: Write failing tests — verify 2.0 model capabilities**

Add a new test class after `TestArkProperties` in `tests/test_video_backend_ark.py`:

```python
class TestArkModelCapabilities:
    """Test capability mappings for different models."""

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

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_video_backend_ark.py::TestArkModelCapabilities -v`
Expected: FAIL — 2.0 models get default capabilities (includes FLEX_TIER, no VIDEO_EXTEND)

- [ ] **Step 3: Implement — add model capability mapping table**

In `lib/video_backends/ark.py`'s `ArkVideoBackend` class, replace the hardcoded capabilities in `__init__`. Add the mapping table after the `DEFAULT_MODEL` line and before `__init__`, then modify `__init__`:

```python
class ArkVideoBackend:
    """Ark (Volcano Engine) video generation backend."""

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

- [ ] **Step 4: Run all ark backend tests to confirm they pass**

Run: `uv run python -m pytest tests/test_video_backend_ark.py -v`
Expected: ALL PASS (new tests and existing tests both pass)

- [ ] **Step 5: Commit**

```bash
git add lib/video_backends/ark.py tests/test_video_backend_ark.py
git commit -m "feat: ArkVideoBackend differentiates capabilities per model (Seedance 2.0 supports video_extend)"
```

---

### Task 3: Pricing — Add Seedance 2.0 to CostCalculator

**Files:**
- Modify: `lib/cost_calculator.py:87-94` (ARK_VIDEO_COST dict)
- Test: `tests/test_cost_calculator.py`

- [ ] **Step 1: Write failing tests — verify 2.0 pricing**

Add to the end of the `TestArkCost` class in `tests/test_cost_calculator.py`:

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

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_cost_calculator.py::TestArkCost::test_seedance_2_cost -v`
Expected: FAIL — unknown model falls back to 1.5 Pro rate of 16.00

- [ ] **Step 3: Implement — add pricing entries**

In the `ARK_VIDEO_COST` dict in `lib/cost_calculator.py`, add after the `doubao-seedance-1-5-pro-251215` entry:

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

- [ ] **Step 4: Run all cost tests to confirm they pass**

Run: `uv run python -m pytest tests/test_cost_calculator.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add lib/cost_calculator.py tests/test_cost_calculator.py
git commit -m "feat: add Seedance 2.0 / 2.0 Fast video generation pricing rules"
```

---

### Task 4: Full Regression Verification

**Files:** No new changes, verification only

- [ ] **Step 1: Run full test suite**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: ALL PASS, no regressions

- [ ] **Step 2: Run lint and format checks**

Run: `uv run ruff check lib/config/registry.py lib/video_backends/ark.py lib/cost_calculator.py && uv run ruff format --check lib/config/registry.py lib/video_backends/ark.py lib/cost_calculator.py`
Expected: no issues

- [ ] **Step 3: If there are lint issues, fix and commit**

Run: `uv run ruff format lib/config/registry.py lib/video_backends/ark.py lib/cost_calculator.py`
Then commit if there are changes.
