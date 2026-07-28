# SDK SessionStore 接入：transcript 镜像入库设计

**日期**：2026-05-01
**状态**：设计稿（待用户复核）
**关联升级**：`claude-agent-sdk` 0.1.61 → 0.1.71（PR #445）

## 背景

`server/agent_runtime/sdk_transcript_adapter.py:19` 当前依赖 SDK 私有 API
`claude_agent_sdk._internal.sessions._read_session_file` 读取本地 jsonl，仅为了补回
`get_session_messages()` 公开 API 没暴露的 transcript-level timestamp。代码注释自承
"Prefer replacing this with a public SDK API once one is available"。SDK 升级随时
可能改动 `_internal/`，存在隐患。

`claude-agent-sdk` 0.1.64–0.1.65 引入了完整的 `SessionStore` 协议体系：
`append/load/list_sessions/list_session_summaries/delete/list_subkeys`，配套 9 个
`*_from_store` / `*_via_store` helper 与 `claude_agent_sdk.testing.run_session_store_conformance`
契约测试。这正是消除私有 API 依赖、并把 transcript 持久化统一进项目数据库
（`lib/db/`）的时机。

## 范围

经过澄清确认：

| 决策项 | 结论 |
|---|---|
| 改造深度 | B：自定义 SessionStore + transcript 入项目 DB（不仅仅替换私有 API） |
| 表粒度 | Z：行级 entries 表 + 一行/会话 summaries 表（fold_session_summary 维护） |
| 本地 jsonl 副本 | P：保留默认路径，由 SDK `cleanupPeriodDays` 自动清理 |
| 历史数据迁移 | 启动钩子（FastAPI lifespan）一次性导入，幂等 |
| 数据库方言 | 不绑死 PG，走 `lib/db/engine.py`，dev SQLite / prod PG 同一份代码 |
| 启用方式 | 默认开启，环境变量 `ARCREEL_SDK_SESSION_STORE=off` 可回滚 |
| 旧路径清理 | 留 1–2 个版本兜底，稳定后删除 `_internal._read_session_file` 依赖 |

## §1 总体架构

```
┌──────────── ClaudeSDKClient (子进程) ───────────────────┐
│ ① 写本地 jsonl (~/.claude/projects/...)                │  ← 保留兜底
│ ② 通过 ClaudeAgentOptions.session_store                 │
│    .append() 镜像副本 (~100ms 批量)                     │
└────────────────┬───────────────────────────────────────┘
                 │ entries (JSON dict list)
                 ▼
┌──────── DbSessionStore (新增) ─────────────────────────┐
│ append() → INSERT INTO agent_session_entries          │
│            UPSERT INTO agent_session_summaries        │
│ load()   → SELECT ... ORDER BY seq                    │
│ list_sessions / list_session_summaries / delete       │
│ list_subkeys                                          │
└────────────────┬───────────────────────────────────────┘
                 │ AsyncSession (lib/db/engine.py)
                 ▼
       agent_session_entries (行级，一行一条 entry)
       agent_session_summaries (一行/会话，fold_session_summary 输出)
       agent_sessions (现有，业务索引层不变)
```

读取侧：

```
SdkTranscriptAdapter
  ─ 旧：get_session_messages + _internal._read_session_file (私有 API)
  ─ 新：get_session_messages_from_store(store, key) (公开 helper)
        + entries 自带 timestamp，无需再读 raw jsonl
        ⇒ 删除 _load_timestamps() 与对 _read_session_file 的依赖
```

新增模块布局：

```
lib/agent_session_store/
  __init__.py              # 导出 DbSessionStore, make_project_key
  store.py                 # DbSessionStore 实现 SDK Protocol
  models.py                # AgentSessionEntry / AgentSessionSummary ORM
  import_local.py          # 启动钩子：本地 jsonl → store 一次性迁移
  conformance_test.py      # 跑 SDK 官方 run_session_store_conformance
```

**关键点**：
- 新代码进 `lib/`（按 CLAUDE.md「核心库归 lib/」），不进 `server/`
- `agent_sessions` 表保留，定位是**业务索引**（project_name/title/status/user_id）；
  `agent_session_entries` 是 **SDK transcript 镜像**，两者通过 `sdk_session_id` 关联
  但不强外键（SDK 也允许会话只在 store 里、没有业务索引）

## §2 数据模型

