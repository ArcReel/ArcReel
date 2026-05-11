# Agent URL 配置优化与预设供应商目录设计

**作者**：Pollo
**日期**：2026-05-11
**状态**：Draft (pending review)
**关联**：Issue #476，用户反馈「配置 anthropic URL 后实际调用无效」与「DeepSeek 模型发现端点 vs Claude SDK 端点冲突」

## 1. 背景与问题

### 1.1 现有架构

ArcReel 中 Anthropic 配置的来源与消费链路：

```
AgentConfigTab (UI) → PATCH /api/v1/system/config
  → ConfigService.set_setting("anthropic_base_url", ...)
  → SystemSettingRepository (DB)
  → sync_anthropic_env(all_settings)
  → os.environ["ANTHROPIC_BASE_URL"] = ...
  → Claude Agent SDK (subprocess) 读取 env
```

模型发现走独立路径：

```
AgentConfigTab "Discover" 按钮 → POST /api/v1/custom-providers/discover-anthropic
  → _discover_anthropic(base_url, api_key)
  → ensure_anthropic_base_url(base_url) + "/v1/models"
```

### 1.2 用户反馈的三个真实问题

1. **`/anthropic` 后缀盲区**：DeepSeek、Kimi、GLM、阿里百炼、火山方舟、腾讯 LKEAP、小米 MiMo 等几乎所有国内代理网关，Claude SDK 必须打 `{root}/anthropic/v1/messages`；但模型发现 `/v1/models` 在根域。当前 `ensure_anthropic_base_url` 只剥 `/v1`、`/v1/messages`，不识别 `/anthropic`/`/api/anthropic`/`/apps/anthropic`/`/plan/anthropic`/`/coding/anthropic`/`/api/coding` 等子路径。结果两个端点不能共用一个 base_url。
2. **配置生效但调不通**：用户填的 URL 同步到 env，但 SDK 实际打 `/v1/messages` 时网关返回 404 或非 anthropic JSON（如 ark agent plan 是 OpenAI 兼容协议），缺少前置体检手段。
3. **Agent 配置页缺少真实连接测试**：`/custom-providers/test` 端点只跑 `models.list()`，对 anthropic 协议覆盖不足；Agent 配置页连这个都没有。

### 1.3 用户体验现状

UI 上 Anthropic 配置只有「单 base_url + 单 key + 单 model」，每次切换网关都要手动改 URL/复制 key。市面有 cc-switch 这类工具提供「预设供应商目录 + 多套凭证 + 一键切换」体验，本设计参考其形态。

---

## 2. 设计目标

1. **预设供应商目录**：内置主流 Anthropic 兼容网关（DeepSeek、Kimi、GLM、Hunyuan、MiniMax、火山方舟、阿里百炼、小米 MiMo、Anthropic 官方 等）的 `messages_url` / `discovery_url` / 推荐模型，用户从 chip 网格选一个即填好 URL。
2. **多套凭证并存 + active 切换**：参考 cc-switch，凭证以列表形式管理，UI 上一键切换 active；切换后新会话使用新凭证。
3. **真实连接测试**：发一条最小 messages 请求（`max_tokens=1, prompt="ping"`）+ 可选 discovery probe，结构化诊断 + 一键修复建议。
4. **自定义模式智能补全**：用户选「自定义」时，URL 派生 `messages_root` / `discovery_root`，probe 失败时尝试补 `/anthropic` 自愈（仅在自定义模式触发，不写库，仅作为修复建议）。
5. **修复 `discover-anthropic` 路径推断**：剥 `/anthropic` 后缀后再拼 `/v1/models`。

### 2.1 显式不做

- 自定义供应商页（OpenAI/Google）的 endpoint UX 改造（保留现状）。
- AWS Bedrock catalog entry（双密钥结构特殊，标 `not_in_catalog`，留作 `__custom__` 入口）。
- 多用户细粒度授权（沿用 `CustomProvider` 的 `CurrentUser` 鉴权）。
- 在跑 SDK session 的强制刷新（切换 active 后给 toast 提示，不强杀）。
- 凭证导入/导出（不在本轮）。

---

## 3. 架构

