# Async Generate API Design

## Date
2026-03-04

## Background

The 4 generation endpoints in `generate.py` (storyboard/video/character/clue) are fully synchronous and blocking — when the frontend sends a request, the backend directly calls the Gemini/Veo API and returns the HTTP response only after generation completes. Video generation can take tens of seconds or longer, causing the frontend to hang.

The existing infrastructure (GenerationQueue + Worker + SSE dual channel) is only used by Skill CLI; the WebUI has not been integrated.

## Approach

### Core Idea

Change the 4 POST endpoints from "execute directly + wait for completion" to "enqueue + return task_id immediately".

```
Before: Frontend → generate.py → await Gemini API (30s+) → return result
After:  Frontend → generate.py → enqueue_task() → return {task_id} immediately
                                        ↓
                              GenerationWorker executes asynchronously
                                        ↓
                              tasks/stream SSE → frontend TaskHud shows status
                              project events SSE → frontend refreshProject() + refresh resources
```

### Backend Changes

**generate.py** — 4 POST handlers:
- Retain parameter validation (prompt format check, resource existence check)
- Remove direct `await generator.generate_xxx_async()` calls
- Replace with `await queue.enqueue_task(...)` to enqueue
- Return immediately with `{"success": true, "task_id": "..."}`
- Remove `_video_semaphore` related code (concurrency controlled by Worker)

### Frontend Changes

**StudioCanvasRouter.tsx** — 4 handleGenerate callbacks:
- character/clue: remove await-for-completion logic; cancel loading immediately after successful enqueue
- loading state changed to be based on active tasks in useTasksStore

### Unified Response Format

```json
{
  "success": true,
  "task_id": "uuid-xxx",
  "message": "Task submitted"
}
```

### No Changes Needed

- generation_tasks.py — Worker execution logic is already complete
- GenerationQueue / TaskRepository — enqueue/dequeue already polished
- GenerationWorker — already has image/video dual channels
- tasks.py SSE / project_events.py SSE — callback chain is complete