```python
# lib/agent_session_store/models.py
from sqlalchemy import BigInteger, Index, JSON, PrimaryKeyConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin, UserOwnedMixin


class AgentSessionEntry(TimestampMixin, UserOwnedMixin, Base):
    """SDK transcript 镜像，一行一条 SessionStoreEntry。"""
    __tablename__ = "agent_session_entries"

    project_key: Mapped[str]      = mapped_column(String, nullable=False)
    session_id:  Mapped[str]      = mapped_column(String, nullable=False)
    subpath:     Mapped[str]      = mapped_column(String, nullable=False, server_default="")
    seq:         Mapped[int]      = mapped_column(BigInteger, nullable=False)
    uuid:        Mapped[str|None] = mapped_column(String, nullable=True)
    entry_type:  Mapped[str]      = mapped_column(String, nullable=False)
    payload:     Mapped[dict]     = mapped_column(JSON, nullable=False)
    mtime_ms:    Mapped[int]      = mapped_column(BigInteger, nullable=False)  # 与 summaries.mtime_ms 同源
    # created_at / updated_at  ← TimestampMixin（调试可读时间）
    # user_id                  ← UserOwnedMixin (FK users.id ON DELETE CASCADE)

    __table_args__ = (
        PrimaryKeyConstraint("project_key", "session_id", "subpath", "seq"),
        # 幂等键：SDK 协议规定 uuid 唯一时按 uuid upsert。NULL 不参与去重。
        Index(
            "uq_agent_entries_uuid",
            "project_key", "session_id", "subpath", "uuid",
            unique=True,
            postgresql_where=text("uuid IS NOT NULL"),
            sqlite_where=text("uuid IS NOT NULL"),
        ),
        Index("idx_agent_entries_listing", "project_key", "session_id", "mtime_ms"),
    )


class AgentSessionSummary(TimestampMixin, UserOwnedMixin, Base):
    """SDK fold_session_summary() 维护的快路径摘要。"""
    __tablename__ = "agent_session_summaries"

    project_key: Mapped[str]  = mapped_column(String, primary_key=True)
    session_id:  Mapped[str]  = mapped_column(String, primary_key=True)
    mtime_ms:    Mapped[int]  = mapped_column(BigInteger, nullable=False)
    data:        Mapped[dict] = mapped_column(JSON, nullable=False)  # 不透明，verbatim
    # created_at / updated_at / user_id  ← mixins
```

**设计约束**：

1. **`SessionKey` 三段全建模** — `project_key + session_id + subpath`。subpath 用空串
   而不是 NULL，避免 `(a, b, NULL) ≠ (a, b, NULL)` 的 SQL 三值逻辑陷阱。
2. **`seq` 单调递增、按 session 取号** — `SELECT COALESCE(MAX(seq), -1) + 1` 在事务里
   取号；不用 autoincrement 因为后者跨 session 不连续。
3. **`payload` 用 `JSON` 类型** — SQLAlchemy 在 PG 走 JSONB、SQLite 走 JSON1，方言差异
   由 ORM 抹平。
4. **uuid 部分唯一索引** — SDK 协议明确「无 uuid 的 entries（titles/tags/mode markers）
   不去重」，NULL 不能进唯一约束，PG/SQLite 都支持部分索引。
5. **不为 SDK 事件 timestamp 单独建列** — SDK 事件时间在 `payload["timestamp"]` 里
   原样透传，`_load_timestamps()` 那个补丁可以彻底删掉。**注意区分** `payload`
   里的 SDK 事件时间（ISO 字符串）与上面 `mtime_ms`（存储落库时刻、毫秒整数）：
   两者用途不同，不能互相替代。
6. **`mtime_ms` 不被 `updated_at` 取代** — SDK 协议明确要求毫秒整数，且
   `list_sessions` 与 `list_session_summaries` 必须同源时钟。`updated_at` 是
   TimestampMixin 维护的 timestamptz，单独留 `mtime_ms` 字段在写入时
   stamp `int(time.time() * 1000)`，避免转换歧义。
7. **`DbSessionStore` 按 user_id 绑定实例** — 不是单例。
   `session_manager._build_options` 每次构造时传当前 user_id：

```python
class DbSessionStore:
    def __init__(self, session_factory, *, user_id: str = DEFAULT_USER_ID):
        self._session_factory = session_factory
        self._user_id = user_id
```