### 3.1 数据流

```
┌──────────────────────────────────────────────────────────────┐
│  AgentConfigTab (前端)                                       │
│  ├─ CredentialList (列表，每条一个 chip + active 标记)       │
│  ├─ AddCredentialModal (cc-switch 风格预设选择 → 表单)        │
│  └─ TestConnectionButton (每条凭证可单独测)                   │
└────────────┬─────────────────────────────────────────────────┘
             │ HTTP
┌────────────▼─────────────────────────────────────────────────┐
│  /api/v1/agent/* (新路由 server/routers/agent_config.py)     │
│  ├─ GET  /preset-providers                                   │
│  ├─ GET  /credentials                                        │
│  ├─ POST /credentials                                        │
│  ├─ PATCH /credentials/{id}                                  │
│  ├─ DELETE /credentials/{id}                                 │
│  ├─ POST /credentials/{id}/activate                          │
│  ├─ POST /credentials/{id}/test                              │
│  └─ POST /test-connection (草稿测试，未保存)                 │
└────────────┬─────────────────────────────────────────────────┘
             │
┌────────────▼─────────────────────────────────────────────────┐
│  Service 层                                                  │
│  ├─ AgentCredentialRepository (lib/db/repositories/)        │
│  ├─ derive_anthropic_endpoints (lib/config/anthropic_url.py)│
│  ├─ probe_anthropic_messages / probe_discovery (新模块)      │
│  └─ sync_active_credential_to_env (lib/config/service.py 改) │
└────────────┬─────────────────────────────────────────────────┘
             │
┌────────────▼─────────────────────────────────────────────────┐
│  DB                                                          │
│  ├─ agent_anthropic_credentials (新表)                       │
│  └─ system_settings (旧的 anthropic_* 仅作为兼容入口)        │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 配置生效路径

```
凭证 active 切换 → AgentCredentialRepository.set_active(id)
                 → sync_active_credential_to_env() 写 ANTHROPIC_BASE_URL/KEY/MODEL/...
                 → 后续新建的 SessionActor 子进程读到新 env
                 → 已运行 SessionActor 仍持有旧 env (子进程已 fork)
```

UI toast：「已切换到 X，新会话生效；当前会话继续使用旧凭证」。不强制 kill 现有 session。

---

## 4. 模块详细设计

### 4.1 `lib/agent_provider_catalog.py` (new)

```python
@dataclass(frozen=True)
class PresetProvider:
    id: str                          # "deepseek", "kimi", "anthropic-official", ...
    display_name: str                # "DeepSeek"
    icon_key: str                    # @lobehub/icons 子组件名 (如 "DeepSeek")
                                     # 渲染时 import(`@lobehub/icons/es/${icon_key}/components/Color`)
    messages_url: str                # https://api.deepseek.com/anthropic
    discovery_url: str | None        # https://api.deepseek.com  (None = 不支持/无公开)
    default_model: str               # deepseek-v4-pro
    suggested_models: tuple[str, ...]  # 下拉兜底
    docs_url: str | None
    notes_i18n_key: str | None       # 如 "preset_notes_deepseek" → i18n 中给文字
    api_key_pattern: str | None      # "^sk-[A-Za-z0-9-]+$" 前端轻量校验
    is_recommended: bool

PRESET_PROVIDERS: dict[str, PresetProvider] = {
    "anthropic-official": PresetProvider(
        id="anthropic-official",
        display_name="Anthropic Official",
        icon_key="Anthropic",
        messages_url="https://api.anthropic.com",
        discovery_url="https://api.anthropic.com",
        default_model="claude-3-5-sonnet-20241022",
        suggested_models=("claude-3-5-sonnet-20241022", "claude-3-7-sonnet", ...),
        docs_url="https://docs.anthropic.com",
        notes_i18n_key=None,
        api_key_pattern=r"^sk-ant-[A-Za-z0-9_-]+$",
        is_recommended=True,
    ),
    "deepseek": PresetProvider(
        id="deepseek",
        display_name="DeepSeek",
        icon_key="DeepSeek",
        messages_url="https://api.deepseek.com/anthropic",
        discovery_url="https://api.deepseek.com",
        default_model="deepseek-v4-pro",
        suggested_models=("deepseek-v4-pro", "deepseek-v4-flash"),
        docs_url="https://api-docs.deepseek.com/",
        notes_i18n_key="preset_notes_deepseek",
        api_key_pattern=r"^sk-[A-Za-z0-9]+$",
        is_recommended=True,
    ),
    # ...
}

