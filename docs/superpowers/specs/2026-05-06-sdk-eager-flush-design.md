# SDK 0.1.73 升级 + eager session_store_flush 接入设计

**日期**：2026-05-06
**状态**：设计稿（待用户复核）
**关联**：`claude-agent-sdk` 0.1.72 → 0.1.73；接续 `2026-05-01-sdk-session-store-design.md`

## 背景

当前 ArcReel 使用 `claude-agent-sdk` 0.1.72，transcript 写入默认 **batched flush**：一轮 turn 完整结束才把全部 entries 一次性写入 `DbSessionStore`。这意味着 **turn 进行中** DB 里没有这一轮的任何记录。

`server/agent_runtime/session_manager.py` 的 SSE 推送依赖 `ManagedSession.message_buffer`（in-memory，cap=100，stream_event 优先驱逐）+ `subscribe(replay_buffer=True)` 给重连客户端 replay。结果：

| 场景 | 现状 |
|---|---|
| 同进程 turn 进行中前端 reload | buffer 在内存可恢复，但出现 R1 / "user 消失"（详见根因分析） |
| 长 turn 跨 buffer cap=100 reload | stream_event 被驱逐；user/assistant/result 不被驱逐故 turn 仍可见，但流式细节缺一段 |
| 服务进程崩 / 重启 | buffer 全丢，DB 也无本轮 → 整轮 100% 丢失，会话被标 interrupted |
| 多 worker 跨进程 | 不支持（buffer 是进程内） |

### 已知 dedup 缺陷（R1 + "用户问题消失"同根）

`AssistantService._is_buffer_duplicate`（`service.py:654`）对 `local_echo` 的去重只查 DB transcript，不检查同一 buffer 内是否已存在 SDK 回放的 UserMessage（带 uuid）：

- **R1 双显**：turn 进行中刷新 → batched 模式 DB 还没 flush 这一轮 → `transcript_uuids` 不含这条 user → echo 与 sdk UserMessage 双双进入 projector
- **"user 消失"**：上一轮 user 文本恰好与本轮相同（如重复的"继续"指令）→ `_echo_in_transcript` 用上一轮 user 做 timestamp 比较 → 边界判错 → echo 被丢 + sdk UserMessage 还没从子进程 emit → 当轮 user 整条不见

### `claude-agent-sdk` 0.1.73 的新能力

`ClaudeAgentOptions.session_store_flush="eager"`（PR #905）：把 `build_mirror_batcher` 的两个 pending 阈值（500 entries / 1 MiB）清零，每条完整 frame（user / assistant / system / tool_result / result）都立即调度一次后台 `drain` → `SessionStore.append()`。

关键性质（来自 PR 描述）：
- **fire-and-forget**：drain 通过 `asyncio.ensure_future` 后台执行，不阻塞 SDK 读循环
- **lock 串行化**：batcher 内部锁保证 append 顺序
- **慢 store 自动 coalesce**：append 慢时后续 frame 在 batcher 里合并，不堆积
- **不含 partial stream_event**：只 flush 完整 frame，DB 里不会出现"半截 assistant"

这正是彻底治 R1 / "user 消失" + 服务重启 partial 丢失 + 跨 buffer-cap reload 三类问题的契机。

## 范围

| 决策项 | 结论 |
|---|---|
| SDK 版本 | `claude-agent-sdk>=0.1.73` |
| flush 模式 | 默认 `eager`，env `ARCREEL_SDK_SESSION_STORE_FLUSH=batched` 紧急回退 |
| dedup 修复 | 修 `_is_buffer_duplicate`：echo 不仅查 transcript，也查同 buffer 内 sdk UserMessage（兜底 DB 慢于 buffer 的窗口） |
| `message_buffer` 重构 | 不做（保留 cap=100 + replay；作为 follow-up 等多 worker 真做时一起改） |
| 多 worker 跨进程实时推送 | 不在范围（C 场景留 follow-up） |
| `MirrorErrorMessage` 渲染 | 不主动加（与 `2026-05-01` spec §6.2 解耦），生产观察后再决定 |
| 回滚周期 | env 开关保留 1–2 个版本（约 4 周），稳定后清理 |

## §1 总体架构

### 1.1 写入路径变化

