# OpenAI 图像端点按能力拆分设计

**日期**：2026-05-02
**状态**：设计稿（待用户复核）
**关联分支**：`refactor/split-image-models`

## 背景

OpenAI 图像 API 包含两条路径：

- `/v1/images/generations` — 文生图（T2I）
- `/v1/images/edits` — 图生图（I2I），需要 `image` 字段

ArcReel 当前 `OpenAIImageBackend` 用 `if request.reference_images:` 在两条路径之间自动派发，
ENDPOINT_REGISTRY 只暴露一条 `openai-images` 通配 endpoint
（`request_path_template="/v1/images/{generations,edits}"` 用 brace 同时表达两条）。

这隐含了「同一模型同时支持两条路径」的假设，对 OpenAI 官方 `gpt-image-*` 通常成立，
但在 NewAPI / OneAPI 中转生态中不成立：很多中转模型只暴露 `/generations`（不支持 edits），
极少数只暴露 `/edits`。结果是用户给一个「只有 generations 的中转模型」传参考图时，
请求落到 `images.edit()` 调用，远端返回 404 / 协议错误，链路失败但归因模糊。

## 范围与决策

| 项 | 决策 |
|---|---|
| OpenAI 图像 endpoint 拆分 | 单条 `openai-images` 拆成三条：`openai-images`（通配 T2I+I2I，向后兼容）/ `openai-images-generations`（仅 T2I）/ `openai-images-edits`（仅 I2I） |
| 运行时语义 | **必须按能力调用模型，无 fallback**；上层 capability gating，backend 兜底抛清晰错误 |
| 默认模型粒度 | 图像侧从「按 media_type 互斥」改为「按能力（T2I / I2I）互斥」；通配 endpoint 同时占两个能力槽 |
| 系统/项目级图像默认配置 | 单 setting key 拆成两个，仍沿用 `<provider>/<model>` 编码 |
| 范围 | 仅图像；文本/视频不动 |
| Discovery | 新发现的图像模型默认 `openai-images`（通配），历史数据原样保留 |
| 错误本地化 | backend / generator 抛带稳定 code 的异常，路由层 `_t(code, **params)` 渲染 |

## §1 ENDPOINT_REGISTRY 与 EndpointSpec

`lib/custom_provider/endpoints.py` — `EndpointSpec` 增加 `image_capabilities` 字段（仅 image 类 endpoint 非空）：

```python
@dataclass(frozen=True)
class EndpointSpec:
    key: str
    media_type: str
    family: str
    display_name_key: str
    request_method: str
    request_path_template: str
    image_capabilities: frozenset[ImageCapability] | None  # 新增
    build_backend: Callable[..., ...]
```

注册表新增两条，并保留通配：

```python
"openai-images": EndpointSpec(
    ...
    request_path_template="/v1/images/{generations,edits}",
    image_capabilities=frozenset({TEXT_TO_IMAGE, IMAGE_TO_IMAGE}),
    build_backend=_build_openai_images,            # mode="both"
),
"openai-images-generations": EndpointSpec(
    ...
    request_path_template="/v1/images/generations",
    image_capabilities=frozenset({TEXT_TO_IMAGE}),
    build_backend=_build_openai_images_generations,  # mode="generations_only"
),
"openai-images-edits": EndpointSpec(
    ...
    request_path_template="/v1/images/edits",
    image_capabilities=frozenset({IMAGE_TO_IMAGE}),
    build_backend=_build_openai_images_edits,        # mode="edits_only"
),
```

**派生 helper（单一真相源）**：

```python
def endpoint_to_image_capabilities(endpoint: str) -> frozenset[ImageCapability]:
    """非 image 类 endpoint 抛 ValueError；image 类返回非空 frozenset。"""
```

`gemini-image` 仍是 image 类、capabilities `{T2I, I2I}`（Gemini 图像协议本来一条 generateContent 路径，无拆分需求）。

`server/routers/custom_providers.py` 暴露 `/custom-providers/endpoints` 时，
把 `image_capabilities` 序列化为 `string[] | null` 一并返回给前端。