**Alembic 迁移**：单独一份 `alembic revision --autogenerate -m "add session_store tables"`，
只加表不改现有表。

## §3 关键操作的数据流

### append（写入路径，最热）

```
SDK 子进程 ─ ~100ms 批量 ─▶ store.append(key, entries)
                                 │
                                 ▼  单事务
        ┌────────────────────────────────────────────────────┐
        │ 0) now_ms = int(time.time()*1000)                   │
        │ 1) seq_start = SELECT COALESCE(MAX(seq),-1)+1       │
        │      FROM agent_session_entries                    │
        │      WHERE project_key=? AND session_id=? AND      │
        │            subpath=?                               │
        │ 2) executemany INSERT ...                           │
        │      seq=seq_start+i, mtime_ms=now_ms               │
        │      ON CONFLICT (project_key,session_id,subpath,  │
        │      uuid) DO NOTHING   -- uuid 部分唯一            │
        │ 3) 若 subpath == "":                                │
        │      prev = SELECT data,mtime_ms FROM               │
        │             agent_session_summaries ... FOR UPDATE  │
        │      new  = fold_session_summary(prev, entries)     │
        │      UPSERT agent_session_summaries                 │
        │      mtime_ms = now_ms   -- 与 entries 同源         │
        │ 4) COMMIT                                           │
        └────────────────────────────────────────────────────┘
```

**并发控制**：SDK 协议明文「同一 session 的 append 可能竞争，store 必须 serialize」。
采用 **行级锁**：summaries 表 `SELECT ... FOR UPDATE` 锁住该 session 行；entries 表
seq 取号靠唯一 PK 兜底（即便竞争插入失败也能在重试时拿到新号）。

- SQLite：走 `BEGIN IMMEDIATE` 隐式锁库（无行级锁，但单写入器够用）
- PG：真正的行锁
- 方言差异由 SQLAlchemy `with_for_update()` 抹掉

**失败语义**：抛异常 → SDK 重试 3 次 → 仍失败发 `MirrorErrorMessage`，子进程不受影响。
我们什么都不用做，只需保证异常如实抛出（**不要 try/except 吞掉**）。

### load（resume 时一次性拉全 transcript）

```
SDK ─▶ store.load(key)
         │
         ▼
   SELECT payload FROM agent_session_entries
     WHERE project_key=? AND session_id=? AND subpath=?
     ORDER BY seq
   → list[dict] 或 None（无行时返回 None，区分"从未写入"）
```

**注意**：返回 `None` ≠ 返回 `[]`。SDK 用这个区分「会话不存在」和「会话存在但被清空」。

### list_sessions / list_session_summaries

```
list_sessions(project_key)        — 用于 fallback 路径
  rows = SELECT session_id, MAX(mtime_ms) AS mtime
           FROM agent_session_entries
           WHERE project_key=? AND subpath=''
           GROUP BY session_id
  return [{"session_id": r.session_id, "mtime": r.mtime}
          for r in rows]   # mtime 必须与 summaries.mtime_ms 同源时钟

list_session_summaries(project_key)  — 快路径，助手列表页
  SELECT session_id, mtime_ms, data
    FROM agent_session_summaries
    WHERE project_key=?
```

> **Schema 增量**：entries 表需新增 `mtime_ms BIGINT NOT NULL` 列（与 summaries 同源
> `int(time.time()*1000)`，append 时 stamp）。`updated_at`（TimestampMixin）保留作
> 调试可读时间，不参与 SDK 协议返回。

**只有后者上业务热路径**：`server/agent_runtime/service.py` 里 `list_sessions` 调用改为
`list_sessions_from_store(store, project_key)`，SDK helper 内部会优先走 summaries。

### delete / list_subkeys

```
delete(key)
  if key.subpath == "":
      DELETE FROM agent_session_entries
        WHERE project_key=? AND session_id=?  -- 级联所有 subpath
      DELETE FROM agent_session_summaries
        WHERE project_key=? AND session_id=?
  else:
      DELETE FROM agent_session_entries
        WHERE project_key=? AND session_id=? AND subpath=?

list_subkeys(key)
  SELECT DISTINCT subpath
    FROM agent_session_entries
    WHERE project_key=? AND session_id=? AND subpath != ''
```

### project_key 来源

