# API Call Logging and Cost Tracking System Design

> Created: 2025-02-03
> Status: Pending implementation

## Overview

Adds complete logging and cost tracking for image/video generation API calls, including:
- Call parameters, call time, call duration, retry count
- Real-time cost calculation based on resolution/duration
- Failure records (cost = 0)
- WebUI cost statistics view and call record filtering

---

## 1. Data Model and Storage

### 1.1 SQLite Database

**Location**: `projects/.api_usage.db` (single global database under the projects directory)

**Table schema: `api_calls`**

```sql
CREATE TABLE api_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Basic info
    project_name    TEXT NOT NULL,           -- Project name
    call_type       TEXT NOT NULL,           -- 'image' | 'video'
    model           TEXT NOT NULL,           -- Model name

    -- Call parameters
    prompt          TEXT,                    -- Call prompt (may be truncated for storage)
    resolution      TEXT,                    -- '720p' | '1080p' | '4k' | '1K' | '2K'
    duration_seconds INTEGER,               -- Video duration (video only, in seconds)
    aspect_ratio    TEXT,                    -- '9:16' | '16:9' etc.
    generate_audio  BOOLEAN DEFAULT TRUE,    -- Whether audio is generated (video only)

    -- Result info
    status          TEXT NOT NULL,           -- 'success' | 'failed'
    error_message   TEXT,                    -- Error message on failure
    output_path     TEXT,                    -- Output file path

    -- Performance metrics
    started_at      DATETIME NOT NULL,       -- Call start time
    finished_at     DATETIME,                -- Call end time
    duration_ms     INTEGER,                 -- Call duration (milliseconds)
    retry_count     INTEGER DEFAULT 0,       -- Retry count

    -- Cost info (computed in real time and stored)
    cost_usd        REAL DEFAULT 0.0,        -- Cost (USD)

    -- Index-friendly
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_project_name ON api_calls(project_name);
CREATE INDEX idx_call_type ON api_calls(call_type);
CREATE INDEX idx_status ON api_calls(status);
CREATE INDEX idx_created_at ON api_calls(created_at);
```

### 1.2 Cost Calculation Rules

**Image (gemini-3-pro-image-preview)**

| Output Resolution | Token Count | Unit Price | Cost per Image |
|-------------------|-------------|------------|----------------|
| 1K / 2K | 1120 tokens | $120 / 1M tokens | $0.134 / image |
| 4K | 2000 tokens | $120 / 1M tokens | $0.24 / image |

> Note: Input image (reference image) cost is $0.0011/image; not included for now (relatively small)

**Video (Veo 3.1 Standard)**

| Resolution | generate_audio | Unit Price ($/second) |
|------------|----------------|-----------------------|
| 720p / 1080p | true | $0.40 |
| 720p / 1080p | false | $0.20 |
| 4K | true | $0.60 |
| 4K | false | $0.40 |

**Cost calculation formulas**:
- Image: `cost = 0.134` (2K) or `cost = 0.24` (4K)
- Video: `cost = duration_seconds × unit_price`

**Failure records**: `cost_usd = 0.0`

---

## 2. Core Module Architecture

### 2.1 New Modules

```
lib/
├── gemini_client.py      # Existing: API calls
├── media_generator.py    # Existing: media generation middle layer
├── usage_tracker.py      # New: call logging and cost tracking
└── cost_calculator.py    # New: cost calculator
```

### 2.2 CostCalculator Class

**File**: `lib/cost_calculator.py`

**Responsibilities**:
- Encapsulate cost table logic
- Calculate cost based on call parameters

```python
class CostCalculator:
    """Cost calculator."""

    # Image cost (USD per image)
    IMAGE_COST = {
        "1K": 0.134,
        "2K": 0.134,
        "4K": 0.24,
    }

    # Video cost (USD per second)
    VIDEO_COST = {
        # (resolution, generate_audio): cost_per_second
        ("720p", True): 0.40,
        ("720p", False): 0.20,
        ("1080p", True): 0.40,
        ("1080p", False): 0.20,
        ("4k", True): 0.60,
        ("4k", False): 0.40,
    }

    def calculate_image_cost(self, resolution: str = "2K") -> float:
        """Calculate the cost of generating an image."""
        return self.IMAGE_COST.get(resolution.upper(), 0.134)

    def calculate_video_cost(
        self,
        duration_seconds: int,
        resolution: str = "1080p",
        generate_audio: bool = True
    ) -> float:
        """Calculate the cost of generating a video."""
        resolution = resolution.lower()
        cost_per_second = self.VIDEO_COST.get(
            (resolution, generate_audio),
            0.40  # Default: 1080p with audio
        )
        return duration_seconds * cost_per_second
```

### 2.3 UsageTracker Class

**File**: `lib/usage_tracker.py`

**Responsibilities**:
- Manage SQLite database connection
- Provide `start_call()` / `finish_call()` methods for recording calls
- Provide query interface (filter by project, time, type, status)
- Provide aggregated statistics interface

