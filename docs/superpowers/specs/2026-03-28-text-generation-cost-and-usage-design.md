# Text Generation Cost Calculation and Usage Tracking

> GitHub Issue: ArcReel/ArcReel#169
> Date: 2026-03-28

## Background

After #168 completes the generic text generation service layer extraction, text generation (novel summarization, script generation, style analysis) supports multi-provider calls, but these calls are not included in cost calculation or usage tracking. This design integrates text generation usage tracking into the existing system.

## Design Decisions

| Decision Point | Choice | Rationale |
|---------------|--------|-----------|
| Token fields | Add `input_tokens` + `output_tokens`, retain `usage_tokens` | Text generation input/output have different pricing; `usage_tokens` is only used by Ark video — won't break existing data |
| Integration layer | Create `TextGenerator` wrapper layer | Consistent with `MediaGenerator` pattern, centralized management, callers don't need to care about tracking |
| project_name | Optional parameter | Future toolbox functionality may not be project-level |
| call_type | Unified `"text"` | Same level as image/video, not subdivided by task type; `task_type` orthogonal field can be added later if needed |
| Frontend display | Green FileText icon, token info replaces resolution/duration | Consistent with existing image (blue) / video (purple) visual system |

## Change Scope

### 1. Database Layer

#### ApiCall Model New Fields

```python
# lib/db/models/api_call.py
input_tokens: Mapped[int | None] = mapped_column(default=None)
output_tokens: Mapped[int | None] = mapped_column(default=None)
```

- `usage_tokens` retained unchanged (Ark video continues to use it)
- `call_type` adds `"text"` value (alongside `"image"` / `"video"`)
- Alembic migration: `ALTER TABLE api_calls ADD COLUMN input_tokens INTEGER, ADD COLUMN output_tokens INTEGER`

#### UsageRepository Changes

**`start_call()`**: `call_type` accepts `"text"` (token count is unknown before generation, not passed here).

**`finish_call()`**: Add optional `input_tokens` / `output_tokens` parameters, add text cost calculation branch:

```python
if call.call_type == "text" and call.input_tokens is not None:
    amount, currency = cost_calculator.calculate_text_cost(
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens or 0,
        provider=call.provider,
        model=call.model,
    )
    call.cost_amount = amount
    call.currency = currency
```

**`get_stats()`**: Return value adds `text_count` field.

### 2. TextGenerator Wrapper Layer

New `lib/text_generator.py`:

```python
class TextGenerator:
    """Combines TextBackend + UsageTracker, unified encapsulation of text generation + usage tracking."""

    def __init__(self, backend: TextBackend, usage_tracker: UsageTracker):
        self.backend = backend
        self.usage_tracker = usage_tracker

    @classmethod
    async def create(
        cls, task_type: TextTaskType, project_name: str | None = None
    ) -> "TextGenerator":
        backend = await create_text_backend_for_task(task_type, project_name)
        usage_tracker = UsageTracker()
        return cls(backend, usage_tracker)

    async def generate(
        self,
        request: TextGenerationRequest,
        project_name: str | None = None,
    ) -> TextGenerationResult:
        call_id = await self.usage_tracker.start_call(
            project_name=project_name,
            call_type="text",
            model=self.backend.model,
            prompt=request.prompt[:500],
            provider=self.backend.name,
        )
        try:
            result = await self.backend.generate(request)
            await self.usage_tracker.finish_call(
                call_id=call_id,
                status="success",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            return result
        except Exception as e:
            await self.usage_tracker.finish_call(
                call_id=call_id,
                status="failed",
                error_message=str(e)[:500],
            )
            raise
```

Design notes:
- `backend.model` / `backend.provider`: all three TextBackend implementations already have these two attributes
- `project_name` is passed in at `generate()` time (optional), not bound at construction time
- VersionManager is not introduced — text generation produces no file output

### 3. Call Site Changes (3 locations)

| Call Site | File | Before | After |
|-----------|------|--------|-------|
| ScriptGenerator | `lib/script_generator.py` | `create_text_backend_for_task()` → `backend.generate_async()` | `TextGenerator.create()` → `generator.generate(request, project_name)` |
| ProjectManager.generate_overview | `lib/project_manager.py:1579` | Same as above | Same as above |
| Style analysis | `server/routers/files.py:524` | Same as above | Same as above |

### 4. Frontend Changes

#### Type Extensions

```typescript
// UsageStats addition
text_count: number;

// UsageCall extension
call_type: "image" | "video" | "text";
input_tokens: number | null;
output_tokens: number | null;
```

#### UsageDrawer

- Text type icon: green `<FileText className="h-3.5 w-3.5 text-green-400" />`
- Second line of list row: text shows token information (e.g., `Input 1,234 · Output 5,678 tokens`), replacing the resolution+duration shown for image/video
- Statistics summary adds text call count

#### UsageStatsSection

- Statistics cards for `call_type="text"` appear naturally with grouped data (no additional logic changes needed)
- Cards for text type display total token count instead of duration

#### GlobalHeader Cost Badge

- No changes needed — already aggregated based on `cost_by_currency`, text type costs are automatically included

## Out of Scope

- Not entering GenerationQueue task queue — text generation has low frequency, keep direct calls
- Not subdividing `call_type` (e.g., `text_script` / `text_overview`) — unified `"text"` is sufficient
- Not migrating existing `usage_tokens` data — Ark video continues to use this field
- Not adding a `task_type` field — no current requirement, add as needed later
