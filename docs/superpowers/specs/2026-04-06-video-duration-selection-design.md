# Video Duration and Orientation Configurable Design

## Background

Currently video duration is hardcoded to three options `[4, 6, 8]` seconds, and portrait/landscape orientation is tightly coupled to `content_mode` (narration=portrait, drama=landscape). As multiple provider video models are integrated, different models support different durations; these two configurations need to change from hardcoded to dynamically configurable.

## Design Goals

1. Video duration is determined by the video model's capabilities, at the model level
2. Portrait/landscape (aspect_ratio) is completely decoupled from content_mode; chosen independently when creating a project
3. Users can set a project default duration preference, or choose "Auto" to let AI decide based on content
4. Segment level can still individually select duration within the model's supported range
5. Backward compatible with existing project data

## Approach: Extend ModelInfo + Runtime Resolution

Extend the existing `ModelInfo` and `CustomProviderModel` with a `supported_durations` field, reusing the existing Registry/ConfigService system.

---

## 1. Model-Level Duration Capability Declaration

### 1.1 Preset Providers — ModelInfo Extension

Add new fields to `ModelInfo` in `lib/config/registry.py`:

```python
@dataclass(frozen=True)
class ModelInfo:
    display_name: str
    media_type: str
    capabilities: list[str]
    default: bool = False
    supported_durations: list[int] = field(default_factory=list)  # new
    # Resolution constraints on duration, declared only when there are restrictions
    # e.g. {"1080p": [8]} means only 8s can be selected at 1080p; resolutions not listed use the full supported_durations set
    duration_resolution_constraints: dict[str, list[int]] = field(default_factory=dict)  # new
```

Duration declarations for each provider's video models:

| Provider | Model | supported_durations | duration_resolution_constraints |
|----------|-------|---------------------|---------------------------------|
| AI Studio | veo-3.1-generate-preview | [4, 6, 8] | {"1080p": [8]} |
| AI Studio | veo-3.1-fast-generate-preview | [4, 6, 8] | {"1080p": [8]} |
| AI Studio | veo-3.1-lite-generate-preview | [4, 6, 8] | {"1080p": [8]} |
| Vertex AI | veo-3.1-generate-001 | [4, 6, 8] | — |
| Vertex AI | veo-3.1-fast-generate-001 | [4, 6, 8] | — |
| Volcano Engine | doubao-seedance-1-5-pro-251215 | [4, 5, 6, 7, 8, 9, 10, 11, 12] | — |
| Volcano Engine | doubao-seedance-2-0-260128 | [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15] | — |
| Volcano Engine | doubao-seedance-2-0-fast-260128 | [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15] | — |
| Grok | grok-imagine-video | [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15] | — |
| OpenAI | sora-2 | [4, 8, 12] | — |
| OpenAI | sora-2-pro | [4, 8, 12] | — |

Non-video models retain an empty list `[]`.

When the frontend fetches duration options, filter based on the current resolution: if the model declares `duration_resolution_constraints` and the current resolution matches, use the constraint list; otherwise use the full `supported_durations` set.

### 1.2 Custom Providers — CustomProviderModel Extension

Add a new column to `CustomProviderModel` in `lib/db/models/custom_provider.py`:

```python
supported_durations: Mapped[str | None] = mapped_column(Text, nullable=True)
# JSON-serialized list[int], e.g. "[4, 8, 12]"
# null means use conservative presets based on api_format
```

One Alembic migration is required.

### 1.3 Conservative Fallback

Used only for custom providers when the model has no declared `supported_durations`:

```python
DEFAULT_DURATIONS_FALLBACK = [4, 8]
```

---

## 2. Aspect Ratio Decoupling from Content Mode

### 2.1 Project Creation

Add parameter to `CreateProjectRequest`:

```python
class CreateProjectRequest(BaseModel):
    name: str | None = None
    title: str | None = None
    style: str | None = ""
    content_mode: str | None = "narration"
    aspect_ratio: str = "9:16"             # new, independent of content_mode
```

Add `aspect_ratio` parameter to `project_manager.create_project_metadata()`, written to the top level of `project.json`:

```json
{
  "content_mode": "narration",
  "aspect_ratio": "9:16",
  ...
}
```

### 2.2 Project Modification

Remove the restriction preventing `aspect_ratio` from being modified. When the user modifies it, the frontend shows a prompt: already-generated storyboard images/videos remain in the original ratio; regeneration is recommended.

`content_mode` still cannot be modified after creation.

### 2.3 get_aspect_ratio() Simplification

```python
def get_aspect_ratio(project: dict, resource_type: str) -> str:
    if resource_type == "characters":
        return "3:4"
    if resource_type == "clues":
        return "16:9"
    # Prefer top-level field; fall back to content_mode derivation when missing (backward compatibility)
    if "aspect_ratio" in project:
        return project["aspect_ratio"]
    return "9:16" if project.get("content_mode", "narration") == "narration" else "16:9"
```

### 2.4 Compatibility with Existing Projects

When `project.json` lacks the `aspect_ratio` field, derive it from `content_mode` using the original logic (narration→`"9:16"`, drama→`"16:9"`); no forced migration. New projects will always have this field.

---

## 3. Project-Level Default Duration

### 3.1 New Fields in project.json

```json
{
  "aspect_ratio": "9:16",
  "default_duration": 4
}
```

- `default_duration: int` — user's preferred duration
- `default_duration: null` (or missing) — "Auto," AI decides based on content

### 3.2 Impact on Script Generation Prompt

- With default value: Prompt injects `"Duration: choose from [4, 6, 8] seconds, default is 4 seconds"`
- Auto mode: Prompt injects `"Duration: choose from [4, 6, 8] seconds, decide based on content rhythm"`