```text
batched (现状)：
  ClaudeSDKClient (subprocess)
    ├─ wire stream → message_buffer (in-memory, cap=100)
    │     ├─ broadcast → SSE subscribers (live)
    │     └─ buffer 满时优先驱逐 stream_event
    └─ end-of-turn → flush ALL entries 一次性 → DbSessionStore.append()
                                                   ↓
                                               DB transcript

eager (本期)：
  ClaudeSDKClient (subprocess)
    ├─ wire stream → message_buffer (in-memory, cap=100)   ← 不变
    │     ├─ broadcast → SSE subscribers (live)
    │     └─ buffer 满时优先驱逐 stream_event
    └─ each complete frame → enqueue → drain (asyncio.ensure_future)
                                          ↓
                                      DbSessionStore.append()
                                          ↓
                                      DB transcript (近实时；慢时 SDK 自动 coalesce frames)
```

eager 模式下：
- buffer 仍是热路径（live broadcast + cap 内 replay）
- DB 与 buffer 几乎同时拥有同一条消息（uuid 一致）
- 长 turn 内 reload 时 history 已含本轮已完成的 user / assistant entries

### 1.2 重连路径

```text
SSE reconnect (前端 reload)
  → AssistantService.stream_events(session_id)
    → if status != running: emit_completed_snapshot (DB only)
    → if status == running:
        ├─ subscribe(replay_buffer=True) → drain replayed messages
        ├─ _build_projector(meta, session_id, replayed_messages)
        │   ├─ history = transcript_adapter.read_raw_messages (DB via store)
        │   ├─ projector.init_with(history) → committed turns 立刻就位
        │   ├─ pre-scan buffer → buffer_real_user_texts (NEW)
        │   ├─ for msg in buffer:
        │   │   if _is_buffer_duplicate(
        │   │       msg, transcript_uuids, tail_fps,
        │   │       history, buffer_real_user_texts (NEW),
        │   │   ):
        │   │     skip
        │   │   else:
        │   │     projector.apply_message(msg)
        │   └─ return projector
        └─ live loop: queue.get → projector.apply_message → SSE patch/delta
```

**eager 后的根本改进**：history 在 turn 进行中已含本轮的 user 和已完成的 assistant entries → `transcript_uuids` 包含这条 user 的 uuid → buffer 中：
- echo 走 `_echo_in_transcript(history)` 命中 → dedup 丢弃
- sdk UserMessage 走 uuid dedup 命中 → 丢弃
- → projector 只剩 history 中那一条 user

**dedup 兜底**（buffer_real_user_texts）覆盖一种残留窗口：eager 在慢 store 触发 coalesce 时 DB 短暂滞后于 buffer。此时 history 还没这条 user，但 buffer 里 echo + sdk UserMessage 都在；新增 pre-scan 让 echo 撞上 buffer 内同文 sdk UserMessage 也判 dup。

## §2 改动点清单

| 文件 | 改动 |
|---|---|
| `pyproject.toml` | `claude-agent-sdk>=0.1.73` |
| `uv.lock` | `uv lock --upgrade-package claude-agent-sdk` |
| `lib/agent_session_store/__init__.py` | 新增 `session_store_flush_mode()`：解析 env `ARCREEL_SDK_SESSION_STORE_FLUSH`，返回 `"eager"` / `"batched"` |
| `server/agent_runtime/session_manager.py` `_build_options` | `ClaudeAgentOptions(..., session_store_flush=session_store_flush_mode())` |
| `server/agent_runtime/service.py` `_build_projector` | 增加 buffer pre-scan，把 `buffer_real_user_texts` 传给 dedup |
| `server/agent_runtime/service.py` `_is_buffer_duplicate` | 签名加 `buffer_real_user_texts` 参数；echo dedup 链增加"撞上 buffer 内同文 sdk user"路径 |
| `server/agent_runtime/service.py` `_collect_buffer_real_user_texts` | 新增 helper，扫 buffer 提取所有 type=user 且非 local_echo 的纯文本 |
| `tests/agent_runtime/test_session_store_e2e.py` | 加 eager 模式下 turn-中 reload / 服务重启恢复 / 长 turn 驱逐恢复测试 |
| `tests/agent_runtime/test_dedup_user_echo.py`（新建） | 专门覆盖 R1 + "user 消失" 的回归 |
| `tests/agent_runtime/test_session_manager_store_injection.py` | 加 `session_store_flush` 透传到 `ClaudeAgentOptions` 的测试 |

