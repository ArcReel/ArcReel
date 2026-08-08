# 在既有 FastAPI 应用内挂载 streamable-HTTP MCP 端点：现行做法调研

> Research for issue #1705（Map #1702）。调研对象：官方 MCP Python SDK（`mcp`）与 FastMCP（`fastmcp`）
> 在 `server/app.py` 这类既有 FastAPI 应用内挂载 streamable-HTTP MCP 端点的做法、鉴权接入、
> stateless 模式语义、progress notification 可用性与客户端支持现状。

## TL;DR

1. **挂载方式**：两个库都走同一模式——`streamable_http_app()` / `http_app()` 返回一个独立
   Starlette ASGI app，用 `app.mount("/mcp", mcp_app)` 挂进 FastAPI；**必须**把 MCP 的
   session manager（`mcp.session_manager.run()` 或 FastMCP 的 `mcp_app.lifespan`）整合进宿主
   FastAPI 的 lifespan，否则首个请求报 "Task group is not initialized"。ArcReel 的
   `server/app.py` 已有 asynccontextmanager lifespan，加一行 `async with` 即可。
2. **版本约束是决定性因素**：官方 SDK 当前稳定线是 **v2（2.0.0，2026-07-28 发布，对应
   MCP 2026-07-28 spec）**，但 `claude-agent-sdk 0.2.128`（本仓库已依赖）钉死
   `mcp>=1.23.0,<2.0.0`，锁定解析为 **mcp 1.26.0**。因此 ArcReel 现阶段只能用 **v1 API**
   （`mcp.server.fastmcp.FastMCP`），v2 的 `MCPServer` 用不了。FastMCP 3.4.6
   （经 `fastmcp-slim` 依赖 `mcp>=1.24.0,<2.0`）与该钉版兼容，可作为新增依赖引入。
3. **鉴权**：SDK 自带 Bearer 机制（`TokenVerifier` + `AuthSettings`，内部是 Starlette
   `BearerAuthBackend` + `RequireAuthMiddleware`）；把 `verify_token` 桥接到
   `server/auth.py::_verify_api_key`（`arc-` API Key）即可复用既有校验与缓存。另一条更薄的路：
   不用 SDK auth，在挂载点外包一层纯 ASGI 中间件自行校验（同 `SPAShellNoCacheMiddleware` 模式）。
   Claude Code / codex 都以静态 `Authorization: Bearer arc-xxx` 头连接，不要求 OAuth 发现流程。
4. **stateless_http=True 是推荐档**：工具不需要回呼客户端（sampling/elicitation）时用 stateless；
   每请求新建 transport、无会话粘性、可多 worker 水平扩展。并发无 SDK 侧上限（每个在途请求一个
   anyio task），SDK 侧**没有工具执行超时**——超时完全由客户端控制。
5. **progress notification 在 streamable HTTP（含 stateless）下可用**：`ctx.report_progress()`
   经在途 POST 请求自己的 SSE 流下发（`related_request_id` 绑定）。对 Claude Code 的实际价值：
   进度通知**不延长**每次调用的硬性 wall-clock 超时，但能**满足 5 分钟 idle 超时**的活性检查，
   使长任务不被提前掐断。codex 默认 `tool_timeout_sec = 60`，需用户侧调大。

---

## 1. 版本与依赖现状（2026-08-08 查证）

