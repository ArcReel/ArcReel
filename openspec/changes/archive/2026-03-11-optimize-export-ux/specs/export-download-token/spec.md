## ADDED Requirements

### Requirement: 签发下载 token
系统 SHALL 提供 `POST /api/v1/projects/{project_name}/export/token` 端点，为已认证用户签发短时效下载 token。

该 token 为 JWT（HS256），payload SHALL 包含：
- `sub`：当前用户名
- `project`：请求的项目名
- `purpose`：固定值 `"download"`
- `exp`：签发时间 + 300 秒（5 分钟）

端点 SHALL 返回 JSON：`{ "download_token": "<jwt>", "expires_in": 300 }`。

#### Scenario: 已认证用户成功获取下载 token
- **WHEN** 已认证用户对存在的项目调用 `POST /api/v1/projects/{name}/export/token`
- **THEN** 系统返回 200，响应体包含 `download_token` 字符串和 `expires_in: 300`

#### Scenario: 未认证用户请求下载 token
- **WHEN** 未携带有效 Bearer JWT 的请求调用 `POST /api/v1/projects/{name}/export/token`
- **THEN** 系统返回 401

#### Scenario: 项目不存在时请求下载 token
- **WHEN** 已认证用户对不存在的项目调用 `POST /api/v1/projects/{name}/export/token`
- **THEN** 系统返回 404

### Requirement: 导出端点接受下载 token 认证
导出端点 `GET /api/v1/projects/{name}/export` SHALL 支持通过 `download_token` query param 进行认证，作为 Bearer JWT 的替代方式。

验证规则：
- token 的 `purpose` 字段 MUST 为 `"download"`
- token 的 `project` 字段 MUST 与 URL 中的 `{name}` 一致
- token MUST 未过期

当 `download_token` 合法时，请求无需携带 `Authorization` header。

#### Scenario: 使用合法下载 token 导出
- **WHEN** 请求携带合法的 `download_token` query param 访问导出端点
- **THEN** 系统正常返回 ZIP 文件，无需 Authorization header

#### Scenario: 使用过期下载 token 导出
- **WHEN** 请求携带已过期的 `download_token` query param 访问导出端点
- **THEN** 系统返回 401，detail 为 "下载链接已过期，请重新导出"

#### Scenario: 使用项目不匹配的下载 token 导出
- **WHEN** 请求携带 `download_token`（签发给项目 A）访问项目 B 的导出端点
- **THEN** 系统返回 403，detail 为 "下载 token 与目标项目不匹配"

#### Scenario: 下载 token 不影响现有认证方式
- **WHEN** 请求携带合法 Bearer JWT（无 download_token）访问导出端点
- **THEN** 系统正常返回 ZIP 文件（向后兼容）

### Requirement: 认证中间件放行下载 token
认证中间件 SHALL 对导出端点的请求进行特殊处理：当请求包含 `download_token` query param 时，SHALL 将验证委托给导出端点自身处理，中间件不拦截。

#### Scenario: 中间件放行含下载 token 的导出请求
- **WHEN** 未携带 Authorization header 但携带 `download_token` query param 的请求访问 `/api/v1/projects/{name}/export`
- **THEN** 认证中间件放行该请求，不返回 401