## §2 OpenAIImageBackend mode 化

`lib/image_backends/openai.py`：

```python
class OpenAIImageBackend:
    Mode = Literal["both", "generations_only", "edits_only"]

    def __init__(self, *, api_key=None, model=None, base_url=None, mode: Mode = "both"):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        self._model = model or DEFAULT_MODEL
        self._mode = mode
        self._capabilities = {
            "both": {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE},
            "generations_only": {ImageCapability.TEXT_TO_IMAGE},
            "edits_only": {ImageCapability.IMAGE_TO_IMAGE},
        }[mode]

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        has_refs = bool(request.reference_images)
        if has_refs and ImageCapability.IMAGE_TO_IMAGE not in self._capabilities:
            raise ImageCapabilityError("image_endpoint_mismatch_no_i2i", model=self._model)
        if not has_refs and ImageCapability.TEXT_TO_IMAGE not in self._capabilities:
            raise ImageCapabilityError("image_endpoint_mismatch_no_t2i", model=self._model)
        return await (self._generate_edit(request) if has_refs else self._generate_create(request))
```

新建**唯一**异常类（backend 与 generator 共用，不含本地化字符串，只带 code + 上下文 dict）：

```python
# 放在 lib/image_backends/base.py
class ImageCapabilityError(RuntimeError):
    def __init__(self, code: str, **params):
        self.code = code
        self.params = params
        super().__init__(code)
```

backend 抛 `ImageCapabilityError("image_endpoint_mismatch_no_i2i", model=...)`；
generator 抛 `ImageCapabilityError("image_capability_missing_i2i", provider=..., model=...)`。
单一类型让上层一处 `except` 即可。

**关键变化**：

- 旧 `_generate_edit` 中「所有 ref 图打开失败 → 回退 T2I」的隐式 fallback 删除。
  打开失败让 SDK 抛参考图为空错误，或当 mode 不允许 i2i 时直接抛上面的不匹配错误。
- `endpoints.py` 的 build_backend 拆三个闭包：
  `_build_openai_images` / `_build_openai_images_generations` / `_build_openai_images_edits`，
  分别给 OpenAIImageBackend 传 `mode=...`。
- `CustomImageBackend` 包装类不需要改 —— `capabilities` 透传 delegate 即可。

## §3 Provider 内 `is_default` 互斥重构

`lib/db/repositories/custom_provider.py`（仓库写入时校验 + 切换的实现处）。

互斥规则：

- **非 image endpoint**：仍按「同 media_type 互斥」。
- **image endpoint**：把目标行 endpoint 派生 image capability set；
  与同 provider 内其它 image 行 caps 取交集；交集非空 → 那些行 `is_default` 清 0，本行设 1。
- 通配 `openai-images` 行设默认 → 同 provider 内所有其它 image 行（含 `gemini-image`、其它通配、单能力）都被清 0。
- 单能力 `-generations` 行设默认 → 占 T2I 槽的其它行清 0（`-generations`、`openai-images`、`gemini-image`），不动 `-edits`。
- 单能力 `-edits` 行同理。

`frontend/src/components/pages/settings/customProviderHelpers.ts` 的 `toggleDefaultReducer<T>`：

- 入参从 `endpointToMediaType` 升级为 `endpointToImageCaps + endpointToMediaType` 双 map。
- image endpoint：按「caps 集合有交集」互斥；非 image endpoint：按 media_type 互斥（不变）。
- catalog 未加载（caps map 空）→ 单行 toggle 兜底（保留现有兜底语义）。

后端 catalog 已在 §1 暴露 `image_capabilities`，前端在 `endpoint-catalog-store` 派生 caps map。

## §4 系统/项目级图像默认配置

### 现状

存储已经是单字符串 `<provider_id>/<model_id>`：

- Setting key `default_image_backend`（`lib/config/service.py:get_default_image_backend`）
- Project 字段 `project.json.image_provider`（`server/routers/generate.py` 用 `split("/", 1)`）

### 拆分