## §3 dedup 增强细节

### 3.1 现状（`service.py:654`）

```python
def _is_buffer_duplicate(
    self, msg, msg_type, transcript_uuids, tail_fps, history_messages,
):
    # 1. UUID dedup
    uuid = msg.get("uuid")
    if uuid and uuid in transcript_uuids:
        return True
    # 2. Local echo dedup —— 只查 transcript
    if msg.get("local_echo") and self._echo_in_transcript(msg, history_messages):
        return True
    # 3. Content fingerprint dedup
    ...
```

### 3.2 改造后

```python
def _is_buffer_duplicate(
    self, msg, msg_type, transcript_uuids, tail_fps, history_messages,
    buffer_real_user_texts: set[str],   # NEW
):
    # 1. UUID dedup（不变）
    uuid = msg.get("uuid")
    if uuid and uuid in transcript_uuids:
        return True

    # 2. Local echo dedup —— transcript 优先，buffer 兜底
    if msg.get("local_echo"):
        if self._echo_in_transcript(msg, history_messages):
            return True
        echo_text = self._extract_plain_user_content(msg)
        if echo_text and echo_text in buffer_real_user_texts:
            return True   # NEW: echo 撞 buffer 内同文真实 user

    # 3. Content fingerprint dedup（不变）
    ...
```

新 helper：

```python
@staticmethod
def _collect_buffer_real_user_texts(buffer: list[dict[str, Any]]) -> set[str]:
    """提取 buffer 中所有 type=user 且非 local_echo 的纯文本，供 echo dedup 使用。"""
    texts: set[str] = set()
    for msg in buffer or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("type") != "user" or msg.get("local_echo"):
            continue
        text = AssistantService._extract_plain_user_content(msg)
        if text:
            texts.add(text)
    return texts
```

### 3.3 dedup 决策矩阵（验证闭合）

| 场景 | history 含本轮 user | buffer 含 echo | buffer 含 sdk user | 期望 projector | 验证 |
|---|---|---|---|---|---|
| eager 正常（DB 实时）| ✓ | ✓ | ✓ | 1 条（来自 history） | echo 走 `_echo_in_transcript` ✓；sdk user 走 uuid dedup ✓ |
| eager 慢 store / batched | ✗ | ✓ | ✓ | 1 条（来自 buffer 的 sdk user） | echo 走 `buffer_real_user_texts` 命中 ✓；sdk user 不被 dedup → 保留 ✓ |
| 极早重连（sdk user 还没 emit）| ✗ | ✓ | ✗ | 1 条（echo） | echo 都不命中 → 保留 ✓ |
| 上一轮 same-text user（防误判）| ✓（本轮）| ✓ | ✓ | 1 条 | history 的 last real user 是本轮（eager 已 flush）→ timestamp 比较通过 ✓ |

**关键不变量**：echo 永远只在"已有真实对应 user"时才被丢；真实 user（带 uuid）永远不丢，是真相源。

### 3.4 范围限定

dedup 增强**仅作用于 reconnect 重建路径**（`_build_projector`）。LIVE 路径（`_dispatch_live_message`）的消息流不经过此 dedup —— 若 LIVE 路径下也有 R1，那是 `stream_projector` / `turn_grouper` 内部逻辑问题，**不在本期范围**，留单独 issue 跟进。

## §4 错误处理

### 4.1 SessionStore.append 错误

eager 模式下 append 调用频率从"每 turn 1 次"→"每完整 frame 1 次"。当前 `DbSessionStore.append` 已有 16 次 retry + 指数退避（针对 seq PK 竞争），SDK 内部还有 3 次 retry。仍失败时 SDK 发 `MirrorErrorMessage` 系统消息。

**本期不主动处理 mirror_error**（与 `2026-05-01-sdk-session-store-design.md` §6.2 解耦）。生产部署后观察日志：
- `arcreel.session_store` 出现 `append failed` ERROR → 收集统计
- 频率 > 阈值（待经验定）→ 启动 follow-up 实现 `MirrorErrorMessage` 在 `stream_projector` 中的告警 turn 渲染

