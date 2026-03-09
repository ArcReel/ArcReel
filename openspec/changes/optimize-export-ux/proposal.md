## Why

当前项目导出功能存在两个核心体验问题：

1. **下载阻塞**：导出使用前端 fetch + Blob 方式下载 ZIP，整个文件必须在内存中完成接收后才触发保存。期间用户无法看到进度，也不能切换页面（切走后 fetch 被中断）。对于包含大量分镜图/视频的项目，ZIP 可达数百 MB，体验极差。
2. **全量导出冗余**：每次导出都打包项目的全部内容（含 `versions/` 目录下的所有历史版本），数据量远超用户实际需要。多数场景下用户只需要当前版本的资源，不需要历史版本文件。

## What Changes

- **浏览器原生下载**：将导出 API 的调用方式从 fetch → Blob → `<a>.click()` 改为直接让浏览器打开带认证的下载链接。浏览器原生下载支持进度显示、可暂停/恢复、不阻塞页面切换。
- **下载 URL 安全认证**：引入短时效一次性下载 token 机制，避免将长期 JWT 暴露在 URL query string 中。
- **导出范围选项**：在导出交互中新增选择——"导出全部" 与 "仅当前版本"：
  - **导出全部**：行为与现有逻辑一致，打包整个项目目录（含 `versions/`）。
  - **仅当前版本**：跳过 `versions/` 目录下的历史文件，仅保留当前使用的资源。同时在 `arcreel-export.json` 清单中记录 `scope: "current"` 标记，并在 `versions.json` 中仅保留 current version 条目作为元数据（保留 prompt 等生成信息），以便导入时恢复上下文。

## Capabilities

### New Capabilities
- `export-download-token`: 短时效一次性下载 token 的签发与验证，用于浏览器原生下载的安全认证
- `export-scope-selection`: 导出范围选择（全部 / 仅当前版本），包括后端打包逻辑和前端选项 UI

### Modified Capabilities
（无已有 spec 需要修改）

## Impact

- **后端**：
  - `server/auth.py` — 新增下载 token 签发/验证逻辑
  - `server/app.py` — 认证中间件需识别下载 token
  - `server/routers/projects.py` — 导出端点支持 scope 参数 + 下载 token 验证
  - `server/services/project_archive.py` — 打包逻辑支持 scope 过滤
- **前端**：
  - `frontend/src/api.ts` — 导出 API 改为获取下载 URL 而非 fetch Blob
  - `frontend/src/components/layout/GlobalHeader.tsx` — 导出按钮增加范围选择交互
- **API 变更**：导出端点增加 `scope` query param，新增下载 token 端点
- **兼容性**：导入逻辑已支持无 `versions/` 目录的 ZIP，`scope: "current"` 的导出包可正常导入