PRESET_ORDER: tuple[str, ...] = (
    # 推荐 (is_recommended=True) 优先；同优先级按字母序
    "anthropic-official",
    "deepseek",
    "kimi",
    "glm",
    "minimax-intl", "minimax-cn",
    "hunyuan",
    "lkeap",        # 腾讯 LKEAP
    "ark-coding",   # 火山方舟 Coding Plan
    "bailian",      # 阿里百炼
    "xiaomi-mimo",
    # ...
)

def get_preset(preset_id: str) -> PresetProvider | None: ...
def list_presets() -> list[PresetProvider]:
    return [PRESET_PROVIDERS[k] for k in PRESET_ORDER]

CUSTOM_SENTINEL_ID = "__custom__"
```

第一批 entries 来自用户提供的兼容性表，逐项核对 `messages_url`/`discovery_url`。后续可由用户提 PR 增补。

### 4.2 `lib/config/anthropic_url.py` (new)

```python
@dataclass(frozen=True)
class AnthropicEndpoints:
    messages_root: str        # SDK 用 (拼 /v1/messages)
    discovery_root: str       # 模型发现用 (拼 /v1/models)
    has_explicit_suffix: bool # 用户是否已经显式带了 anthropic 子路径

# 已知的 "Claude 兼容子路径" 模式 — 按精确度从严到宽排
_KNOWN_ANTHROPIC_SUFFIX = re.compile(
    r"/(?:api/anthropic|apps/anthropic|plan/anthropic|coding/anthropic|api/coding|anthropic)/?$"
)

def derive_anthropic_endpoints(user_url: str) -> AnthropicEndpoints:
    """1) 剥末尾 /v1[/messages] (用户误带版本路径)
       2) 检测 _KNOWN_ANTHROPIC_SUFFIX：
          匹配 → messages_root=原值, discovery_root=剥掉后缀
          不匹配 → messages_root=discovery_root=原值
    """
```

只在 `__custom__` 模式调用；预设走 `PresetProvider.messages_url/discovery_url`。

### 4.3 `lib/db/models/agent_credential.py` (new)

```python
class AgentAnthropicCredential(Base):
    __tablename__ = "agent_anthropic_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(index=True, default=DEFAULT_USER_ID)
    preset_id: Mapped[str]                # "deepseek" | "__custom__" | ...
    display_name: Mapped[str]             # 用户可改 (默认 = preset.display_name)
    base_url: Mapped[str]                 # 预设填 catalog.messages_url；自定义填用户输入
    api_key: Mapped[str]        # 明文存储；读出 API 时 mask_secret 脱敏 (与 ProviderConfig 一致)
    model: Mapped[str | None]
    haiku_model: Mapped[str | None]
    sonnet_model: Mapped[str | None]
    opus_model: Mapped[str | None]
    subagent_model: Mapped[str | None]
    is_active: Mapped[bool] = mapped_column(default=False, index=True)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
