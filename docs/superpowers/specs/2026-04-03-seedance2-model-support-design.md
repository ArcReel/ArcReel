# Seedance 2.0 模型支持设计

> 关联 Issue: [ArcReel/ArcReel#42](https://github.com/ArcReel/ArcReel/issues/42)
> 日期: 2026-04-03
> 范围: 最小可用（模型注册 + 定价 + 能力声明）

## 背景

Seedance 2.0 已对企业公测开放。当前 Ark 视频后端仅注册了 Seedance 1.5 Pro (`doubao-seedance-1-5-pro-251215`)。需要添加 Seedance 2.0 和 2.0 Fast 两个模型，使用户可以在配置中选用。

本次不涉及 Seedance 2.0 的新增能力扩展（多模态参考图、视频编辑/延长、联网搜索等），仅让现有 t2v 和 i2v（首帧）流程在 2.0 模型上跑通。

## 改动清单

### 1. 模型注册 — `lib/config/registry.py`

在 ark 供应商的 `models` 字典中，紧跟 `doubao-seedance-1-5-pro-251215` 之后添加：

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

- `default=True` 保留在 1.5 Pro，不变更默认模型
- 2.0 系列声明 `video_extend`，不声明 `flex_tier`

### 2. 能力映射 — `lib/video_backends/ark.py`

添加模型→能力映射表，替代 `__init__` 中写死的 capabilities：

```python
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

_DEFAULT_CAPABILITIES = {
    VideoCapability.TEXT_TO_VIDEO,
    VideoCapability.IMAGE_TO_VIDEO,
    VideoCapability.GENERATE_AUDIO,
    VideoCapability.SEED_CONTROL,
    VideoCapability.FLEX_TIER,
}
```

`__init__` 中：
```python
self._capabilities = self._MODEL_CAPABILITIES.get(self._model, self._DEFAULT_CAPABILITIES)
```

`generate` 方法无需改动，2.0 的 Ark SDK 调用参数与 1.5 兼容。

### 3. 定价 — `lib/cost_calculator.py`

在 `ARK_VIDEO_COST` 中添加：

```python
"doubao-seedance-2-0-260128": {
    ("default", True): 46.00,
    ("default", False): 46.00,
},
"doubao-seedance-2-0-fast-260128": {
    ("default", True): 37.00,
    ("default", False): 37.00,
},
```

- 2.0 实际按「输入是否含视频」定价，本次范围无视频输入，统一用 46.00/37.00
- `generate_audio` 维度设为相同值（2.0 音频不影响价格）
- 无 flex 条目（2.0 不支持离线推理）
- `calculate_ark_video_cost` 方法无需改动

### 4. 测试

在现有测试文件中扩展，不新增文件：

- **`test_config_registry.py`**: 更新 ark 视频模型数量预期（如有断言）
- **`test_video_backend_ark.py`**: 参数化测试验证 2.0 模型获得正确 capabilities（有 `video_extend`，无 `flex_tier`）
- **`test_cost_calculator.py`**（如存在）: 添加 2.0 模型费用计算断言

## 不在本次范围

- Prompt 适配器（Issue #42 剩余项，单独处理）
- Seedance 2.0 新增能力：首尾帧、多模态参考图、视频编辑/延长、联网搜索
- `VideoGenerationRequest` 扩展（参考图/视频字段）
- 默认模型变更
- 分辨率校验（2.0 不支持 1080p，但 Ark 默认已是 720p，暂不加额外校验）
