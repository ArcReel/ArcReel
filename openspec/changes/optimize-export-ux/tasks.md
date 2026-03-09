## 1. 下载 Token 后端实现

- [x] 1.1 在 `server/auth.py` 中添加 `create_download_token(username, project_name)` 和 `verify_download_token(token, project_name)` 函数
- [x] 1.2 在 `server/routers/projects.py` 中添加 `POST /api/v1/projects/{name}/export/token` 端点，签发下载 token
- [x] 1.3 修改 `server/app.py` 认证中间件，对 `/api/v1/projects/*/export` 路径且携带 `download_token` query param 的请求放行
- [x] 1.4 修改 `server/routers/projects.py` 导出端点，支持 `download_token` query param 认证（校验 purpose、project 匹配、过期）
- [x] 1.5 为下载 token 相关逻辑编写单元测试（`tests/test_auth.py` 补充 + `tests/test_projects_archive_routes.py` 补充）

## 2. 导出范围（Scope）后端实现

- [x] 2.1 修改 `server/routers/projects.py` 导出端点，接受 `scope` query param（`full` / `current`，默认 `full`），传递给 `ProjectArchiveService`
- [x] 2.2 修改 `server/services/project_archive.py` 的 `export_project` 方法，接受 `scope` 参数
- [x] 2.3 实现 `scope=current` 逻辑：遍历目录时跳过 `versions/storyboards/`、`versions/videos/`、`versions/characters/`、`versions/clues/` 下的文件
- [x] 2.4 实现 `scope=current` 时 `versions/versions.json` 裁剪逻辑：只保留每个资源的 current_version 条目
- [x] 2.5 修改 `arcreel-export.json` 清单写入逻辑，`scope` 字段反映实际导出范围
- [x] 2.6 为 scope 相关逻辑编写单元测试（`tests/test_project_archive_service.py` 补充）

## 3. 前端导出交互改造

- [x] 3.1 在 `frontend/src/api.ts` 中添加 `requestExportToken(projectName)` 方法，调用签发 token 端点
- [x] 3.2 在 `frontend/src/api.ts` 中添加 `getExportDownloadUrl(projectName, downloadToken, scope)` 辅助方法，拼接完整下载 URL
- [x] 3.3 创建 `ExportScopeDialog` 组件（可复用弹窗），提供"仅当前版本"和"全部数据"两个选项
- [x] 3.4 修改 `GlobalHeader.tsx` 的 `handleExportProject`：点击导出按钮后弹出 `ExportScopeDialog`，选择后签发 token 并触发 `window.open` 浏览器原生下载
- [x] 3.5 移除 `frontend/src/api.ts` 中旧的 `exportProject` fetch+Blob 逻辑（确认无其他调用方后删除）
- [x] 3.6 为前端导出相关改动编写测试（`GlobalHeader.test.tsx` 补充 + API 测试补充）