```

约束：每个 user 至多一条 `is_active=True`，由 `set_active()` 在事务内保证（先全量 UPDATE is_active=False，再 set 目标 True）。

### 4.4 alembic migration (new)

```python
def upgrade():
    op.create_table("agent_anthropic_credentials", ...)
    op.create_index(
        "ix_agent_credentials_user_active",
        "agent_anthropic_credentials",
        ["user_id", "is_active"],
    )

    # Data migration: 旧 system_settings 中的 anthropic_* → 一条 __custom__ active 记录
    bind = op.get_bind()
    rows = bind.execute(text("""
        SELECT key, value FROM system_settings
        WHERE key IN ('anthropic_api_key', 'anthropic_base_url', 'anthropic_model',
                      'anthropic_default_haiku_model', 'anthropic_default_sonnet_model',
                      'anthropic_default_opus_model', 'claude_code_subagent_model')
    """)).fetchall()
    settings = {r.key: r.value for r in rows if r.value}

    if settings.get("anthropic_api_key"):
        # 与现有 ProviderConfig.value 一致：明文存储，读出时通过 mask_secret 脱敏
        bind.execute(text("""
            INSERT INTO agent_anthropic_credentials
              (user_id, preset_id, display_name, base_url, api_key,
               model, haiku_model, sonnet_model, opus_model, subagent_model,
               is_active, created_at, updated_at)
            VALUES (:user, '__custom__', 'Migrated', :url, :key,
                    :model, :haiku, :sonnet, :opus, :subagent,
                    1, :now, :now)
        """), {
            "user": DEFAULT_USER_ID,
            "url": settings.get("anthropic_base_url", ""),
            "key": settings["anthropic_api_key"],
            "model": settings.get("anthropic_model"),
            "haiku": settings.get("anthropic_default_haiku_model"),
            "sonnet": settings.get("anthropic_default_sonnet_model"),
            "opus": settings.get("anthropic_default_opus_model"),
            "subagent": settings.get("claude_code_subagent_model"),
            "now": datetime.utcnow(),
        })

def downgrade():
    op.drop_index("ix_agent_credentials_user_active", table_name="agent_anthropic_credentials")
    op.drop_table("agent_anthropic_credentials")
```

旧 `system_settings.anthropic_*` 行**不删**，保留作为读 fallback（让旧代码路径不会立即失败）。新代码读 active credential 优先。

### 4.5 `server/routers/agent_config.py` (new)

```python
router = APIRouter(prefix="/agent", tags=["Agent Config"])

class CredentialResponse(BaseModel):
    id: int
    preset_id: str
    display_name: str
    icon_key: str | None       # 从 catalog 解析；__custom__ 为 None
    base_url: str
    api_key_masked: str
    model: str | None
    haiku_model: str | None
    sonnet_model: str | None
    opus_model: str | None
    subagent_model: str | None
    is_active: bool
    created_at: str

class CreateCredentialRequest(BaseModel):
    preset_id: str             # "deepseek" | "__custom__" | ...
    display_name: str | None   # None 时取 catalog.display_name 或 "Custom"
    base_url: str | None       # __custom__ 时必填
    api_key: str
    model: str | None          # None 时取 catalog.default_model
    haiku_model: str | None = None
    sonnet_model: str | None = None
    opus_model: str | None = None
    subagent_model: str | None = None
    activate: bool = False     # 是否同时设为 active

class TestConnectionRequest(BaseModel):
    preset_id: str | None      # 优先；为 None 则用 base_url
    base_url: str | None
    api_key: str
    model: str | None          # None → 用 catalog.default_model 或 "claude-3-5-sonnet-20241022"

class TestConnectionResponse(BaseModel):
    overall: Literal["ok", "warn", "fail"]
    messages_probe: ProbeResult        # 决定 overall
    discovery_probe: ProbeResult | None
    diagnosis: DiagnosisCode | None
    suggestion: SuggestionAction | None
    derived: dict                      # {"messages_root": ..., "discovery_root": ...}

class ProbeResult(BaseModel):
    success: bool
    status_code: int | None
    latency_ms: int | None
    error: str | None                  # 截断到 200 字符

class DiagnosisCode(str, Enum):
    MISSING_ANTHROPIC_SUFFIX = "missing_anthropic_suffix"
    OPENAI_COMPAT_ONLY = "openai_compat_only"
    AUTH_FAILED = "auth_failed"
    MODEL_NOT_FOUND = "model_not_found"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    UNKNOWN = "unknown"

class SuggestionAction(BaseModel):
    kind: Literal["replace_base_url", "check_api_key", "run_discovery", "see_docs"]
    suggested_value: str | None        # replace_base_url 时填

