# 设计：Instructor 集成与结构化输出能力感知降级

## 背景

`ArkTextBackend._generate_structured()` 使用 `response_format={"type": "json_schema", ...}` 调用火山方舟 API，但当前默认模型 `doubao-seed-2-0-lite-260215` 不支持此参数，调用会直接报错。`PROVIDER_REGISTRY` 中 lite 模型错误声明了 `structured_output` 能力。

本设计引入 Instructor 库作为降级路径，修复 Ark 文本后端结构化输出不可用的问题。

## 目标

- 修复 `ArkTextBackend` 结构化输出对豆包模型不可用的问题
- 修正 `PROVIDER_REGISTRY` 中豆包模型错误的 `structured_output` 能力声明
- 引入 Instructor 作为降级路径，模型无原生支持时通过 prompt 注入 + 解析 + 重试实现结构化输出
- 对上层调用方（ScriptGenerator、ProjectManager 等）完全透明

## 非目标

- 不改造 `TextBackend` Protocol（`generate()` 签名、`TextGenerationRequest` 结构不变）
- 不改造 Gemini/Grok Backend（它们的模型都有原生结构化输出支持）
- 不让所有 Backend 统一走 Instructor——原生支持的保持原生路径

## 决策记录

### 决策 1：选择 Instructor 库

引入 `instructor`（MIT，11k+ Stars，300 万+月下载）。核心定位"给任意 OpenAI 兼容客户端加结构化输出"精确匹配需求。`from_openai()` 直接 patch `Ark` 客户端，`Mode` 枚举提供完整降级路径，内置 Pydantic 校验 + `max_retries` 自动重试。

否决方案：自建（缺少错误反馈重试能力）、PydanticAI（过重）、BAML（DSL 不兼容）、Mirascope（社区更小）。

### 决策 2：选择性使用，非统一入口

Instructor 仅作为降级路径。有原生 `structured_output` 能力的模型继续走原生 API，只有不支持的模型走 Instructor `MD_JSON` 模式。

### 决策 3：独立工具模块（方案 C）

新建 `lib/text_backends/instructor_support.py` 提供纯函数 `generate_structured_via_instructor()`。不引入 mixin 或继承层级。各 Backend 按需调用，当前只有 Ark 使用。

### 决策 4：要求调用方传 Pydantic 类

Instructor 的 `response_model` 需要 Pydantic 类。经检查，所有生产调用点已传 Pydantic 类，唯一例外 `project_manager.py` 中 `ProjectOverview.model_json_schema()` 需改为直接传 `ProjectOverview`。

### 决策 5：Ark Backend 保留原生路径

虽然当前豆包模型不支持原生结构化输出，但保留 `_generate_structured()` 的原生路径代码，未来火山方舟上线支持的模型（如 DeepSeek）可通过 registry capabilities 声明直接走原生路径。

## 架构设计

### 数据流

```
调用方 (ScriptGenerator / ProjectManager)
  │  传入 Pydantic 类作为 response_schema
  ▼
TextGenerator.generate(request)
  │  透传，不感知 Instructor
  ▼
ArkTextBackend.generate(request)
  │  检查 response_schema 是否存在
  ▼
_generate_structured(request)
  │  检查 self._supports_native_structured
  ├─ True  → 原生 response_format（现有逻辑不变）
  └─ False → instructor_support.generate_structured_via_instructor()
  ▼
TextGenerationResult
```

### 新模块：`lib/text_backends/instructor_support.py`

提供一个纯函数：

```python
def generate_structured_via_instructor(
    client,            # OpenAI 兼容客户端（如 Ark）
    model: str,
    messages: list[dict],
    response_model: type[BaseModel],
    mode: Mode = Mode.MD_JSON,
    max_retries: int = 2,
) -> tuple[str, int | None, int | None]:
```

- 使用 `instructor.from_openai(client, mode=mode)` patch 客户端
- 调用 `create_with_completion()` 获取 Pydantic 结果 + completion 对象
- 从 `completion.usage` 提取 token 统计
- 返回 `(json_text, input_tokens, output_tokens)` 元组

关键设计：
- **`Mode.MD_JSON`**：prompt 注入 schema 描述 + 从 markdown/text 中提取 JSON，兼容性最广
- **`max_retries=2`**：解析失败时将错误信息反馈给模型重新生成
- **`create_with_completion()`**：Instructor 官方推荐的 token usage 获取方式
- **鸭子类型 client 参数**：不强绑 `Ark`，保持通用性

### `ArkTextBackend` 改动

1. **构造时判断能力**：新增 `_supports_native_structured` 属性，从 `PROVIDER_REGISTRY` 查询模型是否有 `structured_output` 能力。未注册模型保守降级为 Instructor（宁可多走 prompt 注入也不调用会报错的原生 API）。

2. **`_generate_structured()` 分流**：
   - `_supports_native_structured=True`：走现有原生 `response_format` 路径（零改动）
   - `_supports_native_structured=False`：调用 `generate_structured_via_instructor()`，组装 `TextGenerationResult` 返回

3. **`generate()` 路由逻辑不变**：images → vision, response_schema → structured, else → plain

### Registry 修正

移除 `doubao-seed-2-0-lite-260215` 的 `structured_output` 能力声明。同步检查其他 Ark 文本模型。

### 调用方修正

`lib/project_manager.py`：`ProjectOverview.model_json_schema()` → `ProjectOverview`。

## 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `lib/text_backends/instructor_support.py` | 新建 | Instructor 降级函数 |
| `lib/text_backends/ark.py` | 修改 | 构造时读取能力，降级路径调用 instructor_support |
| `lib/config/registry.py` | 修改 | 修正豆包模型 capabilities |
| `lib/project_manager.py` | 修改 | response_schema 传 Pydantic 类 |
| `pyproject.toml` | 修改 | 添加 `instructor>=1.7.0` 依赖 |
| `tests/test_text_backends/test_instructor_support.py` | 新建 | instructor_support 单元测试 |
| `tests/test_text_backends/test_ark.py` | 修改 | 新增能力判断 + 降级路径测试 |

## 测试策略

| 测试 | 内容 |
|------|------|
| `test_instructor_support.py` | mock Instructor patched client，验证 `create_with_completion()` 调用、JSON 序列化、token 统计提取 |
| `test_ark.py` 扩展 | 验证模型无 `structured_output` 能力时走 Instructor 路径，有能力时走原生路径 |
| 现有测试回归 | Gemini/Grok Backend 不受影响，ProjectManager 传 Pydantic 类兼容所有后端 |
