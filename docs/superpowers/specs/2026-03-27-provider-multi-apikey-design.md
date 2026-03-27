# 供应商多 API Key 支持设计

**日期：** 2026-03-27
**状态：** 已审核

## 概述

为每个供应商支持配置多个 API Key / Vertex 凭证，用户手动切换当前活跃 Key，连接测试可针对任意单个 Key 进行。

## 需求

1. 同一供应商可配置多个凭证（API Key 或 Vertex 服务账号 JSON）
2. 每个凭证有：自定义名称、密钥值、可选的自定义 base_url（AI Studio）
3. RPM / max_workers / request_gap 等配置跟供应商走，所有凭证共享
4. 每个供应商有一个「当前活跃凭证」，在供应商配置页手动切换，全局生效
5. 连接测试可针对任意一个凭证单独进行
6. Bug fix：base_url 尾部 `/` 归一化

## 方案选择

**方案 A（采用）：新建 `provider_credential` 表**

凭证是独立的结构化实体（名称、密钥、URL、活跃状态），用专用表建模最自然。与现有 `provider_config`（共享配置 KV）职责分离，不污染现有逻辑。

淘汰方案：
- 方案 B（KV 表 slot 前缀）：命名约定脆弱，查询别扭
- 方案 C（JSON 字段）：并发更新需读-改-写，加密/脱敏复杂

---

## 数据模型

### 新增 `provider_credential` 表

```python
class ProviderCredential(TimestampMixin, Base):
    __tablename__ = "provider_credential"
    __table_args__ = (
        Index("ix_provider_credential_provider", "provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)          # "gemini-aistudio"
    name: Mapped[str] = mapped_column(String(128), nullable=False)             # 用户自定义名称
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)           # api_key 类供应商
    credentials_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # Vertex JSON 路径
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)          # 自定义 URL
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # created_at, updated_at 由 TimestampMixin 提供
```

**设计要点：**
- `api_key` 和 `credentials_path` 互斥：api_key 类供应商用 `api_key`，Vertex 用 `credentials_path`
- 每个供应商最多一条 `is_active=True`（应用层保证）
- `api_key` 明文存储（与现有 `provider_config` 表一致），API 响应时脱敏
- `base_url` 存储时做尾部 `/` 归一化
- `provider_config` 表不变，继续存 RPM / workers 等共享配置

### 数据迁移

Alembic 迁移脚本：
1. 创建 `provider_credential` 表
2. 将 `provider_config` 中现有的 `api_key` / `credentials_path` / `base_url` 行迁入 `provider_credential`，设 `is_active=True`，名称为「默认密钥」
3. 从 `provider_config` 中删除这些已迁移的行

---

## 后端 API

### 凭证管理端点（新增）

所有端点在 `server/routers/providers.py` 中，作为供应商的子资源。

#### `GET /api/v1/providers/{provider_id}/credentials`

返回该供应商的所有凭证列表（api_key 脱敏）。

**响应：**
```json
{
  "credentials": [
    {
      "id": 1,
      "provider": "gemini-aistudio",
      "name": "个人账号",
      "api_key_masked": "AIza…xY2d",
      "credentials_filename": null,
      "base_url": "https://proxy.example.com/v1/",
      "is_active": true,
      "created_at": "2026-03-27T10:00:00Z"
    }
  ]
}
```

#### `POST /api/v1/providers/{provider_id}/credentials`

新增凭证。若为该供应商的第一条凭证，自动设为 `is_active=True`。

**请求（api_key 类）：**
```json
{
  "name": "团队账号",
  "api_key": "AIza...",
  "base_url": "https://proxy.example.com/v1"
}
```

**请求（Vertex 类）：** multipart form — `name` 字段 + `file` 上传。

#### `PATCH /api/v1/providers/{provider_id}/credentials/{cred_id}`

更新凭证（name / api_key / base_url）。

#### `DELETE /api/v1/providers/{provider_id}/credentials/{cred_id}`

删除凭证。若删除的是活跃凭证且还有其他凭证，自动将 `created_at` 最早的另一条设为活跃。若删完了，供应商状态回到 `unconfigured`。触发 `invalidate_backend_cache()`。

#### `POST /api/v1/providers/{provider_id}/credentials/{cred_id}/activate`

将指定凭证设为活跃（同时清除同供应商的其他活跃标记）。触发 `invalidate_backend_cache()` + `worker.reload_limits()`。

### 连接测试改造

```
POST /api/v1/providers/{provider_id}/test?credential_id=123
```

- 新增可选 query param `credential_id`
- 若指定，用该凭证测试
- 若未指定，用当前活跃凭证测试
- 若无任何凭证，返回「缺少配置」错误