**完全由 SDK 决定，不自定义。** SDK 在 live mirror 路径
（`session_resume.py:130`）用 `project_key_for_directory(options.cwd)` 算 key，
内部是 realpath + NFC + djb2 hashed `_sanitize_path`。如果我们在读取侧自创格式，
helper 会查不到 SDK 写入的数据。

```python
from claude_agent_sdk import project_key_for_directory   # 公开 API

def make_project_key(project_cwd: Path | str) -> str:
    return project_key_for_directory(str(project_cwd))
```

放一个 thin wrapper 在 `lib/agent_session_store/__init__.py`，`session_manager`
在构造调用 `*_from_store` helper 时统一用它（避免 SDK 这个 API 的 import 散落）。

**多用户隔离不靠 project_key**：
- ArcReel 当前每个项目独立 cwd（`projects/<project_name>/`），项目间天然隔离
- `user_id` 字段保留在 `agent_session_entries` / `agent_session_summaries` 表里，作用
  是 FK CASCADE 删账户级联；不参与 SessionKey
- 若未来需要「同项目跨用户独立会话」，按用户分 cwd（如 `projects/<user_id>/<project_name>/`）
  即可，对本设计零侵入

## §4 调用面改造与回滚开关

### 4.1 改动点清单

| 文件 | 改动 |
|---|---|
| `server/agent_runtime/session_manager.py` `_build_options` | 构造 `DbSessionStore`，传入 `ClaudeAgentOptions(session_store=...)` |
| `server/agent_runtime/sdk_transcript_adapter.py` | `get_session_messages` → `get_session_messages_from_store(store, key)`；签名补充 `project_cwd` 参数用于 `make_project_key`；删除 `_load_timestamps()` 与 `_internal._read_session_file` import |
| `server/agent_runtime/service.py` | `list_sessions` / `delete_session` 替换为 `list_sessions_from_store` / `delete_session_via_store` |
| `lib/agent_session_store/` | 新增（store + models + key 工具 + import_local + conformance test） |
| `alembic/versions/xxxx_add_session_store_tables.py` | 新增两表 |
| `server/app.py` lifespan | 启动钩子调用 `migrate_local_transcripts_to_store()` |

### 4.2 SDK helper 与公开 API 的搭配

之前直接调用的 SDK 函数 → store 化对应：

```
list_sessions(cwd=...)            ──▶ list_sessions_from_store(store, project_key)
get_session_messages(sid)         ──▶ get_session_messages_from_store(store, key)
delete_session(sid)               ──▶ delete_session_via_store(store, key)
tag_session(sid, tag)             ──▶ 保持原 API（SDK 内部写一条 entry，自动镜像）
```

`tag_session` 不改的理由：SDK 内部就是写一条 entry，镜像机制天然覆盖；少改一处。

### 4.3 回滚开关

新增环境变量：`ARCREEL_SDK_SESSION_STORE`（默认 `"db"`，可设 `"off"`）

```python
# session_manager._build_options
def _build_session_store(self) -> SessionStore | None:
    mode = os.getenv("ARCREEL_SDK_SESSION_STORE", "db")
    if mode == "off":
        return None  # 退化到 SDK 默认行为（读写本地 jsonl）
    return DbSessionStore(get_async_session_factory(), user_id=self._user_id)
```

读取侧也要双路：

```python
# sdk_transcript_adapter
class SdkTranscriptAdapter:
    def __init__(self, store: SessionStore | None):
        self._store = store

    def read_raw_messages(self, sdk_session_id):
        if self._store is not None:
            return self._read_via_store(sdk_session_id)
        return self._read_via_local_jsonl(sdk_session_id)  # 旧逻辑保留
```

为什么留旧路径而不是直接删：

- 出问题 5 秒回滚（设环境变量重启），不用回滚代码
- 老 jsonl 数据在 P 方案里仍然存在；如果用户当前会话恰好只在本地 jsonl 没在 store，
  关掉 store 还能继续工作
- 跑稳一两个版本后，再清理 `_read_via_local_jsonl` + 私有 `_read_session_file` 死路

### 4.4 启用阶段

| 阶段 | 默认值 | 验证 |
|---|---|---|
| 首发 | `ARCREEL_SDK_SESSION_STORE=db` 默认开 | 全量回归测试 + 1–2 周生产观察 |
| 稳定后（下下个版本） | 删除环境变量与 `_read_via_local_jsonl` 兜底；移除 `_internal._read_session_file` import | 一次性清理 PR |

