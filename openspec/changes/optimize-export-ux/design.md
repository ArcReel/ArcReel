## Context

当前导出流程：前端通过 `fetch` 调用 `GET /api/v1/projects/{name}/export`（携带 Bearer JWT），等待完整 ZIP 响应体加载到内存（Blob），再通过创建 `<a>` 标签触发浏览器保存。后端使用 FastAPI `FileResponse` 返回临时 ZIP 文件。

问题：大文件下载期间无进度指示，用户切换页面会中断 fetch。且每次导出都包含 `versions/` 目录下的所有历史版本文件，数据量冗余。

认证现状：JWT（HS256，7 天有效期），通过 `Authorization: Bearer` header 传递。SSE 端点使用 `?token=` query param 回退。导出端点不在白名单内，需要认证。

## Goals / Non-Goals

**Goals:**
- 导出下载由浏览器原生接管，支持进度显示、暂停恢复、切换页面不中断
- 下载 URL 的认证安全：不暴露长期 JWT，使用短时效一次性 token
- 支持两种导出范围：全部（含版本历史）和仅当前版本
- "仅当前版本" 导出包可被正常导入，保留必要的版本元数据

**Non-Goals:**
- 不改造导入流程（现有导入逻辑已能处理无 versions/ 的 ZIP）
- 不做断点续传或分块下载
- 不做后台异步打包 + 通知机制（当前项目体量无此必要）
- 不修改 SSE 的 `?token=` 认证方式

## Decisions

### 1. 浏览器原生下载方案：签发下载 token + `window.open`

**方案**：前端先调用 `POST /api/v1/projects/{name}/export/token` 获取短时效下载 token，然后通过 `window.open` 或 `<a>` 标签打开 `GET /api/v1/projects/{name}/export?download_token=xxx&scope=full|current`，浏览器直接接管下载。

**备选方案**：
- *直接将 JWT 放 URL query*：简单但不安全，JWT 有效期长（7 天），会出现在浏览器历史、服务器日志中。**否决**。
- *Cookie 认证*：需要改造整个认证体系，引入 CSRF 防护，改动面太大。**否决**。
- *Content-Disposition + fetch streaming*：fetch streaming API 浏览器支持参差不齐，且仍需前端代码维持连接。**否决**。

**下载 token 设计**：
- 签发端点：`POST /api/v1/projects/{name}/export/token`（需 Bearer JWT 认证）
- Token 格式：JWT（HS256，与现有共享密钥），payload 包含 `sub`（用户名）、`project`（项目名）、`purpose: "download"`、`exp`（5 分钟过期）
- 验证规则：导出端点验证 `download_token` query param，校验 `purpose` 和 `project` 字段匹配
- 一次性：不做服务端状态管理（无需 Redis），短时效 + 绑定项目名即可满足安全需求

### 2. 导出范围参数：`scope` query param

**方案**：导出端点接受 `scope=full|current` query param（默认 `full` 向后兼容）。

- `scope=full`：现有行为，打包整个项目目录
- `scope=current`：
  - 跳过 `versions/` 目录下的历史文件（`versions/storyboards/`、`versions/videos/` 等）
  - 保留 `versions/versions.json`，但裁剪为仅包含 current version 条目
  - 清单文件 `arcreel-export.json` 的 `scope` 字段设为 `"current"`

### 3. versions.json 裁剪策略

"仅当前版本" 导出时，`versions.json` 中每个资源条目只保留 `current_version` 指向的那一条 version 记录。这样：
- 保留了 prompt、created_at 等生成元数据
- 文件路径指向的版本文件存在（当前版本文件在主资源目录下，而非 versions/ 子目录）
- 导入后 VersionManager 可正常工作（只有一个版本）

### 4. 前端交互：点击导出时弹出选择弹窗

点击 "导出 ZIP" 按钮后弹出简洁的选择弹窗（非全屏 modal），提供两个选项卡片：
- "仅当前版本"（推荐，体积更小）
- "全部数据"（含版本历史）

选择后立即触发下载 token 签发 → 浏览器原生下载。

## Risks / Trade-offs

- **[下载 token 时效过短]** → 5 分钟窗口足够覆盖从签发到浏览器发起请求的延迟。如遇极端网络延迟，用户可重新点击导出。
- **[下载 token 非一次性]** → 理论上 5 分钟内可复用 token 多次下载，但绑定了项目名 + 短时效，风险可接受。如需严格一次性可后续加 Redis nonce。
- **[裁剪 versions.json 可能丢失历史上下文]** → 这是 "仅当前版本" 的预期行为，用户在选择时应有明确感知（UI 上标注）。
- **[向后兼容]** → `scope` 默认为 `full`，现有调用方（如果有外部集成）不受影响。