### Vertex 凭证上传改造

```
POST /api/v1/providers/gemini-vertex/credentials
```

改为同时上传文件 + 创建凭证记录。文件存 `vertex_keys/vertex_cred_{cred_id}.json`（支持多文件）。

### 供应商状态判定变更

`ConfigService.get_all_providers_status()` 的 `"ready"` 判定从「`provider_config` 中是否有 `api_key`」改为「`provider_credential` 中是否有 `is_active=True` 的记录」。

### ConfigResolver 集成

`ConfigResolver.provider_config()` 返回值逻辑调整：
1. 从 `provider_config` 读共享配置（RPM / workers 等）
2. 从 `provider_credential` 读活跃凭证的 `api_key` / `base_url` / `credentials_path`
3. 合并后返回

**调用方无感知变化** — `generation_tasks.py` 中的 `db_config.get("api_key")` 等代码不需要改。

### ProviderConfigResponse 变更

`GET /api/v1/providers/{provider_id}/config` 的 `fields` 列表中不再包含 `api_key`、`credentials_path`、`base_url`，只保留共享配置字段（RPM / workers 等）。

---

## 前端 UI

### ProviderDetail 页面重构

分为两个区域：

**区域一：凭证管理（取代原有的 api_key / credentials_path / base_url 字段）**

```
┌──────────────────────────────────────────────────┐
│  密钥管理                           [+ 添加密钥]  │
├──────────────────────────────────────────────────┤
│  ● 个人账号        AIza…xY2d                     │
│    https://proxy.example.com/v1/                 │
│                    [测试] [编辑] [删除]            │
│──────────────────────────────────────────────────│
│  ○ 团队账号        AIza…k8Pm                     │
│                    [测试] [激活] [编辑] [删除]     │
└──────────────────────────────────────────────────┘
```

- `●` / `○` 表示活跃/非活跃状态
- 每条凭证行显示：名称、脱敏 key（或 Vertex 文件名）、可选的 base_url
- 每条凭证独立的「测试」按钮，调用 `POST /test?credential_id=xxx`
- 「编辑」展开内联编辑表单
- 「添加密钥」打开内联表单
- Vertex 供应商的「添加」包含文件上传 + 名称输入

**区域二：共享配置（保持现有逻辑）**

```
┌──────────────────────────────────────────────────┐
│  ▸ 高级配置                                       │
│    图片 RPM: [60]    视频 RPM: [10]               │
│    请求间隔: [3.1]   图片并发: [2]  视频并发: [1]  │
│                                        [保存]     │
└──────────────────────────────────────────────────┘
```

通过 `PATCH /providers/{id}/config` 保存，逻辑不变。

### 新增类型定义

```typescript
interface ProviderCredential {
  id: number;
  provider: string;
  name: string;
  api_key_masked: string | null;
  credentials_filename: string | null;
  base_url: string | null;
  is_active: boolean;
  created_at: string;
}
```

---

## base_url 归一化

### 问题

Google genai SDK 的 `http_options.base_url` 要求尾部带 `/`。用户输入不带斜杠的 URL 会导致请求失败。

### 修复

**存储时归一化：** 凭证创建/更新时，对 `base_url` 做 `url.strip()` 后确保以 `/` 结尾。

**消费时防御性归一化：** 在以下 4 处使用 `base_url` 创建 `genai.Client` 的地方加一层防御：

1. `lib/image_backends/gemini.py:89` — 图片后端
2. `lib/video_backends/gemini.py:87` — 视频后端
3. `lib/gemini_client.py:498` — GeminiClient
4. `server/routers/providers.py:286` — 连接测试

归一化函数：
```python
def normalize_base_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.endswith("/"):
        url += "/"
    return url
```

---

## 边界情况

| 场景 | 行为 |
|------|------|
| 供应商无任何凭证 | 状态为 `unconfigured`，生成任务报错「未配置密钥」 |
| 删除活跃凭证，还有其他凭证 | 自动将 `created_at` 最早的另一条设为活跃 |
| 删除活跃凭证，无其他凭证 | 供应商回到 `unconfigured`，触发 `invalidate_backend_cache()` |
| 切换活跃凭证 | 清除后端缓存，下次生成任务使用新 key |
| 凭证名称重复（同供应商内） | 允许，不做唯一约束（用 id 区分） |
| base_url 为空字符串 | 存为 `None`，使用供应商默认地址 |

## 不做的事

- 自动轮换 / 负载均衡 — 仅手动切换
- api_key 加密存储 — 与现有 `provider_config` 表保持一致
- 凭证使用统计 — 超出当前范围