## §5 历史数据迁移（启动钩子）

### 5.1 入口

`server/app.py` 的 FastAPI `lifespan` 中，在数据库 schema 就绪之后、worker 启动之前
跑一次：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_schema()
    await migrate_local_transcripts_to_store()   # 新增
    await start_generation_worker()
    yield
    ...
```

### 5.2 模块布局

```
lib/agent_session_store/
  import_local.py
    ├─ migrate_local_transcripts_to_store(store, projects_root) # 顶层入口
    ├─ _enumerate_local_session_ids(project_cwd) -> Iterable[str]
    │     # 内部仅调用 SDK 公开 list_sessions(directory=project_cwd)
    ├─ _is_already_migrated(store, project_cwd, session_id) -> bool
    │     # store.load(project_key_for_directory(cwd) + session_id) is not None
    └─ _migrate_one(store, project_cwd, session_id) -> None
          # await import_session_to_store(session_id, store, directory=str(project_cwd))
```

**完全用 SDK 公开 API**（`list_sessions` / `import_session_to_store` /
`project_key_for_directory`），不触碰任何 `_internal` 模块、不做路径硬编码、
不复刻 `_sanitize_path` 算法、不假设 jsonl 文件命名。

### 5.3 流程

```
migrate_local_transcripts_to_store(store, projects_root)
  ├─ marker = data_dir / ".session_store_migration_done"
  ├─ if marker.exists(): return early   # 幂等，热路径零开销
  │
  ├─ 多 worker 并发保护：INSERT INTO config(key='session_store_migration_lock', ...)
  │   ON CONFLICT DO NOTHING；未拿到锁直接 return
  │
  ├─ for project_cwd in projects_root.iterdir():
  │   if not project_cwd.is_dir(): continue
  │
  │   # SDK 自己解析 ~/.claude/projects/<sanitized>/ 或 CLAUDE_CONFIG_DIR
  │   # 自己处理 _sanitize_path、git worktree fallback 等细节
  │   try:
  │     sessions = list_sessions(directory=str(project_cwd))
  │   except Exception:
  │     logger.exception(...); continue
  │
  │   for info in sessions:
  │     try:
  │       if await _is_already_migrated(store, project_cwd, info.session_id):
  │         skipped += 1; continue
  │       # SDK 自己负责定位 jsonl + 流式批量 append + subagent + .meta.json sidecar
  │       await import_session_to_store(
  │           info.session_id, store, directory=str(project_cwd)
  │       )
  │       imported += 1
  │     except Exception:
  │       logger.exception("failed to migrate session=%s", info.session_id)
  │       failed += 1
  │       # 不抛，单条失败不影响整体启动
  │
  ├─ logger.info("transcript migration: %d imported / %d skipped / %d failed",
  │              imported, skipped, failed)
  └─ marker.write_text(json.dumps({"completed_at": ..., "stats": ...}))