### 3.3 Compatibility with Existing Projects

Missing `default_duration` is treated as `null` (auto).

---

## 4. DurationSeconds Type Refactoring

### 4.1 Backend

Remove the `DurationSeconds` custom type from `lib/script_models.py`, replace with:

```python
# NarrationSegment
duration_seconds: int = Field(ge=1, le=60, description="Segment duration in seconds")

# DramaScene
duration_seconds: int = Field(ge=1, le=60, description="Scene duration in seconds")
```

No longer hardcoding valid values in the Pydantic layer; strict validation moves to the business layer (based on the current video model's `supported_durations`).

### 4.2 Frontend

```typescript
// Remove
export type DurationSeconds = 4 | 6 | 8;

// Replace with
// duration_seconds uses number type directly
```

---

## 5. Prompt Builder Dynamization

### 5.1 Function Signature Changes

`lib/prompt_builders_script.py`:

```python
def build_narration_prompt(
    ...,
    supported_durations: list[int],
    default_duration: int | None,
    aspect_ratio: str,
) -> str:

def build_drama_prompt(
    ...,
    supported_durations: list[int],
    default_duration: int | None,
    aspect_ratio: str,
) -> str:
```

### 5.2 Dynamic Text Substitution

**Duration section:**
- Remove hardcoded `"Duration: 4, 6, or 8 seconds"`
- Replace with dynamically generated description based on parameters

**Orientation section:**
- `build_storyboard_suffix()` changed to accept `aspect_ratio` parameter, outputting corresponding composition description based on value (`"Portrait composition."` / `"Landscape composition."`)
- Remove hardcoded `"16:9 landscape composition"` from `build_drama_prompt`, change to dynamic injection

### 5.3 Caller Adaptation

`lib/script_generator.py`: reads `supported_durations` (via video model resolution), `default_duration`, and `aspect_ratio` from `project.json`, then passes them to the Prompt builders.

---

## 6. Agent Script and Video Generation Adaptation

### 6.1 generate_video.py

`agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py`:

- `validate_duration()` removes hardcoded `[4, 6, 8]`, changes to accept `supported_durations` parameter
- `default_duration` is read from project configuration, no longer hardcoded 4/8 based on `content_mode`
- SKILL.md is updated synchronously with duration-related descriptions

### 6.2 Service Layer

`server/services/generation_tasks.py`:

- Duration fallback logic in `execute_video_task()`: `payload > project.default_duration > supported_durations[0]`
- `get_aspect_ratio()` simplified to directly read `project["aspect_ratio"]`

`server/routers/generate.py`:

- `GenerateVideoRequest.duration_seconds` default value changes from `4` to `None`, resolved by the service layer

---

## 7. Frontend Changes

### 7.1 Project Creation Form

- Add orientation selector (portrait 9:16 / landscape 16:9), independent of content_mode
- Add default duration selector: options sourced from current video model's `supported_durations`, with additional "Auto" option

### 7.2 Project Settings Page

- Allow modifying `aspect_ratio` and `default_duration`
- When modifying `aspect_ratio`, show a prompt: already-generated storyboard images/videos remain in the original ratio; regeneration is recommended
- When switching video models, `default_duration` options update in sync; if the current value is not in the new model's supported list, reset to `null` (Auto)

### 7.3 SegmentCard Duration Selector

- Change `DURATION_OPTIONS` from hardcoded `[4, 6, 8]` to dynamically fetched from the project's current video model's `supported_durations`
- Data source: can be injected by `StatusCalculator` in project data, or frontend independently parses from providers API

### 7.4 TypeScript Types

- `DurationSeconds = 4 | 6 | 8` changed to `number`
- `ProjectData` adds `default_duration?: number | null`
- Top-level `aspect_ratio: string`

---

## 8. Data Migration and Backward Compatibility

| Scenario | Handling |
|----------|----------|
| Existing project without `aspect_ratio` | Derived from `content_mode` on read (narration→9:16, drama→16:9) |
| Existing project without `default_duration` | Treated as `null` (auto mode) |
| Existing scripts with 4/6/8 values | Still valid, no migration needed |
| CustomProviderModel new column | Alembic migration, nullable, null falls back to api_format presets |
| API responses | Only add fields, do not delete or modify existing fields |

---

## Affected Files List

### Backend
- `lib/config/registry.py` — ModelInfo extension + per-provider duration declarations
- `lib/db/models/custom_provider.py` — CustomProviderModel new column
- `lib/script_models.py` — remove DurationSeconds type
- `lib/prompt_builders.py` — parameterize build_storyboard_suffix
- `lib/prompt_builders_script.py` — dynamic duration and orientation injection into Prompts
- `lib/script_generator.py` — read project configuration, pass to Prompt builders
- `lib/project_manager.py` — create_project_metadata adds aspect_ratio
- `server/routers/projects.py` — CreateProjectRequest adds field, remove modification restriction
- `server/routers/generate.py` — duration default changed to None
- `server/services/generation_tasks.py` — simplify get_aspect_ratio, duration fallback logic
- `agent_runtime_profile/.claude/skills/generate-video/` — scripts + SKILL.md

### Frontend
- `frontend/src/types/script.ts` — DurationSeconds type
- `frontend/src/types/project.ts` — ProjectData new fields
- `frontend/src/components/canvas/timeline/SegmentCard.tsx` — dynamic duration options
- `frontend/src/components/canvas/timeline/TimelineCanvas.tsx` — remove content_mode derivation
- `frontend/src/api.ts` — remove modification restriction
- Project creation/settings-related components — add selectors

### Database
- 1 Alembic migration (CustomProviderModel.supported_durations)