| 包 | 最新版本 | 关键事实 | 出处 |
|---|---|---|---|
| `mcp`（官方 Python SDK） | **2.0.0**（2026-07-28 发布，v1 线最新约 1.28.1） | v2 是配合 MCP 2026-07-28 spec 的大版本重构，`FastMCP` 类更名 `MCPServer`；`pip install mcp` 现在装 2.x | [PyPI mcp](https://pypi.org/project/mcp/)、[migration.md](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/migration.md)、[Releases](https://github.com/modelcontextprotocol/python-sdk/releases) |
| `claude-agent-sdk` | 0.2.128（本仓库 uv.lock 已锁） | requires `mcp>=1.23.0,<2.0.0` → 本仓库解析为 **mcp 1.26.0**（transitive，已可 import） | [PyPI claude-agent-sdk 0.2.128 metadata](https://pypi.org/pypi/claude-agent-sdk/0.2.128/json)、本仓库 `uv.lock` |
| `fastmcp`（PrefectHQ） | **3.4.6** | 依赖 `fastmcp-slim[client,server]==3.4.6`，后者要求 `mcp>=1.24.0,<2.0` → 与 claude-agent-sdk 钉版**兼容** | [PyPI fastmcp](https://pypi.org/pypi/fastmcp/json)、[PyPI fastmcp-slim](https://pypi.org/pypi/fastmcp-slim/json) |
| `fastapi` / `starlette` | 本仓库锁 0.140.13 / 1.3.1 | `app.mount()` 挂 ASGI 子应用；子应用 lifespan 不会被执行（Starlette 语义，两库文档均强调） | 本仓库 `uv.lock` |

**结论**：ArcReel 的两条可行路线是 **(a) 直接用已在依赖树里的官方 SDK v1（mcp 1.26.0，零新增依赖）**，
或 **(b) `uv add fastmcp`（3.4.6）**。官方 SDK v2 在 `claude-agent-sdk` 解除 `<2` 钉版前不可用；
写代码时应按 v1 API（`mcp.server.fastmcp.FastMCP`），并预期未来向 v2 `MCPServer` 迁移
（迁移面：import 路径、transport 参数从构造函数移到 `streamable_http_app()`，官方有
[migration guide](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/migration.md)）。

## 2. 挂载方式：mount + lifespan 整合

### 2.1 官方 SDK（v1 写法，适配 mcp 1.26.0）

```python
from contextlib import asynccontextmanager
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("arcreel", stateless_http=True, streamable_http_path="/")  # v1: 参数在构造函数

@mcp.tool()
async def some_tool(x: str) -> str: ...

mcp_asgi = mcp.streamable_http_app()   # 返回独立 Starlette app

@asynccontextmanager
async def lifespan(app: FastAPI):
    ...  # 既有 startup
    async with mcp.session_manager.run():   # 关键：session manager 必须由宿主 lifespan 驱动
        yield
    ...  # 既有 shutdown

app.mount("/mcp", mcp_asgi)
```

要点（官方文档明确）：

- `streamable_http_app()` 返回的 Starlette app 自带 lifespan 会调 `session_manager.run()`，
  但 **mount 之后子应用的 lifespan 是死代码**（Starlette 不执行挂载子应用的 lifespan），必须在
  宿主 lifespan 里显式 `async with mcp.session_manager.run()`，否则请求时报
  "Task group is not initialized. Make sure to use run()"。出处：
  [Running / ASGI](https://py.sdk.modelcontextprotocol.io/v2/run/asgi)、
  [Troubleshooting](https://py.sdk.modelcontextprotocol.io/v2/troubleshooting)、
  [docs/run/asgi.md](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/asgi.md)。
- ArcReel 的 `server/app.py::lifespan` 已是 asynccontextmanager，把 `yield` 包进
  `async with mcp.session_manager.run():` 即可；`session_manager.run()` 每实例只能调用一次
  （多次 run 会被 `_has_started` 挡住），与 uvicorn `--reload` 重建进程的模型兼容。
- 路径拼接：v1 中 `streamable_http_path` 默认 `/mcp`。若 `app.mount("/mcp", ...)` 再叠加默认
  path，最终端点会变成 `/mcp/mcp`；把 `streamable_http_path` 设为 `/`（或挂载在根、保留默认
  path）二选一。v2 把该参数移到了 `streamable_http_app(streamable_http_path=...)`。
- `transport_security`（`TransportSecuritySettings`）控制 DNS-rebinding 防护 / Host 校验；
  对外网部署需按部署域名配置 allowed hosts（SDK 默认按 `host="127.0.0.1"` 生成）。出处：
  [streamable_http_app API](https://py.sdk.modelcontextprotocol.io/v2/api/mcp/server/mcpserver/server)。

### 2.2 FastMCP 3.x

```python
from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans

mcp = FastMCP("arcreel")
mcp_app = mcp.http_app(path="/")            # path="/"，因为下面挂在 /mcp

app = FastAPI(lifespan=combine_lifespans(arcreel_lifespan, mcp_app.lifespan))
app.mount("/mcp", mcp_app)
```

- FastMCP 文档同样强调 lifespan 必传：缺失时 "Session manager won't initialize"。它额外提供
  `combine_lifespans` 工具函数合并宿主与 MCP 两个 lifespan，比手工嵌套 `async with` 更整洁。
  出处：[deployment/http.mdx](https://github.com/prefecthq/fastmcp/blob/main/docs/deployment/http.mdx)、
  [integrations/fastapi.mdx](https://github.com/prefecthq/fastmcp/blob/main/docs/integrations/fastapi.mdx)、
  [servers/lifespan.mdx](https://github.com/prefecthq/fastmcp/blob/main/docs/servers/lifespan.mdx)。

### 2.3 挂载注意事项（两库通用）

- mount 的子应用**绕过 FastAPI 的依赖注入**（`dependencies=[Depends(get_current_user)]` 不作用于
  子应用），但宿主 `app.add_middleware(...)` 的全局中间件仍会包住它——ArcReel 的
  CORSMiddleware、request logging、`SPAShellNoCacheMiddleware` 都会套在 MCP 端点外。
  SPA fallback 是中间件而非 catch-all 路由的话不冲突；若有 catch-all 路由需确认 mount 先于其匹配。
- MCP 端点行为：单一 `/mcp` 路径同时接受 `POST`（JSON-RPC 请求，响应可为纯 JSON 或按需升格为
  SSE 流）与 `GET`（可选的服务器→客户端通知流）；`json_response=True` 可强制纯 JSON 响应。

## 3. Bearer 鉴权与 `arc-` API Key 复用

### 3.1 SDK 内建机制（推荐路径）

官方 SDK 的 auth 模型：MCP server 作为 **OAuth 2.1 resource server**，只校验不发token。
接入面是一个抽象类 `TokenVerifier`（`mcp.server.auth.provider`）：

```python
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

class ArcApiKeyVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        payload = await _verify_api_key(token)      # server/auth.py 既有校验（含 LRU+TTL 缓存）
        if payload is None:
            return None
        return AccessToken(token=token, client_id=payload["sub"], scopes=["arcreel"])

mcp = FastMCP("arcreel", token_verifier=ArcApiKeyVerifier(),
              auth=AuthSettings(issuer_url=..., resource_server_url=..., required_scopes=[...]))
```

- `token_verifier` 与 `auth` 必须**成对提供**，否则构造时 `ValueError`。出处：
  [Authorization tutorial](https://py.sdk.modelcontextprotocol.io/v2/llms-full.txt)（`docs_src/authorization/tutorial001.py`）。
- 内部实现：`BearerAuthBackend`（Starlette `AuthenticationBackend`，从 `Authorization: Bearer`
  取 token → `verify_token()` → 过期检查）+ `RequireAuthMiddleware`（未认证 401 / scope 不足 403，
  带 `WWW-Authenticate` 头与 RFC 9728 protected-resource-metadata 指针）。出处：
  [bearer_auth API](https://py.sdk.modelcontextprotocol.io/v2/api/mcp/server/auth/middleware/bearer_auth)。
- `AuthSettings.issuer_url` / `resource_server_url` 面向 OAuth 发现（RFC 9728）；对只用静态
  API Key 的客户端（Claude Code `--header` / codex `bearer_token_env_var`）这些 URL 不会被访问，
  填部署地址即可。好处是对未来支持 OAuth 的客户端也能给出规范的 401 响应。
- FastMCP 3.x 对应物是 `auth=` provider 体系（JWT/静态 token/各托管 IdP），自定义校验同样是实现
  一个 verifier。出处：[FastMCP auth 文档](https://github.com/prefecthq/fastmcp/blob/main/docs/deployment/http.mdx)。

### 3.2 备选：自定义纯 ASGI 中间件

不启用 SDK auth，在 `app.mount("/mcp", ...)` 外包一层薄 ASGI wrapper：从 scope headers 取
Bearer token → `await verify_token_flexible(token)`（`server/auth.py` 已同时支持 JWT 与 `arc-`
API Key）→ 失败即回 401。与 `SPAShellNoCacheMiddleware` 同一实现模式（纯 ASGI、不进
BaseHTTPMiddleware）。代价：401 响应的 `WWW-Authenticate` 语义要自己写对；收益：不用构造
`AuthSettings`，且 JWT 用户 token 也能连 MCP。两条路都可行；**桥接 `TokenVerifier` 是更
SDK-native、面向未来 OAuth 客户端的做法**。

## 4. stateless_http：并发与超时模型

- **开关语义**（官方文档称之为 "The one knob"）：工具不需要**回呼客户端**（sampling、
  elicitation/`Resolve`、server-initiated request）→ 用 `stateless_http=True`；需要 → 必须
  sessionful + 粘性路由。stateless 下调用回呼类 API 直接抛
  `MCPError: Cannot send 'elicitation/create': this transport context has no back-channel`。
  出处：[legacy-clients](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/legacy-clients.md)、
  [llms-full.txt "The one knob: stateless_http"](https://py.sdk.modelcontextprotocol.io/v2/llms-full.txt)。
- **并发**：`StreamableHTTPSessionManager` 在 `run()` 里开一个 anyio task group；stateless 模式
  下每个 HTTP 请求新建一次性 transport、spawn 一个 task 处理，无会话字典、无锁竞争，**SDK 层
  没有并发上限**——上限即 uvicorn/事件循环的并发能力。多 worker / 多副本无需粘性路由（这正是
  stateless 的设计目标）。sessionful 模式则维护 `_server_instances` 会话表 +
  会话属主（credential）校验，可配 `session_idle_timeout`（stateless 下配置它会直接
  `RuntimeError`）。出处：[StreamableHTTPSessionManager API](https://py.sdk.modelcontextprotocol.io/v2/api/mcp/server/streamable_http_manager)。
- **超时**：SDK **不含任何工具执行超时**——工具协程随请求存活；请求断开时 ASGI 取消传播终止
  task。超时完全是客户端行为（见 §6）。对 ArcReel 意味着：长任务工具要么内部自守时，要么走
  「入队 + 返回 task id + 轮询工具」模式，不要指望服务端兜底掐断。
- **请求体上限**：`max_request_body_size`（默认值见 SDK `DEFAULT_MAX_REQUEST_BODY_SIZE`）。

## 5. progress notification 在 streamable HTTP 下的可用性

- **机制**：工具签名加 `ctx: Context`，`await ctx.report_progress(progress, total=..., message=...)`。
  仅当客户端在请求 `_meta` 里带了 `progressToken` 时才会真正发送；未带 token 时是 **no-op**
  （v1 旧版会 raise，v2 明确改为 no-op）。progress 值须单调递增、传绝对值。出处：
  [progress tutorial](https://py.sdk.modelcontextprotocol.io/v2/llms-full.txt)（`docs_src/progress/tutorial001.py`）。
- **streamable HTTP / stateless 下可用**：进度通知以 `related_request_id` 绑定在途请求，经该
  POST 请求自己的 SSE 响应流下发，因此**不依赖会话级 GET 通知流，stateless 模式同样可用**。
  反之，与在途请求无关的通知（如 resource updated）在 sessionless 连接上没有出路、会被丢弃
  ——FastMCP 源码 docstring 对此有明确说明。出处：
  [fastmcp/server/context.py `send_notification`](https://github.com/prefecthq/fastmcp/blob/main/fastmcp_slim/fastmcp/server/context.py)。
- **前提**：响应必须是 SSE（默认）；`json_response=True` 强制纯 JSON 会失去中途下发进度的通道。

## 6. 客户端支持现状（2026-08-08）

### Claude Code（CLI，文档载明行为对应 v2.1.x）

- **远程 HTTP + Bearer**：一等支持。
  `claude mcp add --transport http arcreel https://host/mcp --header "Authorization: Bearer arc-xxx"`；
  `.mcp.json` 支持 `${API_KEY}` 环境变量插值、`headersHelper` 动态生成头。出处：
  [code.claude.com/docs/en/mcp](https://code.claude.com/docs/en/mcp)。
- **超时模型**（同页文档，精确语义）：
  - 每服务器 `timeout` 字段（ms）= 单次工具调用的**硬 wall-clock 上限**，
    **progress notification 不延长它**；未设时落到 `MCP_TOOL_TIMEOUT`，其默认约 28 小时。
  - HTTP 服务器另有 **60 秒 first-byte 计时器**（到服务器首个响应字节）；把 `timeout` /
    `MCP_TOOL_TIMEOUT` 设为 ≥60s 可抬高。SSE 响应先发出即算首字节，正常不构成约束。
  - **idle 超时**（v2.1.187+）：HTTP 服务器默认 5 分钟内既无响应也无 progress notification
    即中止调用——**这是 progress notification 的实际价值**：长任务工具持续 report_progress
    可维持调用存活。可用 `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` 调整或置 0 关闭。
  - v2.1.212+：主对话中超过 2 分钟的调用自动转后台 task，超时限制仍适用。
  - `MCP_TIMEOUT` 只管服务器启动/连接阶段。
- **注意**：claude.ai 网页版 connector（非 CLI）**不支持自定义 Bearer 头，只支持 OAuth**
  （[anthropics/claude-ai-mcp#112](https://github.com/anthropics/claude-ai-mcp/issues/112)）；
  面向 Claude Code CLI 集成不受影响。

### codex（OpenAI Codex CLI）

- **远程 streamable HTTP + Bearer**：支持，非实验特性。
  `codex mcp add arcreel --url https://host/mcp --bearer-token ...` 或 `config.toml`：
  `[mcp_servers.arcreel] url = "..."` + `bearer_token_env_var = "ARCREEL_MCP_TOKEN"` /
  `http_headers` / `env_http_headers`；OAuth（`codex mcp login`）为默认回退。出处：
  [Codex MCP 文档](https://developers.openai.com/codex/mcp)（现重定向至 learn.chatgpt.com/docs/extend/mcp）。
- **超时**：`startup_timeout_sec` 默认 10s，`tool_timeout_sec` **默认 60s**——ArcReel 的长任务
  工具需在集成文档里提醒 codex 用户调大该值；官方文档未记载 progress notification 影响超时。
- **已知坑**：`bearer_token_env_var` 引用的环境变量在 codex 进程不可见时，`codex mcp list`
  仍显示已配置，但实际请求不带 Authorization 头
  （[openai/codex#30125](https://github.com/openai/codex/issues/30125)）。

## 7. 对 ArcReel 的落地建议（供地图 #1702 汇总）

1. **选官方 SDK v1 路线（mcp 1.26.0，零新增依赖）**：`FastMCP("arcreel", stateless_http=True,
   streamable_http_path="/")` + `app.mount("/mcp", ...)` + lifespan 里 `async with
   mcp.session_manager.run()`。FastMCP 3.4.6 的增值（`combine_lifespans`、托管 IdP、OpenAPI
   转工具）当前用不上，不值得为此加一个大依赖；若未来要 OAuth 全家桶再评估。
2. **鉴权**：实现 `TokenVerifier` 桥接 `server/auth.py::_verify_api_key`，与 SDK 的
   `AuthSettings` 成对启用；不要试图靠 FastAPI `Depends` 覆盖挂载的子应用（无效）。
3. **stateless 定调**：ArcReel 服务端工具不需要 sampling/elicitation 回呼 → stateless 成立，
   且天然兼容未来多副本。长任务一律「入队 + task id + 轮询」或在工具内 `report_progress`
   维持 Claude Code idle 活性；不要做单次超长阻塞调用（codex 默认 60s 就会掐）。
4. **json_response 保持 False**（默认 SSE），否则失去进度通道。
5. 对外部署时配置 `TransportSecuritySettings` 的 allowed hosts（DNS-rebinding 防护）。
6. 迁移预期：`claude-agent-sdk` 解除 `mcp<2` 钉版后按官方 migration guide 迁到 v2
   `MCPServer`（import 路径 + transport 参数位置变化，模式不变）。

## 出处清单

- MCP Python SDK 文档（v2 站点，含 v1 迁移对照）：
  <https://py.sdk.modelcontextprotocol.io/v2/run/asgi> ·
  <https://py.sdk.modelcontextprotocol.io/v2/troubleshooting> ·
  <https://py.sdk.modelcontextprotocol.io/v2/api/mcp/server/streamable_http_manager> ·
  <https://py.sdk.modelcontextprotocol.io/v2/api/mcp/server/auth/middleware/bearer_auth>
- python-sdk 仓库文档：
  <https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/migration.md> ·
  <https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/asgi.md> ·
  <https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/legacy-clients.md>
- PyPI：<https://pypi.org/project/mcp/>（2.0.0）·
  <https://pypi.org/pypi/claude-agent-sdk/0.2.128/json>（`mcp>=1.23.0,<2.0.0`）·
  <https://pypi.org/pypi/fastmcp/json>（3.4.6）· <https://pypi.org/pypi/fastmcp-slim/json>（`mcp>=1.24.0,<2.0`）
- FastMCP 文档与源码：
  <https://github.com/prefecthq/fastmcp/blob/main/docs/deployment/http.mdx> ·
  <https://github.com/prefecthq/fastmcp/blob/main/docs/integrations/fastapi.mdx> ·
  <https://github.com/prefecthq/fastmcp/blob/main/docs/servers/lifespan.mdx> ·
  <https://github.com/prefecthq/fastmcp/blob/main/fastmcp_slim/fastmcp/server/context.py>
- Claude Code MCP 文档：<https://code.claude.com/docs/en/mcp>
- claude.ai connector Bearer 限制：<https://github.com/anthropics/claude-ai-mcp/issues/112>
- Codex MCP 文档：<https://developers.openai.com/codex/mcp>（→ learn.chatgpt.com/docs/extend/mcp）
- Codex bearer env var 坑：<https://github.com/openai/codex/issues/30125>