```

### 5.4 关键决策

1. **零路径硬编码** — `~/.claude/projects/` 还是 `CLAUDE_CONFIG_DIR/projects/` 还是
   docker 挂载点完全由 SDK 决定，迁移代码只传 ArcReel 的项目 cwd 给 SDK。
2. **零文件名假设** — 不 `glob("*.jsonl")`、不 `.stem`、不假定 subagent 子目录结构。
   `import_session_to_store` 自己处理（含 subagent transcripts + `.meta.json` sidecar）。
3. **Marker 放 `data_dir/.session_store_migration_done`** — 与 `agent_runtime` 数据
   目录同侧，docker volume 自然带；不写 SDK 私域。
4. **幂等双保险** — Marker 阻挡重复扫描（热路径快）；`_is_already_migrated` 通过
   `store.load(key) is not None` 兜底（marker 误删时不会重复 import）。`store.load`
   走的是我们自己的 `agent_session_entries` 表，与 SDK 公开协议契约一致。
5. **单条失败不阻断启动** — 一条 jsonl 损坏 / SDK 解析失败不能让服务起不来；失败
   计数 + 日志。
6. **worker 并发** — uvicorn 多 worker 启动时多个进程同时跑 lifespan。`marker` 单独
   不够（race condition），叠加 `config` 表的锁行（`ON CONFLICT DO NOTHING`）实现
   跨进程互斥。
7. **零旧数据用户** — `list_sessions(directory=project_cwd)` 返回空列表直接进入下个
   项目；全部为空时写 marker，下次启动连 SDK 调用都省了。

### 5.5 验收标准

| 场景 | 期望行为 |
|---|---|
| 首次启动，有 N 个旧会话 | 全部 import，marker 写入；UI 列表显示新+旧 |
| 二次启动 | marker 命中，跳过；启动耗时无感知 |
| 部分 jsonl 损坏 | 损坏的跳过 + 日志；其他正常迁；marker 仍写入 |
| 多 worker 同时启动 | 一个 worker 拿锁干活，其他跳过 |
| 删除 marker 重启 | 重新扫描；DB 里已有的通过 `_is_already_migrated` 跳过；只补漏 |

## §6 错误处理与可观测性

### 6.1 三类错误的分工

| 错误源 | 谁报 | 我们做什么 |
|---|---|---|
| `store.append()` 抛异常 | SDK 内部 retry 3 次 → 仍失败发 `MirrorErrorMessage` 系统消息 | **日志记录原始异常**；不重试不吞掉。`MirrorErrorMessage` 在 `stream_projector` 里识别并转成 `system` turn 推前端 |
| `store.load()` 抛异常 | SDK 在 resume 失败 → 抛到 `ClaudeSDKClient` 上层 | `session_manager` 捕获 → 标记会话状态 `error`，前端显示「会话恢复失败，建议新建」 |
| 启动迁移失败 | `migrate_local_transcripts_to_store` 内部捕获单条异常 | 日志 + 计数；marker 仍写入；启动继续 |

### 6.2 `MirrorErrorMessage` 在 stream 中的处理

SDK 把 mirror 失败包成 `SystemMessage(subtype="mirror_error")` 注入消息流。
`stream_projector` 当前对 system 消息透传，**需要新增识别**：

```python
# stream_projector.py
def _is_mirror_error(event: dict) -> bool:
    return event.get("type") == "system" and event.get("subtype") == "mirror_error"

# 处理：渲染为前端可见的告警 turn（不是 fatal，会话仍能继续）
```

**为什么不静默吞掉**：mirror 失败 = DB 落库出问题，但子进程本地 jsonl 已写入
（P 方案），数据没真丢。但用户不知道「这次重启后历史会缺一段」——必须前端可见，
否则下次 load 才发现历史不全就太晚了。

### 6.3 日志规范

`lib/agent_session_store/store.py` 用 `logger = logging.getLogger("arcreel.session_store")`：

```
INFO  append: session=<id> entries=<n> seq_start=<seq>
WARN  append failed (will be retried by SDK): session=<id> err=<...>
ERROR load failed: session=<id> err=<...>
INFO  delete: session=<id> subpath=<...> rows=<...>
INFO  migrate: imported=<i> skipped=<s> failed=<f>
```

不打 entry 内容（可能含用户隐私 + 数据量大）。

### 6.4 指标

不引入 Prometheus，先用 `lib/db/models/config.py` 做计数器存（项目模式一致）：

- `session_store.mirror_errors_total`
- `session_store.append_p99_ms`（采样写）

后续接入 Prom 时直接迁。**也可以这一期不做指标**，只留日志 + 告警靠 ops 抓 ERROR
关键字。最终决策放到 implementation plan 里。

## §7 测试策略

### 7.1 三层测试

```
┌─────────────────────────────────────────────────────────┐
│ Layer 1: SDK 官方 conformance                           │
│   tests/agent_session_store/test_conformance.py         │
│   from claude_agent_sdk.testing import                  │
│       run_session_store_conformance                     │
│   await run_session_store_conformance(make_store)       │
│   → 13 项契约测试，覆盖 append/load/list/delete 协议    │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│ Layer 2: 项目侧单测                                     │
│   - test_pkey_encoding.py: make_project_key 编解码      │
│   - test_seq_concurrency.py: 同 session 并发 append    │
│     → 全部成功 + seq 连续 + 无重复 uuid                 │
│   - test_summary_fold.py: append 后 summaries 表        │
│     的 mtime_ms 单调；fold_session_summary 调用幂等     │
│   - test_migration_idempotent.py: 跑两次 import_local  │
│     → 第二次零写入 + 计数 = 0                           │
│   - test_migration_corrupted_jsonl.py: 损坏文件不阻断   │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│ Layer 3: end-to-end 烟测                                │
│   tests/agent_runtime/test_session_store_e2e.py         │
│   - 起一个真的 ClaudeSDKClient（mock LLM 后端）         │
│   - 跑一轮对话 → DB 里 entries 行数 > 0                 │
│   - 杀掉 client → 用 sdk_session_id resume               │
│   - 验证 get_session_messages_from_store 拿回历史完整  │
└─────────────────────────────────────────────────────────┘
```

### 7.2 fixtures

`tests/conftest.py` 已有 async session 工厂；新增：

```python
@pytest.fixture
async def session_store(async_session_factory):
    return DbSessionStore(async_session_factory, user_id="test-user")