@router.get("/preset-providers")
async def list_preset_providers(_user: CurrentUser, _t: Translator):
    return {
        "providers": [
            {
                "id": p.id,
                "display_name": p.display_name,
                "icon_key": p.icon_key,
                "messages_url": p.messages_url,
                "discovery_url": p.discovery_url,
                "default_model": p.default_model,
                "suggested_models": list(p.suggested_models),
                "docs_url": p.docs_url,
                "notes": _t(p.notes_i18n_key) if p.notes_i18n_key else None,
                "api_key_pattern": p.api_key_pattern,
                "is_recommended": p.is_recommended,
            }
            for p in list_presets()
        ],
        "custom_sentinel_id": CUSTOM_SENTINEL_ID,
    }

@router.get("/credentials")
async def list_credentials(...): ...

@router.post("/credentials", status_code=201)
async def create_credential(body: CreateCredentialRequest, ...):
    # 校验 preset_id 存在或 == __custom__
    # __custom__ 时 base_url 必填
    # 创建 credential
    # 如果 activate=True 或当前没有 active，set_active(new.id)
    ...

@router.patch("/credentials/{id}")
async def update_credential(...): ...

@router.delete("/credentials/{id}", status_code=204)
async def delete_credential(...):
    # 不能删 active；要求先切到其他凭证
    ...

@router.post("/credentials/{id}/activate")
async def activate_credential(id: int, ...):
    await repo.set_active(id, user_id=...)
    await session.commit()
    await sync_anthropic_env(session)  # 立即从新 active credential 同步到 env
    return {"active_id": id}

@router.post("/credentials/{id}/test")
async def test_credential(id: int, ...):
    cred = await repo.get(id)
    return await run_test(
        preset_id=cred.preset_id,
        base_url=cred.base_url,
        api_key=cred.api_key,
        model=cred.model,
    )

@router.post("/test-connection")
async def test_connection_draft(body: TestConnectionRequest, ...):
    return await run_test(...)
```

### 4.6 `lib/config/anthropic_probe.py` (new)

```python
async def probe_messages(
    *,
    messages_root: str,
    api_key: str,
    model: str,
    timeout_s: float = 10.0,
) -> ProbeResult:
    """POST {messages_root}/v1/messages with minimal payload."""
    url = f"{messages_root.rstrip('/')}/v1/messages"
    payload = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    # httpx 直调，避免 SDK subprocess 副作用
    ...

async def probe_discovery(
    *, discovery_root: str | None, api_key: str, timeout_s: float = 5.0
) -> ProbeResult | None:
    if not discovery_root:
        return None
    ...

async def run_test(
    *,
    preset_id: str | None,
    base_url: str | None,
    api_key: str,
    model: str | None,
) -> TestConnectionResponse:
    """完整流程：派生 → probe messages → 失败时自定义模式自愈 → probe discovery → 诊断"""

    # 1. 派生 endpoints
    if preset_id and preset_id != CUSTOM_SENTINEL_ID:
        preset = get_preset(preset_id)
        if preset is None:
            raise HTTPException(400, "unknown preset")
        ep = AnthropicEndpoints(
            messages_root=preset.messages_url,
            discovery_root=preset.discovery_url or "",
            has_explicit_suffix=True,
        )
        effective_model = model or preset.default_model
    else:
        if not base_url:
            raise HTTPException(400, "base_url required for __custom__ mode")
        ep = derive_anthropic_endpoints(base_url)
        effective_model = model or "claude-3-5-sonnet-20241022"

    # 2. probe messages
    msg = await probe_messages(messages_root=ep.messages_root, api_key=api_key, model=effective_model)

    # 3. 自定义模式 + 失败 + 没有 explicit suffix → 尝试 +/anthropic 自愈
    suggestion: SuggestionAction | None = None
    if (not msg.success
        and preset_id == CUSTOM_SENTINEL_ID
        and not ep.has_explicit_suffix
        and msg.status_code in (404, 405, 502)):
        retry_root = ep.messages_root.rstrip("/") + "/anthropic"
        retry = await probe_messages(messages_root=retry_root, api_key=api_key, model=effective_model)
        if retry.success:
            msg = retry
            suggestion = SuggestionAction(kind="replace_base_url", suggested_value=retry_root)
            diagnosis = DiagnosisCode.MISSING_ANTHROPIC_SUFFIX

    # 4. probe discovery (warn 级别)
    disc = await probe_discovery(discovery_root=ep.discovery_root, api_key=api_key)

    # 5. 诊断
    if msg.success:
        overall = "ok" if (disc is None or disc.success) else "warn"
        diagnosis = diagnosis if msg.success and "diagnosis" in locals() else None
    else:
        overall = "fail"
        diagnosis = classify(msg)  # 401/403→AUTH_FAILED, 404→MODEL_NOT_FOUND or UNKNOWN, ...

    return TestConnectionResponse(
        overall=overall,
        messages_probe=msg,
        discovery_probe=disc,
        diagnosis=diagnosis,
        suggestion=suggestion,
        derived={"messages_root": ep.messages_root, "discovery_root": ep.discovery_root},
    )