```text
旧 setting:        "default_image_backend"     = "openai/gpt-image-1"
新 setting:        "default_image_backend_t2i" = "openai/gpt-image-1"
                   "default_image_backend_i2i" = "openai/gpt-image-1"

旧 project 字段:   "image_provider"        = "openai/gpt-image-1"
新 project 字段:   "image_provider_t2i"    = "openai/gpt-image-1"
                   "image_provider_i2i"    = "openai/gpt-image-1"
```

### Resolver 改动

`lib/config/resolver.py`：

```python
async def default_image_backend_t2i(self) -> tuple[str, str]: ...
async def default_image_backend_i2i(self) -> tuple[str, str]: ...
```

旧 `default_image_backend()` 替换为上述两个；调用点不多，直接迁移（详见 §6）。

### 写入校验

写 setting / project 时解析 `<provider>/<model>` → 查 `CustomProviderModel.endpoint` → 派生 image caps；
只允许写到 caps 覆盖的那条 key。两个槽位**独立**填：通配模型可填到任一槽或两个槽；
单能力 `-generations` 模型只能填 T2I 槽；单能力 `-edits` 模型只能填 I2I 槽。
任一组未填 → 对应能力的图像生成请求直接拒绝，不向另一组借用。

### 数据迁移

- **Setting**：alembic data migration 把 `default_image_backend` 复制到 `_t2i` 和 `_i2i` 两条；
  旧 key 保留至下个清理迁移（避免回滚损失），本次只停止读写。
- **Project**：ProjectManager 读取层做 lazy 升级 —— 读到旧 `image_provider` 字段时同时返回新两字段；
  首次写回时落两字段。无需批量迁移。

## §5 MediaGenerator capability gating

`lib/media_generator.py` 调用 image backend 前先校验：

```python
needed = (ImageCapability.IMAGE_TO_IMAGE
          if reference_images else ImageCapability.TEXT_TO_IMAGE)

if needed not in image_backend.capabilities:
    raise ImageCapabilityError(
        "image_capability_missing_i2i" if needed == ImageCapability.IMAGE_TO_IMAGE
        else "image_capability_missing_t2i",
        provider=image_backend.name,
        model=image_backend.model,
    )

result = await image_backend.generate(request)
```

`ImageCapabilityError` 即 §2 定义的同一异常类，按 code 区分语义。

§4 已让 resolver 按是否带 ref 图选 `_t2i` / `_i2i`，理论上选到的就是「对的」模型；
gating 抛错只是兜底（防御调用方手工拼 backend 或配置漂移）。

**入队前不预校验**：保持入队-执行解耦；执行时 resolver 选不到（或选到能力不匹配）就抛上述错误。
错误冒到任务层，写到 task error，前端显示翻译后的 message。

## §6 上层调用方改动

### `server/services/generation_tasks.py`

> **关键**：一个生成任务（如分镜）内不同 shot 既可能要 T2I 也可能要 I2I（角色/线索 shot 带参考图，其余不带）。
> 因此**不能**在任务级只解析一个 backend，必须把"两个能力的 backend"都解析出来，
> 在 per-shot 调用时按是否带 ref 图选取。

- `_snapshot_image_backend`（入队时把当前生效的 backend 写到 payload）：
  改为同时写两份 `image_provider_t2i` / `image_provider_i2i`。
- `_resolve_effective_image_backend(project, payload)` 返回 `(t2i_pair, i2i_pair)` 两个 `(provider_id, model_id)` 二元组。
  各槽按下列优先级查找（独立解析）：
  1. payload 的 `image_provider_t2i` / `image_provider_i2i`
  2. payload 旧字段 `image_provider`（存量任务兼容；T2I 与 I2I 都用此值，不再产生新值）
  3. project 的 `image_provider_t2i` / `image_provider_i2i`
  4. project 旧字段 `image_provider`（lazy 升级路径）
  5. setting `default_image_backend_t2i` / `default_image_backend_i2i`
  任一槽解析失败 → 该槽返回 None；调用方在实际需要它时再抛错。