@pytest.fixture
async def fake_local_jsonl(tmp_path):
    """造一份逼真的 SDK 本地 jsonl 给迁移测试用"""
    ...
```

### 7.3 SQLite vs PG 双跑

CI 默认 SQLite。Layer 1 的 conformance 测试**必须在 PG 也跑一次**（dialect 差异如
`FOR UPDATE` 行为、JSONB vs JSON、部分索引语法）。

加一条 GitHub Actions matrix：

```yaml
matrix:
  db: [sqlite, postgres]
```

仅对 `tests/agent_session_store/` 目录跑双 dialect，其他测试继续 SQLite-only
（节省 CI 时间）。

### 7.4 不写的测试

- 不测 SDK 内部 retry 逻辑（SDK 自家责任）
- 不测 `MirrorErrorMessage` 的 SDK 侧生成（同上）
- 只测**我们对 retry 失败的反应**（日志+前端展示）

## 验收标准

| # | 标准 |
|---|---|
| 1 | `uv run python -m pytest tests/agent_session_store/` 全绿，含 SDK conformance |
| 2 | CI matrix 在 SQLite + PG 双方言下 conformance 全绿 |
| 3 | 升级前的旧会话在升级后首次启动通过迁移钩子全部入库，UI 列表可见 |
| 4 | 删除 `data_dir/.session_store_migration_done` + 重启 → 已迁的会话不重复入库 |
| 5 | `sdk_transcript_adapter.py` 不再 import `claude_agent_sdk._internal.sessions._read_session_file`（store 路径） |
| 6 | 模拟 DB 写入失败 → 前端能收到 `mirror_error` 告警 turn |
| 7 | 设 `ARCREEL_SDK_SESSION_STORE=off` 重启 → 退化到旧 jsonl 路径仍可用 |
| 8 | grep 整个仓库无 `_internal.sessions` 引用（清理阶段验收，本期不强制） |

## 非目标

- **不**把 `agent_sessions` 表合并进 `agent_session_entries`（业务索引层独立保留）
- **不**实现 `delete_session_via_store` 之外的级联删除策略（用户管理删账户由 FK CASCADE 处理）
- **不**接入 Prometheus 指标（留 follow-up）
- **不**重定向 `CLAUDE_CONFIG_DIR`（P 方案明确保留 SDK 默认路径）

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| SDK 0.1.71 之后再改 SessionStore Protocol | conformance test 第一时间炸；订阅 SDK CHANGELOG |
| 多 worker lifespan 并发跑迁移 | config 表锁 + marker 双重保护 |
| DB 写入慢拖累 SDK turn 时延 | append 在后台 100ms 批量，子进程不阻塞；DB 慢只触发 mirror 重试 |
| `fold_session_summary` 写 sidecar 时并发竞争 | summaries 表 `SELECT FOR UPDATE` 行锁串行化 |
| 旧 jsonl 命名/路径在 SDK 后续版本变化 | 迁移完全走 SDK 公开 API（`list_sessions` / `import_session_to_store` / `project_key_for_directory`），SDK 自己处理路径解析和 docker / `CLAUDE_CONFIG_DIR` 等部署差异 |

## Follow-up（不在本期范围）

- 删除 `_read_via_local_jsonl` 兜底路径与 `_internal._read_session_file` 引用
- 接入 Prometheus 指标（`mirror_errors_total` / `append_p99_ms`）
- 评估 `CLAUDE_CONFIG_DIR` 重定向到 `data_dir/sdk-cache`（Q 方案），便于 docker volume 备份
- 评估 `tag_session` 也走 store（消除对 SDK 公开 API 的依赖，但收益小）