```

### 4.7 `lib/config/service.py` 修改

```python
# 旧 sync_anthropic_env(all_settings: dict[str, str]) → 重命名为 _sync_from_settings 内部辅助
# 新 sync_anthropic_env(session) 是新签名
async def sync_anthropic_env(session: AsyncSession) -> None:
    repo = AgentCredentialRepository(session)
    cred = await repo.get_active(user_id=DEFAULT_USER_ID)
    if cred is None:
        # 兼容：fallback 到旧 system_settings (启动早期未迁移完时)
        all_settings = await SystemSettingRepository(session).get_all()
        _sync_from_settings(all_settings)
        return
    env_map = {
        "ANTHROPIC_API_KEY": cred.api_key,
        "ANTHROPIC_BASE_URL": cred.base_url,
        "ANTHROPIC_MODEL": cred.model or "",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": cred.haiku_model or "",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": cred.sonnet_model or "",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": cred.opus_model or "",
        "CLAUDE_CODE_SUBAGENT_MODEL": cred.subagent_model or "",
    }
    for k, v in env_map.items():
        if v: os.environ[k] = v
        else: os.environ.pop(k, None)
```

调用方迁移：
- `server/app.py` lifespan：旧 `sync_anthropic_env(all_settings)` → `await sync_anthropic_env(session)`
- `server/routers/system_config.py` PATCH 接口：旧的「PATCH 后调 sync_anthropic_env」路径仍保留对 `anthropic_*` 旧 setting key 的兼容写入，但写入后**不再**立即调 `sync_anthropic_env`（凭证迁移完成后，这些 key 已成 fallback 路径，PATCH 应改走 `/agent/credentials/{id}` 接口）。在迁移期保留双写，直到下个版本删除。
- `Repository 命名`：统一用 `AgentCredentialRepository`（不带 `Anthropic` 中缀）。

旧 `sync_anthropic_env(all_settings: dict)` 签名彻底删除（同 PR 内更新所有调用点），不留 deprecation shim。

### 4.8 `_discover_anthropic` 修复

`lib/custom_provider/discovery.py`：

```python
async def _discover_anthropic(base_url: str | None, api_key: str) -> list[dict]:
    from lib.config.anthropic_url import derive_anthropic_endpoints
    ep = derive_anthropic_endpoints(base_url or "https://api.anthropic.com")
    discovery_root = ep.discovery_root or "https://api.anthropic.com"
    resp = await get_http_client().get(
        f"{discovery_root}/v1/models",
        ...
    )
```

`server/routers/custom_providers.py` 的 `/discover-anthropic` 端点：从 active credential 取默认凭证（不再从 system_settings 读 `anthropic_*`）。

### 4.9 前端

#### 4.9.1 类型 `frontend/src/types/agent-credential.ts`

```typescript
export interface PresetProvider {
  id: string;
  display_name: string;
  icon_key: string;
  messages_url: string;
  discovery_url: string | null;
  default_model: string;
  suggested_models: string[];
  docs_url: string | null;
  notes: string | null;
  api_key_pattern: string | null;
  is_recommended: boolean;
}

