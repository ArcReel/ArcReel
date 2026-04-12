# Design: Instructor Integration and Capability-Aware Structured Output Fallback

## Background

`ArkTextBackend._generate_structured()` uses `response_format={"type": "json_schema", ...}` to call the Volcano Engine Ark API, but the current default model `doubao-seed-2-0-lite-260215` does not support this parameter, causing direct errors. `PROVIDER_REGISTRY` incorrectly declares `structured_output` capability for the lite model.

This design introduces the Instructor library as a fallback path, fixing the unavailability of structured output in the Ark text backend.

## Goals

- Fix `ArkTextBackend` structured output being unavailable for Doubao models
- Correct the incorrect `structured_output` capability declaration for Doubao models in `PROVIDER_REGISTRY`
- Introduce Instructor as a fallback path — when a model lacks native support, implement structured output through prompt injection + parsing + retry
- Completely transparent to upper-level callers (ScriptGenerator, ProjectManager, etc.)

## Non-Goals

- Not modifying the `TextBackend` Protocol (`generate()` signature and `TextGenerationRequest` structure remain unchanged)
- Not modifying Gemini/Grok backends (their models have native structured output support)
- Not routing all backends through Instructor — backends with native support retain the native path

## Decision Record

### Decision 1: Choose the Instructor Library

Introduce `instructor` (MIT, 11k+ stars, 3M+ monthly downloads). Its core positioning — "add structured output to any OpenAI-compatible client" — precisely matches the requirement. `from_openai()` directly patches the `Ark` client, the `Mode` enum provides a complete fallback path, with built-in Pydantic validation + `max_retries` auto-retry.

Rejected alternatives: self-built (lacks error-feedback retry capability), PydanticAI (too heavy), BAML (DSL incompatibility), Mirascope (smaller community).

### Decision 2: Selective Use, Not a Unified Entry Point

Instructor is used only as a fallback path. Models with native `structured_output` capability continue using the native API; only models without support use Instructor `MD_JSON` mode.

### Decision 3: Independent Utility Module (Option C)

Create `lib/text_backends/instructor_support.py` providing a pure function `generate_structured_via_instructor()`. No mixins or inheritance hierarchy introduced. Backends call it as needed; currently only Ark uses it.

### Decision 4: Require Callers to Pass Pydantic Classes

Instructor's `response_model` requires a Pydantic class. After inspection, all production call sites already pass Pydantic classes. The only exception is `ProjectOverview.model_json_schema()` in `project_manager.py`, which needs to be changed to pass `ProjectOverview` directly.

### Decision 5: Retain Native Path in Ark Backend

Although current Doubao models don't support native structured output, retain the native path code in `_generate_structured()`. Future models on Volcano Engine Ark that support it (e.g., DeepSeek) can use the native path directly through registry capability declarations.

## Architecture Design

### Data Flow

```
Caller (ScriptGenerator / ProjectManager)
  │  passes Pydantic class as response_schema
  ▼
TextGenerator.generate(request)
  │  pass-through, unaware of Instructor
  ▼
ArkTextBackend.generate(request)
  │  check if response_schema exists
  ▼
_generate_structured(request)
  │  check self._supports_native_structured
  ├─ True  → native response_format (existing logic unchanged)
  └─ False → instructor_support.generate_structured_via_instructor()
  ▼
TextGenerationResult
```

### New Module: `lib/text_backends/instructor_support.py`

Provides a single pure function:

```python
def generate_structured_via_instructor(
    client,            # OpenAI-compatible client (e.g., Ark)
    model: str,
    messages: list[dict],
    response_model: type[BaseModel],
    mode: Mode = Mode.MD_JSON,
    max_retries: int = 2,
) -> tuple[str, int | None, int | None]:
```

- Uses `instructor.from_openai(client, mode=mode)` to patch the client
- Calls `create_with_completion()` to get Pydantic result + completion object
- Extracts token statistics from `completion.usage`
- Returns `(json_text, input_tokens, output_tokens)` tuple

Key design choices:
- **`Mode.MD_JSON`**: prompt injection of schema description + JSON extraction from markdown/text — broadest compatibility
- **`max_retries=2`**: on parse failure, feeds error information back to the model for regeneration
- **`create_with_completion()`**: Instructor's recommended method for getting token usage
- **Duck-typed client parameter**: not tightly bound to `Ark`, keeps it general

### `ArkTextBackend` Changes

1. **Determine capability at construction**: Add `_supports_native_structured` attribute, queried from `PROVIDER_REGISTRY` to check whether the model has `structured_output` capability. Unregistered models conservatively fall back to Instructor (better to use prompt injection than call a native API that will error).

2. **`_generate_structured()` branching**:
   - `_supports_native_structured=True`: use existing native `response_format` path (zero changes)
   - `_supports_native_structured=False`: call `generate_structured_via_instructor()`, assemble and return `TextGenerationResult`

3. **`generate()` routing logic unchanged**: images → vision, response_schema → structured, else → plain

### Registry Fix

Remove `structured_output` capability declaration from `doubao-seed-2-0-lite-260215`. Also review other Ark text models.

### Caller Fix

`lib/project_manager.py`: `ProjectOverview.model_json_schema()` → `ProjectOverview`.

## File Change Checklist

| File | Change Type | Description |
|------|-------------|-------------|
| `lib/text_backends/instructor_support.py` | Create | Instructor fallback function |
| `lib/text_backends/ark.py` | Modify | Read capabilities at construction, call instructor_support on fallback path |
| `lib/config/registry.py` | Modify | Fix doubao model capabilities |
| `lib/project_manager.py` | Modify | Pass Pydantic class as response_schema |
| `pyproject.toml` | Modify | Add `instructor>=1.7.0` dependency |
| `tests/test_text_backends/test_instructor_support.py` | Create | instructor_support unit tests |
| `tests/test_text_backends/test_ark.py` | Modify | Add capability check + fallback path tests |

## Testing Strategy

| Test | Content |
|------|---------|
| `test_instructor_support.py` | Mock Instructor patched client, verify `create_with_completion()` call, JSON serialization, token statistics extraction |
| `test_ark.py` extension | Verify Instructor path is taken when model lacks `structured_output` capability; native path taken when it does |
| Existing test regression | Gemini/Grok backends not affected; ProjectManager passing Pydantic class is compatible with all backends |