### 4.2 慢 DB 时 SDK 自动 coalesce

PR #905 描述：append 是 fire-and-forget + lock 串行化，慢时 frame 在 batcher 里合并。**ArcReel 不需要自己加节流逻辑**。

### 4.3 多次 reconnect 重建开销

eager 写入 + frontend 多次 reconnect：每次 reconnect 都要重建 projector + 拉 transcript。`_snapshot_cache` 仅缓存 terminal 会话（`status != "running"`），running 会话每次都重读 DB。

ArcReel 当前规模可接受。若未来出现"频繁重连导致的 DB 读放大"，加 running 会话的短 TTL（~500ms）snapshot cache 即可。**不在本期**。

## §5 回滚开关

```python
# lib/agent_session_store/__init__.py
import logging
import os

logger = logging.getLogger("arcreel.session_store")

_VALID_FLUSH_MODES = {"eager", "batched"}

def session_store_flush_mode() -> str:
    """SDK ClaudeAgentOptions.session_store_flush 取值。"""
    raw = os.getenv("ARCREEL_SDK_SESSION_STORE_FLUSH", "").strip().lower()
    if raw == "batched":
        return "batched"
    if raw and raw not in _VALID_FLUSH_MODES:
        logger.warning(
            "Unknown ARCREEL_SDK_SESSION_STORE_FLUSH=%r; defaulting to eager",
            raw,
        )
    return "eager"
```

`session_manager._build_options`：

```python
return ClaudeAgentOptions(
    cwd=str(project_cwd),
    ...
    session_store=self._build_session_store(),
    session_store_flush=session_store_flush_mode(),   # NEW
)
```

回滚路径：`ARCREEL_SDK_SESSION_STORE_FLUSH=batched` 重启服务即可，**无代码改动 / 无数据迁移**。dedup 增强代码与 flush 模式正交（即使 batched 模式下也是更鲁棒的 dedup），不需要回滚。

## §6 测试矩阵

```text
tests/agent_runtime/
├── test_session_store_e2e.py（已存在）
│   + test_eager_flush_persists_per_frame
│       — 起 mock SDK，eager 模式发 1 个 turn，验证 DB 在 turn 内已有 entries
│
│   + test_reconnect_during_running_turn_no_user_dup           # R1 回归
│       — turn 进行中 reconnect，projector turns 中 user 仅 1 条
│
│   + test_reconnect_during_running_turn_user_visible          # "user 消失" 回归
│       — 上一轮已存在 same-text user；本轮 turn 进行中 reconnect；本轮 user 必须可见
│
│   + test_service_restart_partial_transcript_visible          # crash durability
│       — eager 写入 N 条 → 模拟进程重启 → DB 仍含这 N 条；会话标 interrupted；
│         前端 GET snapshot 看到 partial transcript
│
│   + test_long_turn_buffer_eviction_recoverable_via_db        # L2 缓解
│       — 单 turn 触发 buffer cap=100 驱逐 stream_event；reload 后从 DB 拿回
│         完整 user/assistant/result 序列
│
├── test_dedup_user_echo.py（新文件）
│   + test_echo_dedup_against_transcript_real_user
│       — echo 撞 history 内同文 user → dedup
│
│   + test_echo_dedup_against_buffer_real_user_when_db_lags
│       — history 不含；buffer 内 echo + 同文 sdk user → echo 被 dedup，sdk user 保留
│
│   + test_echo_with_same_text_as_prior_round_not_misclassified
│       — 上一轮 user 文本与本轮相同；本轮 sdk user 已落 history → echo 应 dedup（不是误判）
│
│   + test_collect_buffer_real_user_texts_excludes_local_echo
│       — helper 单测：local_echo / non-user / image-only 都不进集合
│
│   + test_echo_preserved_when_no_real_user_anywhere
│       — history 空、buffer 只有 echo 自身 → 必须保留
│
└── test_session_manager_store_injection.py（已存在）
    + test_eager_mode_passed_to_options
        — 默认环境下 ClaudeAgentOptions.session_store_flush == "eager"
    + test_batched_mode_via_env_passes_through
        — ARCREEL_SDK_SESSION_STORE_FLUSH=batched → "batched"
    + test_unknown_mode_falls_back_to_eager_with_warning
        — 设非法值 → 警告 + eager
```

