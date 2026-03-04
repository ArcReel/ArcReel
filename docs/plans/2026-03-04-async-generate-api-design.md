# 生成接口异步化设计

## 日期
2026-03-04

## 背景

`generate.py` 的 4 个生成接口（storyboard/video/character/clue）是完全同步阻塞的 — 前端发出请求后，后端直接调用 Gemini/Veo API，直到生成完成才返回 HTTP 响应。视频生成可能需要几十秒甚至更长，导致前端长时间挂起。

已有基础设施（GenerationQueue + Worker + SSE 双通道）仅供 Skill CLI 使用，WebUI 未接入。

## 方案

### 核心思路

4 个 POST 接口从"直接执行 + 等待完成"改为"入队 + 立即返回 task_id"。

```
之前：前端 → generate.py → await Gemini API (30s+) → 返回结果
之后：前端 → generate.py → enqueue_task() → 立即返回 {task_id}
                                    ↓
                          GenerationWorker 异步执行
                                    ↓
                          tasks/stream SSE → 前端 TaskHud 显示状态
                          project events SSE → 前端 refreshProject() + 刷新资源
```

### 后端改动

**generate.py** — 4 个 POST handler：
- 保留参数校验（prompt 格式检查、资源存在性检查）
- 移除 `await generator.generate_xxx_async()` 直接调用
- 改为 `await queue.enqueue_task(...)` 入队
- 立即返回 `{"success": true, "task_id": "..."}`
- 移除 `_video_semaphore` 相关代码（并发控制由 Worker 管理）

### 前端改动

**StudioCanvasRouter.tsx** — 4 个 handleGenerate 回调：
- character/clue：移除 await 等待完成逻辑，入队成功后立即取消 loading
- loading 状态改为基于 useTasksStore 中的活跃任务判断

### 响应格式统一

```json
{
  "success": true,
  "task_id": "uuid-xxx",
  "message": "任务已提交"
}
```

### 不需要改动

- generation_tasks.py — Worker 执行逻辑已完整
- GenerationQueue / TaskRepository — 入队/出队已完善
- GenerationWorker — 已有 image/video 双通道
- tasks.py SSE / project_events.py SSE — 回调链路完整