- 各 generate task 入口（storyboard / video / character / clue / grid）调用点：
  在 per-shot 循环里按是否传 `reference_images` 选择 t2i_pair 或 i2i_pair，
  传给 `MediaGenerator.generate_image(...)`。

### `server/services/cost_estimation.py`

费用估算只关心一个图像 backend 即可（仍按通配/默认走，估算粒度允许"按主能力"近似）。
若两组配置不一致，估算优先 T2I 默认；若仅 I2I 配置存在则用 I2I 默认。

### `server/routers/generate.py`

`project_image_backend.split("/", 1)` 处替换为按能力派生：
旧字段 lazy 升级（见 §4）；新代码读 `image_provider_t2i` / `image_provider_i2i`。

## §7 前端改动

### `endpoint-catalog-store`

后端 `/custom-providers/endpoints` 返回的每条 EndpointSpec 新增 `image_capabilities: string[] | null`。
store 派生：

```ts
endpointToMediaType: Record<EndpointKey, MediaType>            // 已有
endpointToImageCapabilities: Record<EndpointKey, ImageCap[]>   // 新增（image endpoint 非空）
```

### `EndpointSelect.tsx`

- 在 image 分组下多出两条 `openai-images-generations` / `openai-images-edits`。
- 每行 endpoint 选项右侧追加轻量 capability 标签（`T2I` / `I2I` / `T2I·I2I`），数据来自 catalog `image_capabilities`，**不**前端硬编码。

### `customProviderHelpers.ts`

```ts
toggleDefaultReducer<T>(
  rows: T[],
  targetKey: string,
  endpointToImageCaps: Record<EndpointKey, ImageCap[] | null>,
  endpointToMediaType: Record<EndpointKey, MediaType>,
)
```

- image endpoint：按"image-cap 集合有交集"互斥。
- text/video endpoint：按"media_type 相同"互斥（保留旧行为）。
- catalog 未加载 → 单行 toggle 兜底。

### `ModelConfigSection.tsx`

新增 `ImageModelDualSelect` 子组件：

- 单一下拉绑当前候选；onChange 后读 catalog 派生该模型 endpoint 的 caps。
- caps 同时含 T2I+I2I（通配）→ 只一个下拉，写入 `image_t2i` 与 `image_i2i` 两个槽位。
- caps 仅 T2I → 当前下拉绑 T2I 槽；下方动态露出第二个下拉绑 I2I 槽（仅展示 caps 含 I2I 的可选项；含通配模型）。
- caps 仅 I2I 同理（先选 I2I 模型时露 T2I 下拉）。
- 任一槽未填 → 表单校验失败、禁止保存。

ModelConfigSection 之外，所有原有 `image_provider_id` / `image_model_id` 单选位置（系统设置页与项目设置页）替换为 `ImageModelDualSelect`。

### i18n

`frontend/src/i18n/{zh,en}/dashboard.ts`、`errors.ts` 新增：

- 显示名：`endpoint_openai_images_generations_display`、`endpoint_openai_images_edits_display`
- capability 标签：`image_capability_t2i`、`image_capability_i2i`、`image_capability_both`
- 下拉标题：`image_model_t2i_label`、`image_model_i2i_label`
- 错误 4 条（与后端 `lib/i18n/{zh,en}/errors.py` 同名 key）：
  - `image_endpoint_mismatch_no_t2i`
  - `image_endpoint_mismatch_no_i2i`
  - `image_capability_missing_t2i`
  - `image_capability_missing_i2i`

错误文案示例（zh）：

- `image_endpoint_mismatch_no_i2i`：「模型 {model} 仅支持文生图，去掉参考图或换支持图生图的模型」
- `image_endpoint_mismatch_no_t2i`：「模型 {model} 仅支持图生图，请提供参考图」
- `image_capability_missing_i2i`：「{provider}/{model} 不支持图生图，请配置图生图默认模型或通配模型」
- `image_capability_missing_t2i`：「{provider}/{model} 不支持文生图，请配置文生图默认模型或通配模型」

## §8 Discovery / 数据迁移 / 测试 / 非目标

### Discovery 默认值