```python
class UsageTracker:
    """API call record tracker."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def start_call(
        self,
        project_name: str,
        call_type: str,  # 'image' | 'video'
        model: str,
        prompt: str = None,
        resolution: str = None,
        duration_seconds: int = None,
        aspect_ratio: str = None,
        generate_audio: bool = True,
    ) -> int:
        """Record the start of a call; returns call_id."""
        ...

    def finish_call(
        self,
        call_id: int,
        status: str,  # 'success' | 'failed'
        output_path: str = None,
        error_message: str = None,
        retry_count: int = 0,
    ) -> None:
        """Record the end of a call and compute cost."""
        ...

    def get_stats(
        self,
        project_name: str = None,
        start_date: datetime = None,
        end_date: datetime = None,
    ) -> dict:
        """Get summary statistics."""
        # Returns: total_cost, image_count, video_count, failed_count
        ...

    def get_calls(
        self,
        project_name: str = None,
        call_type: str = None,
        status: str = None,
        start_date: datetime = None,
        end_date: datetime = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """Get paginated call record list."""
        # Returns: items, total, page, page_size
        ...
```

### 2.4 Integration

**Modify `GeminiClient.__init__`**:

```python
def __init__(self, ..., usage_tracker: UsageTracker = None, project_name: str = None):
    ...
    self.usage_tracker = usage_tracker
    self.project_name = project_name
```

**Modify `generate_image` / `generate_video` methods**:

```python
def generate_video(self, ...):
    call_id = None
    retry_count = 0

    # Record call start
    if self.usage_tracker and self.project_name:
        call_id = self.usage_tracker.start_call(
            project_name=self.project_name,
            call_type="video",
            model=self.VIDEO_MODEL,
            prompt=prompt[:500],  # Truncate for storage
            resolution=resolution,
            duration_seconds=int(duration_seconds),
            aspect_ratio=aspect_ratio,
            generate_audio=not ("music" in negative_prompt.lower()),  # Based on actual parameter
        )

    try:
        # Execute API call (retry_count tracked inside with_retry decorator)
        result = self._do_generate_video(...)

        # Record success
        if self.usage_tracker and call_id:
            self.usage_tracker.finish_call(
                call_id=call_id,
                status="success",
                output_path=str(output_path) if output_path else None,
                retry_count=retry_count,
            )
        return result

    except Exception as e:
        # Record failure
        if self.usage_tracker and call_id:
            self.usage_tracker.finish_call(
                call_id=call_id,
                status="failed",
                error_message=str(e)[:500],
                retry_count=retry_count,
            )
        raise
```

**Modify `MediaGenerator`**:

```python
class MediaGenerator:
    def __init__(self, project_path: Path, rate_limiter: RateLimiter = None):
        self.project_path = Path(project_path)
        self.project_name = self.project_path.name

        # Initialize UsageTracker (global database)
        db_path = self.project_path.parent / ".api_usage.db"
        self.usage_tracker = UsageTracker(db_path)

        # Pass to GeminiClient
        self.gemini = GeminiClient(
            rate_limiter=rate_limiter,
            usage_tracker=self.usage_tracker,
            project_name=self.project_name,
        )
```

### 2.5 Retry Count Tracking

Modify the `with_retry` decorator to pass retry count via context variable:

```python
import contextvars

# Context variable for passing retry count
retry_count_var = contextvars.ContextVar('retry_count', default=0)

def with_retry(...):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_attempts):
                retry_count_var.set(attempt)
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    ...
            raise last_error
        return wrapper
    return decorator
```

---

## 3. WebUI Backend API

### 3.1 New Router File

**File**: `webui/server/routers/usage.py`

```python
router = APIRouter()

@router.get("/usage/stats")
async def get_global_stats(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """Get global summary statistics."""
    # Returns: total_cost, image_count, video_count, failed_count
    ...

@router.get("/usage/stats/{project_name}")
async def get_project_stats(
    project_name: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """Get per-project summary statistics."""
    ...

@router.get("/usage/calls")
async def get_calls(
    project_name: Optional[str] = None,
    call_type: Optional[str] = None,  # 'image' | 'video'
    status: Optional[str] = None,     # 'success' | 'failed'
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """Get call record list (with filtering and pagination)."""
    # Returns: items, total, page, page_size
    ...

@router.get("/usage/projects")
async def get_projects_list():
    """Get list of projects with call records (for filter dropdown)."""
    ...
```

### 3.2 Register Route

**Modify**: `webui/server/app.py`

```python
from webui.server.routers import projects, characters, clues, files, generate, versions, usage

app.include_router(usage.router, prefix="/api/v1", tags=["Cost Statistics"])
```

---

## 4. WebUI Frontend

### 4.1 Global Cost Statistics Page

**File**: `webui/usage.html`