export interface AgentCredential {
  id: number;
  preset_id: string;
  display_name: string;
  icon_key: string | null;
  base_url: string;
  api_key_masked: string;
  model: string | null;
  haiku_model: string | null;
  // ...
  is_active: boolean;
  created_at: string;
}
```

#### 4.9.2 `PresetIcon` 组件

```tsx
const ICON_LOADERS: Record<string, () => Promise<{ default: ComponentType }>> = {
  Anthropic: () => import("@lobehub/icons/es/Anthropic/components/Color"),
  DeepSeek: () => import("@lobehub/icons/es/DeepSeek/components/Color"),
  Moonshot: () => import("@lobehub/icons/es/Moonshot/components/Color"),
  Kimi: () => import("@lobehub/icons/es/Kimi/components/Color"),
  Zhipu: () => import("@lobehub/icons/es/Zhipu/components/Color"),
  // ... 与 catalog icon_key 一一对应
};

export function PresetIcon({ iconKey, size = 20 }: Props) {
  const Icon = useLazyIcon(iconKey);
  if (!Icon) return <Monogram name={iconKey} size={size} />;
  return <Icon size={size} />;
}
```

#### 4.9.3 `AgentConfigTab.tsx` 改造

Section 1 (API Credentials) 整段替换：

```tsx
<Section kicker="Credentials" title={t("agent_credentials")}>
  <CredentialList
    credentials={credentials}
    onActivate={handleActivate}
    onTest={handleTest}
    onEdit={handleEdit}
    onDelete={handleDelete}
  />
  <button onClick={() => setAddModalOpen(true)}>
    + {t("add_credential")}
  </button>
</Section>

<AddCredentialModal
  open={addModalOpen}
  presets={presets}
  onSubmit={handleCreate}
  onClose={() => setAddModalOpen(false)}
/>
```

`AddCredentialModal` 内部布局参考用户提供的截图：
- 顶部 tab：Claude 供应商 / 统一供应商（本轮只做 Claude，统一供应商 tab 显示 Coming Soon）
- 中部 chip 网格：自定义配置（左上角固定）+ 各预设 chip（带 lobehub icon + 推荐星标）
- 选中后下方表单：API Key 输入 + Model 选择 + 显示名（默认 = preset.display_name）+ Notes
- 自定义模式：额外显示 Base URL 输入
- 底部：取消 + 添加（添加成功后自动 activate 如果是第一条）

Section 2 (Model Routing) + Section 3 (Runtime Tuning) 保持现状，但其内部读写改为指向 active credential 字段。

#### 4.9.4 i18n

新增 keys（zh / en / vi）：
- `agent_credentials`, `add_credential`, `select_provider`, `claude_compat_providers`, `unified_providers_coming_soon`, `custom_config`, `preset_recommended`, `set_active`, `is_active`, `test_credential`, `test_running`, `test_ok`, `test_warn`, `test_fail`, `apply_fix`, `apply_fix_hint`, `diagnosis_missing_anthropic_suffix`, `diagnosis_openai_compat_only`, `diagnosis_auth_failed`, `diagnosis_model_not_found`, `diagnosis_rate_limited`, `diagnosis_network`, `diagnosis_unknown`, `derived_messages_root`, `derived_discovery_root`, `cred_delete_active_blocked`, `cred_activated_toast`, `preset_notes_deepseek` 等。

---

## 5. 错误处理与诊断

| DiagnosisCode | 触发条件 | 用户文案 (i18n key) | suggestion |
|---------------|----------|---------------------|------------|
| `missing_anthropic_suffix` | probe 自愈成功 | "看起来 base_url 缺 `/anthropic` 后缀，已自动探测到正确端点" | `replace_base_url(suggested=...)` |
| `openai_compat_only` | 200 但响应非 anthropic JSON (无 `type=message`/`content`) | "该端点返回 OpenAI 兼容协议，Claude SDK 无法使用。检查是否选错 Plan" | `see_docs` |
| `auth_failed` | 401/403 | "API Key 无效或已过期" | `check_api_key` |
| `model_not_found` | 404 + body 含 `model` 关键字 | "模型 X 不存在。点击下方按钮发现可用模型" | `run_discovery` |
| `rate_limited` | 429 | "触发限流，稍后重试" | None |
| `network` | 超时/连接拒绝 | "网络无法访问，检查 URL 与防火墙" | None |
| `unknown` | 其他 | 显示 status + body 前 200 字符 | None |

---

## 6. 测试计划

### 6.1 后端单元测试

- `tests/test_anthropic_url.py`：`derive_anthropic_endpoints` 覆盖 catalog 表中所有形态 + 边界（带 `/v1`、`/v1/messages`、空、None、官方根、未知子路径）。
- `tests/test_agent_provider_catalog.py`：每个 preset 的 `messages_url`/`discovery_url` 形态正确；`icon_key` 在前端 `ICON_LOADERS` 表中存在（共享 fixture）。
- `tests/test_agent_credential_repo.py`：set_active 互斥；删除 active 报错；data migration 行为。
- `tests/test_anthropic_probe.py`：mock httpx 覆盖 200/401/404/超时/非 anthropic JSON 各分支。
- `tests/test_agent_config_router.py`：6 个新端点的鉴权 / 校验 / 边界。
- `tests/test_discover_anthropic_path_fix.py`：回归 `/discover-anthropic` 在带 `/anthropic` 后缀时也走对路径。

### 6.2 前端单元测试

- `AddCredentialModal.test.tsx`：选预设 → URL 自动填 + readonly；选自定义 → URL 可编辑；submit payload 正确。
- `CredentialList.test.tsx`：activate / delete / test 操作触发对应 API；active 不可删提示。
- `PresetIcon.test.tsx`：lobehub 加载失败 fallback monogram。
- 更新 `AgentConfigTab.test.tsx` 以适配新结构。

### 6.3 i18n 一致性

`tests/test_i18n_consistency.py` 自动校验 zh/en/vi 三语 key 不漂移。

### 6.4 手工测试矩阵

至少手测以下网关：
- Anthropic 官方
- DeepSeek（验证 messages 走 `/anthropic`，discovery 走根）
- Kimi (Moonshot)
- GLM (z.ai)
- 火山方舟 Coding Plan
- 自定义模式 + probe 自愈（填 `https://api.deepseek.com` 触发自愈到 `/anthropic`）