`lib/custom_provider/discovery.py`：`infer_endpoint`

- 图像家族（match `_IMAGE_PATTERN`）+ `discovery_format=openai` → 仍返回 `openai-images`（通配）。
- 不自动拆成两条记录（discovery 无法验证远端两条路径都通）；用户手工调整。
- `gemini-image` 路径不变。

### 数据迁移

| 数据 | 处理 |
|---|---|
| `custom_provider_model.endpoint = "openai-images"` 现有行 | 不动；通配语义自然映射到新 caps `{T2I, I2I}` |
| Setting `default_image_backend` | alembic data migration 复制到 `_t2i` 与 `_i2i`；旧 key 保留至下个清理迁移 |
| `project.json.image_provider` | ProjectManager 读取层 lazy 升级；首次写回落两字段 |
| `is_default` 行 | 通配 `is_default=True` 行不变；旧规则已保证同 provider 内 image 默认唯一，进新规则不冲突 |

### 测试改动清单

后端：

- `tests/test_custom_provider_endpoints.py` — image_keys 集合扩三条；`infer_endpoint` 仍返回通配
- `tests/test_custom_provider_factory.py` — 三条 image endpoint 各自 build 出的 backend.mode/capabilities
- `tests/test_openai_image_backend.py`：
  - mode=both 行为不变
  - mode=generations_only：传 ref 图 → 抛 `ImageCapabilityError(code="image_endpoint_mismatch_no_i2i")`
  - mode=edits_only：不传 ref 图 → 抛 `image_endpoint_mismatch_no_t2i`
  - 删除"全 ref 图打不开 → 回退 T2I"旧行为测试
- `tests/test_custom_provider_repo.py` — is_default 互斥按能力交集；通配设默认清单能力默认；单能力间不互相清
- 新增 `tests/test_media_generator_image_capability.py` — generator gating；不匹配抛 `ImageCapabilityError`
- `tests/test_config_resolver.py` — `default_image_backend_t2i` / `_i2i` 解析；旧 key lazy 升级
- `tests/test_alembic_*` — 覆盖 setting 数据迁移（旧 key → 两条新 key）

前端：

- `customProviderHelpers.test.ts` — toggleDefaultReducer 三条 image endpoint 互斥矩阵
- `endpoint-catalog-store.test.ts` — catalog 暴露 `image_capabilities` 字段
- 新增 `ImageModelDualSelect.test.tsx` — 单能力露第二下拉、通配只一下拉、两槽未填禁止保存

### 非目标（本轮不动）

- Gemini image endpoint 拆能力（Gemini 图像协议本来就 generateContent 一条路径，无拆分需求）
- Anthropic Messages endpoint（旧 spec 留位，本轮不引入）
- Video 侧 capability 拆分（视频用 `VideoCapabilities` 已经覆盖）
- Cost calculator 行为：能力误用属于配置错误，错误情况无费用产生，calculator 无须改

## 影响面汇总

新增/修改文件预估：

- 后端：`lib/custom_provider/endpoints.py`、`lib/image_backends/openai.py`、
  `lib/db/repositories/custom_provider.py`、`lib/config/resolver.py`、`lib/config/service.py`、
  `lib/config/repository.py`、`lib/media_generator.py`、`lib/i18n/{zh,en}/errors.py`、
  `server/routers/custom_providers.py`、`server/routers/generate.py`、
  `server/services/generation_tasks.py`、`server/services/cost_estimation.py`、
  alembic 一个 data migration
- 前端：`endpoint-catalog-store.ts`、`EndpointSelect.tsx`、`customProviderHelpers.ts`、
  `ModelConfigSection.tsx`、新增 `ImageModelDualSelect.tsx`、`i18n/{zh,en}/dashboard.ts`、`i18n/{zh,en}/errors.ts`
- 测试：见上节

兼容性：

- 现有 `openai-images` 数据零迁移
- 旧 setting / project image_provider 字段在过渡期仍能读，写入路径切换到新两字段
- API `/custom-providers/endpoints` 响应新增字段（前端老版本忽略未知字段，无破坏）
