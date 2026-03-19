# 修复 Agent 图片生成 Event Loop 冲突

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 skill 脚本通过队列生成图片时 "Future attached to a different loop" 错误

**Architecture:** 两层修复：(1) 在 `_run_sync` 中 dispose 连接池作为安全网（已完成），(2) 从 4 个脚本中移除冗余的 `is_worker_online` 前置检查，消除多次 `asyncio.run()` 的触发根因

**Tech Stack:** Python asyncio, SQLAlchemy async engine, aiosqlite

---

## 根因分析

Skill 脚本中 `is_worker_online_sync()` + `enqueue_and_wait_sync()` 各调用一次 `asyncio.run()`，
第一次创建连接池连接绑定在 loop1 上，第二次创建 loop2 时复用了 loop1 的连接 → 报错。

### 修复策略

| 层级 | 修复 | 文件 |
|------|------|------|
| 安全网 | `_run_in_fresh_loop()` — 每次 `asyncio.run()` 前 dispose 连接池 | `lib/generation_queue_client.py` ✅ |
| 根因 | 移除脚本中冗余的 `is_worker_online` 前置检查 | 4 个 skill 脚本 |

`is_worker_online` 前置检查是冗余的，因为 `enqueue_task_only()` 内部已检查 worker 在线状态
并抛出 `WorkerOfflineError`。脚本已经 catch 了这个异常作为回退路径。

---

## Task 1: 修改 generate_character.py（已完成安全网部分）

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/generate-characters/scripts/generate_character.py:19-23,78-99`

- [ ] **Step 1: 移除 `is_worker_online_sync` 导入**

从 import 中移除 `is_worker_online_sync as is_worker_online`

- [ ] **Step 2: 移除 `if is_worker_online():` 包裹层**

```python
# Before:
if is_worker_online():
    try:
        queued = enqueue_and_wait(...)
        ...
        return output_path
    except WorkerOfflineError:
        print("ℹ️  未检测到队列 worker，回退直连生成")
    except TaskFailedError as exc:
        raise RuntimeError(...) from exc

# After:
try:
    queued = enqueue_and_wait(...)
    ...
    return output_path
except WorkerOfflineError:
    print("ℹ️  未检测到队列 worker，回退直连生成")
except TaskFailedError as exc:
    raise RuntimeError(...) from exc
```

---

## Task 2: 修改 generate_clue.py

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/generate-clues/scripts/generate_clue.py:19-24,65-86`

- [ ] **Step 1: 移除 `is_worker_online_sync` 导入**
- [ ] **Step 2: 移除 `if is_worker_online():` 包裹层**

与 Task 1 完全相同的模式。

---

## Task 3: 修改 generate_video.py（4 处）

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py:33-38,397,504-555,662,670-713,733,808-853,892,1010-1061`

- [ ] **Step 1: 移除 `is_worker_online_sync` 导入**

- [ ] **Step 2: 重构 `generate_episode_video`（第 397 行附近）**

移除 `queue_worker_online = is_worker_online()` 及相关 `if/else` 分支。
内部闭包 `generate_single_item` 统一走 try/except WorkerOfflineError 模式。

- [ ] **Step 3: 重构 `generate_scene_video`（第 662 行附近）**

同上，移除 `queue_worker_online` flag 和 `if/else` 分支。

- [ ] **Step 4: 重构 `generate_all_videos`（第 733 行附近）**

同上。

- [ ] **Step 5: 重构 `generate_selected_videos`（第 892 行附近）**

同上。

---

## Task 4: 运行测试验证

- [ ] **Step 1: 运行相关测试**

```bash
uv run python -m pytest tests/test_generation_queue.py tests/test_generation_queue_client.py -v
```

- [ ] **Step 2: 运行全部测试确认无回归**

```bash
uv run python -m pytest
```