**回归保护**：`tests/test_assistant_service_more.py::test_merge_and_dedup_helpers`（已有）必须保持绿色，dedup 重构不能破坏老路径。

## §7 验收标准

| # | 标准 |
|---|---|
| 1 | `uv sync` 成功，`uv run python -c "from claude_agent_sdk import ClaudeAgentOptions; ClaudeAgentOptions(session_store_flush='eager')"` 不抛异常 |
| 2 | `uv run python -m pytest tests/agent_runtime/` 全绿（含 §6 全部新测试） |
| 3 | 默认启动（无 env）：`server/agent_runtime/session_manager.py` 透传 `session_store_flush="eager"` 到 `ClaudeAgentOptions`（unit test 验证） |
| 4 | `ARCREEL_SDK_SESSION_STORE_FLUSH=batched` 启动 → 行为退化到 0.1.72 现状（unit test + 手动验证） |
| 5 | 手动 reproduce：发消息 → 立即 reload → user 不消失 + 不双显 |
| 6 | 手动 reproduce：长 turn 跑到 buffer 驱逐部分 stream_event → reload → assistant turn 主体可见（流式打字机细节缺失可接受） |
| 7 | 手动 reproduce：发消息 → `kill -9 服务` → 重启 → 进会话 → 看到中断前的 partial transcript（会话状态 `interrupted`） |
| 8 | `tests/test_assistant_service_more.py::test_merge_and_dedup_helpers` 绿色（回归保护） |

## §8 非目标

- **不**重构 `ManagedSession.message_buffer` 为 "DB 已读后的尾部窗口"（方案 B，留 follow-up）
- **不**实现多 worker 跨进程实时推送（C 场景，留 follow-up：Redis pubsub / PG LISTEN / DB tail）
- **不**主动新增 `MirrorErrorMessage` 在 `stream_projector` 中的识别 / 渲染（与 `2026-05-01` spec 解耦）
- **不**修 LIVE 路径下可能存在的 R1 / R2（如有；本期仅修 reconnect 重建路径）
- **不**为 running 会话引入短 TTL snapshot cache
- **不**引入 Prometheus / 自定义指标

## §9 风险与缓解

| 风险 | 缓解 |
|---|---|
| eager 下 SQLite 单写入器场景写竞争 | SDK 自带慢 store coalesce + `DbSessionStore._append_once` 事务短；超规模时切 PG |
| 新 dedup 逻辑误丢 echo | §6.2 测试矩阵双向覆盖：既不能双显也不能消失；`test_echo_preserved_when_no_real_user_anywhere` 兜底正向用例 |
| SDK 0.1.73 之后再改 `session_store_flush` 字段名 / 取值 | 升级前订阅 SDK CHANGELOG；conformance test 兜底；本 spec 把字段名隔离在 `session_store_flush_mode()` 单点 |
| `_echo_in_transcript` 跨 round timestamp 比较仍敏感 | 新 dedup 路径优先匹配同 buffer 内 real user（不依赖 timestamp），减少误判 surface；timestamp 比较仅作为 transcript 路径的兜底 |
| eager 后 mirror error 频次升高 | 留 follow-up；本期靠日志监控触发 |
| 长 turn 重连 transcript 读放大 | 现状 `_snapshot_cache` 已 LRU 128；本期不动 running 会话的 cache 策略 |

## §10 Follow-up（不在本期范围）

- **buffer 重构**：`ManagedSession.message_buffer` → "DB 已读 watermark 之后的尾部窗口"，buffer 缩到 ~20 条 stream_event；reconnect 完全靠 DB 历史
- **多 worker 跨进程实时推送**：Redis pubsub / PG `LISTEN/NOTIFY` / DB tail（与 buffer 重构联动）
- **`MirrorErrorMessage` 渲染**：在 `stream_projector` 中识别并转为前端可见的告警 turn
- **Prometheus 指标**：`session_store.mirror_errors_total` / `session_store.append_p99_ms`
- **LIVE 路径 R1 / R2 残留排查**：若手动复现仍能触发，单独 issue
- **稳定后清理**：移除 `ARCREEL_SDK_SESSION_STORE_FLUSH=batched` 兜底分支