```
┌─────────────────────────────────────────────────────────┐
│  Video Projects  [Home] [Cost Statistics]      Refresh   │
├─────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ Total    │ │ Image    │ │ Video    │ │ Failures │   │
│  │ $156.78  │ │ 320 calls│ │ 89 calls │ │ 15 times │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
├─────────────────────────────────────────────────────────┤
│  Filter: [Time Range ▼] [Type ▼] [Project ▼] [Status ▼] [Reset]│
├─────────────────────────────────────────────────────────┤
│  Call Records                                            │
│  ┌──────┬────────┬──────┬────────┬──────┬──────┬──────┐ │
│  │ Time │Project │ Type │Resolution│Status│Duration│Cost│ │
│  ├──────┼────────┼──────┼────────┼──────┼──────┼──────┤ │
│  │ ...  │  ...   │Video │ 1080p  │  ✓   │  45s │$3.20│ │
│  │ ...  │  ...   │Image │  2K    │  ✓   │   8s │$0.13│ │
│  │ ...  │  ...   │Video │ 1080p  │  ✗   │  12s │$0.00│ │
│  └──────┴────────┴──────┴────────┴──────┴──────┴──────┘ │
│                              [Prev] 1/10 [Next]          │
└─────────────────────────────────────────────────────────┘
```

**Time range filter options**:
- Today
- Last 7 days
- Last 30 days
- Custom (date picker)

**File**: `webui/js/usage.js`

- Load statistics data
- Load call record list
- Filter logic
- Pagination logic

### 4.2 In-project Statistics

**Modify**: `webui/project.html`

Add cost statistics card area at the top of the page:

```html
<!-- Cost statistics cards -->
<div id="usage-stats" class="grid grid-cols-4 gap-4 mb-6">
    <div class="bg-gray-800 rounded-lg p-4">
        <div class="text-sm text-gray-400">Total Cost</div>
        <div class="text-2xl font-bold text-green-400" id="stat-total-cost">$0.00</div>
    </div>
    <div class="bg-gray-800 rounded-lg p-4">
        <div class="text-sm text-gray-400">Image Calls</div>
        <div class="text-2xl font-bold" id="stat-image-count">0</div>
    </div>
    <div class="bg-gray-800 rounded-lg p-4">
        <div class="text-sm text-gray-400">Video Calls</div>
        <div class="text-2xl font-bold" id="stat-video-count">0</div>
    </div>
    <div class="bg-gray-800 rounded-lg p-4">
        <div class="text-sm text-gray-400">Failures</div>
        <div class="text-2xl font-bold text-red-400" id="stat-failed-count">0</div>
    </div>
</div>
<div class="text-right mb-4">
    <a href="/usage.html?project={project_name}" class="text-blue-400 hover:text-blue-300">
        View detailed records →
    </a>
</div>
```

**New**: `webui/js/project/usage.js`

- Load project statistics data
- Update statistics cards

### 4.3 Home Page Navigation Update

**Modify**: `webui/index.html`

Add "Cost Statistics" link to top navigation:

```html
<div class="flex items-center space-x-4">
    <a href="/usage.html" class="text-gray-400 hover:text-white transition-colors">
        Cost Statistics
    </a>
    <!-- Existing refresh and new project buttons -->
</div>
```

---

## 5. File Checklist

### New Files

| File | Description |
|------|-------------|
| `lib/usage_tracker.py` | SQLite database management + call record CRUD |
| `lib/cost_calculator.py` | Cost calculator (encapsulates cost table logic) |
| `webui/server/routers/usage.py` | Cost statistics API routes |
| `webui/usage.html` | Global cost statistics page |
| `webui/js/usage.js` | Cost page frontend logic |
| `webui/js/project/usage.js` | In-project cost statistics component |

### Modified Files

| File | Change |
|------|--------|
| `lib/gemini_client.py` | Integrate UsageTracker; record calls before/after generate_image/generate_video |
| `lib/media_generator.py` | Initialize UsageTracker; pass project_name to GeminiClient |
| `webui/server/app.py` | Register usage router |
| `webui/index.html` | Add "Cost Statistics" link to top navigation |
| `webui/project.html` | Add cost statistics card area |
| `webui/js/project.js` | Import usage.js; fetch cost statistics when loading project |

---

## 6. Implementation Order

### Phase 1 — Core Modules

1. `lib/cost_calculator.py` — cost calculator
2. `lib/usage_tracker.py` — database + record management

### Phase 2 — API Integration

3. Modify `lib/gemini_client.py` — integrate call tracking
4. Modify `lib/media_generator.py` — initialize UsageTracker; pass project_name

### Phase 3 — Backend API

5. `webui/server/routers/usage.py` — statistics and query API
6. Modify `webui/server/app.py` — register routes

### Phase 4 — Frontend Pages

7. `webui/usage.html` + `webui/js/usage.js` — global cost page
8. Modify `webui/project.html` + new `webui/js/project/usage.js` — in-project statistics
9. Modify `webui/index.html` — navigation link

---

## 7. Testing Points

1. **Cost calculation accuracy**: verify image/video cost calculation matches the cost table
2. **Failure records**: verify that failed calls record error_message and cost is 0
3. **Retry count**: verify retry count is accumulated correctly
4. **Filtering**: verify time range, type, project, and status filters work correctly
5. **Pagination**: verify pagination logic is correct
6. **Summary statistics**: verify total cost and call counts are correct