---

## 7. 兼容性

- 旧 `system_settings.anthropic_*` 行**不删**：alembic data migration 完成后这些 key 进入"只读 fallback"状态，新代码不写入。
- `sync_anthropic_env` 函数签名改为接收 `AsyncSession`（旧的接收 `dict[str, str]` 签名替换为内部辅助函数 `_sync_from_settings`）。
- 启动 lifespan 中的初始化路径：先跑 alembic upgrade（含 data migration），再调 `sync_anthropic_env(session)`。
- 自定义供应商页 (Custom Provider) 的「导入到 Agent」功能（`getCustomProviderCredentials` + setDraft）改为：导入即创建一条 `__custom__` credential 并默认 activate，弹出新 modal 让用户确认/编辑。

---

## 8. 实施风险与缓解

| 风险 | 缓解 |
|------|------|
| Lobehub icon 包体积膨胀 | 每个 icon 用 `import()` 动态分块；Vite 自动 tree-shake 未引用项 |
| Active 切换时正在跑的 SDK session 不感知 | 仅 toast 提示；不强杀。文档明确说明 |
| 真实 messages probe 产生费用 | `max_tokens=1` 实际成本 < 0.001 USD；UI 上按钮 hover 显示提示 "Will send 1 token request" |
| Probe 把 API key 传到日志 | `_run_test` 与 `probe_messages` 严格不打 body 与 headers；只打 URL + status |
| 多用户场景凭证泄露 | `CurrentUser` 鉴权 + `user_id` 列；与 CustomProvider 一致水位 |
| 数据迁移在生产 SQLite/PG 上失败 | data migration 用 `try/except + log warning`，迁移失败不阻塞 schema migration；用户可在 UI 里手动建 |

---

## 9. 后续工作（不在本轮）

- **统一供应商 tab**：图中第二个 tab，把 Custom Provider 也整合到 cc-switch 形态下统一管理。
- **AWS Bedrock catalog entry**：双密钥结构需要扩展 schema（`api_key_secondary` 或 JSON `extra_credentials`）。
- **凭证导入/导出**：跨设备迁移。
- **多用户细粒度授权**：与全局多用户改造一并做。
- **预设清单远程更新**：当前 hardcoded，未来可考虑 GitHub raw + 缓存。
