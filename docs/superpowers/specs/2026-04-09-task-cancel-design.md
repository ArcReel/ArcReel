# 任务取消功能设计

## 概述

为排队中的生成任务（分镜/视频/角色/线索）提供手动取消能力，支持单任务取消（含依赖级联）和批量取消所有排队任务。

## 需求约束

- 只有 `queued` 状态的任务可以取消，`running` 状态不可取消（生成任务会实际产生费用和结果，不应丢弃）
- 所有取消操作均需二次确认
- 单任务取消时，若存在依赖该任务的下游 queued 任务，需级联取消并在确认框中展示影响范围
- Agent skill 通过 `batch_enqueue_and_wait_sync()` 批量提交的任务，被取消后 skill 应能感知并优雅退出

## 设计

### 1. 状态机变更

新增 `cancelled` 终态：

```
queued ──→ running ──→ succeeded
  │                 └─→ failed
  └──→ cancelled (新增)
```

- `TERMINAL_TASK_STATUSES = ("succeeded", "failed", "cancelled")`
- `cancelled` 只从 `queued` 转入
- 级联取消复用 `_cascade_failed_dependents()` 的递归模式

### 2. 数据模型变更

Task 模型新增字段：

- `cancelled_by: Optional[str]` — `"user"`（手动取消）或 `"cascade"`（级联取消），用于前端区分展示

### 3. API 设计

#### 单任务取消

```
GET  /api/v1/tasks/{task_id}/cancel-preview
Response: {
  "task": { "task_id": "...", "task_type": "storyboard", "resource_id": "..." },
  "cascaded": [
    { "task_id": "...", "task_type": "video", "resource_id": "..." },
    ...
  ]
}
// 任务不是 queued → 400

POST /api/v1/tasks/{task_id}/cancel
Response: {
  "cancelled": [ ... ],
  "skipped_running": [ ... ]
}
// 任务不是 queued → 400
```

#### 批量取消（项目维度）

```
GET  /api/v1/projects/{project_name}/tasks/cancel-all-preview
Response: {
  "queued_count": 12
}

POST /api/v1/projects/{project_name}/tasks/cancel-all
Response: {
  "cancelled_count": 11,
  "skipped_running_count": 1
}
```

#### 设计要点

- Preview 是纯读 GET，Cancel 是 POST，读写分离
- 批量取消无需级联逻辑——所有 queued 都直接取消
- Cancel 执行时原子校验：`UPDATE ... WHERE status = 'queued'`，只影响仍为 queued 的行
- 返回值区分 cancelled 和 skipped_running，前端据此展示准确反馈

### 4. 后端实现层

#### TaskRepository 新增方法

```python
async def cancel_task(task_id: str) -> CancelResult:
    # 1. 校验任务状态为 queued，否则抛异常
    # 2. 标记为 cancelled, cancelled_by="user"
    # 3. 递归查找所有依赖此任务的 queued 任务，标记为 cancelled, cancelled_by="cascade"
    # 4. 为每个取消的任务写入 TaskEvent
    # 5. 返回 CancelResult(cancelled=[...], skipped_running=[...])

async def cancel_all_queued(project_name: str) -> BulkCancelResult:
    # 1. UPDATE tasks SET status='cancelled', cancelled_by='user'
    #    WHERE project_name=? AND status='queued'
    # 2. 返回 BulkCancelResult(cancelled_count=N, skipped_running_count=M)

async def get_cancel_preview(task_id: str) -> CancelPreview:
    # 1. 校验 queued 状态
    # 2. 递归收集依赖链上的 queued 任务
    # 3. 返回 CancelPreview(task=..., cascaded=[...])

async def get_cancel_all_preview(project_name: str) -> int:
    # SELECT COUNT(*) WHERE project_name=? AND status='queued'
```

#### GenerationQueue

透传 repository 方法，保持与 `enqueue_task` / `mark_task_failed` 同层封装。

#### generation_queue_client 变更

- `wait_for_task()` 轮询循环中检测到 `cancelled` 状态时抛出 `TaskCancelledError`（新增异常类，与 `TaskFailedError` 并列）
- `batch_enqueue_and_wait_sync()` 的 `on_failure` 回调同样触发，`BatchTaskResult` 中可区分 failed 和 cancelled

### 5. 前端交互

#### 单任务取消

1. `queued` 状态的任务显示取消按钮（`running` 不显示）
2. 点击 → 调 cancel-preview API
3. 弹确认框：
   - 无级联："确定取消此任务？"
   - 有级联："取消此任务将同时取消 N 个依赖任务" + 依赖任务列表
4. 确认 → 调 cancel API → toast 反馈
   - 全部成功："已取消 N 个任务"
   - 部分 skipped："已取消 N 个任务，M 个任务在此期间已开始执行"

#### 批量取消

1. 任务列表区域提供"取消所有排队任务"按钮（仅当有 queued 任务时显示）
2. 点击 → 调 cancel-all-preview → 确认框："确定取消所有 N 个排队中的任务？"
3. 确认 → 调 cancel-all API → toast 反馈

#### 状态展示

- `cancelled` 任务显示为灰色/划线样式，与 `failed`（红色）视觉区分
- `cancelled_by="cascade"` 的任务附加"级联取消"标签

### 6. 竞态处理

采用乐观策略：Preview 返回当时快照，Cancel 原子执行时只取消仍为 `queued` 的任务。若 preview 与 cancel 之间有任务被 worker 领走变为 running，cancel 返回 `skipped_running` 列表，前端展示差异。竞态窗口极短（2-5 秒），实际发生概率低，且结果完全透明。
