## ADDED Requirements

### Requirement: API Key 生成
系统 SHALL 提供 API Key 创建接口，生成格式为 `arc-` + 32 位随机字符串的密钥，返回完整密钥（仅在创建时可见），数据库仅存储 SHA-256 哈希值。

#### Scenario: 成功创建 API Key
- **WHEN** 已认证用户调用 `POST /api/v1/api-keys` 并提供 `name` 参数
- **THEN** 系统返回包含完整 `key`、`name`、`key_prefix`、`created_at`、`expires_at` 的响应，状态码 201

#### Scenario: 创建时名称重复
- **WHEN** 已认证用户创建与现有 key 同名的 API Key
- **THEN** 系统返回 409 错误

### Requirement: API Key 列表查询
系统 SHALL 提供 API Key 列表查询接口，返回所有 key 的元数据（不含完整密钥）。

#### Scenario: 查询 API Key 列表
- **WHEN** 已认证用户调用 `GET /api/v1/api-keys`
- **THEN** 系统返回所有 key 的 `id`、`name`、`key_prefix`、`created_at`、`expires_at`、`last_used_at`

### Requirement: API Key 删除（吊销）
系统 SHALL 提供 API Key 删除接口，立即使该 key 失效。

#### Scenario: 成功删除 API Key
- **WHEN** 已认证用户调用 `DELETE /api/v1/api-keys/{key_id}`
- **THEN** 系统删除该 key 记录，后续使用该 key 的请求返回 401

#### Scenario: 删除不存在的 key
- **WHEN** 已认证用户删除不存在的 key_id
- **THEN** 系统返回 404

### Requirement: Bearer Token 认证分流
系统 SHALL 在 `_verify_and_get_payload` 中根据 token 前缀判定认证模式：以 `arc-` 开头走 API Key 验证路径，否则走 JWT 验证路径。

#### Scenario: API Key 认证成功
- **WHEN** 请求携带 `Authorization: Bearer arc-xxxxx` 且该 key 在数据库中存在且未过期
- **THEN** 系统返回 `{"sub": "apikey:<key_name>", "via": "apikey"}` payload，并更新 `last_used_at`

#### Scenario: API Key 已过期
- **WHEN** 请求携带有效格式的 API Key 但已超过 `expires_at`
- **THEN** 系统返回 401

#### Scenario: API Key 不存在
- **WHEN** 请求携带 `arc-` 前缀 token 但哈希未匹配到数据库记录
- **THEN** 系统返回 401

#### Scenario: JWT 认证不受影响
- **WHEN** 请求携带不以 `arc-` 开头的 Bearer token
- **THEN** 系统按原有 JWT 验证流程处理

### Requirement: API Key 缓存
系统 SHALL 对 API Key 查询结果使用内存缓存（LRU, TTL 5 分钟），减少数据库查询。

#### Scenario: 缓存命中
- **WHEN** 同一 API Key 在 5 分钟内多次请求
- **THEN** 仅首次触发数据库查询，后续从缓存读取

#### Scenario: Key 删除后缓存失效
- **WHEN** API Key 被删除
- **THEN** 该 key 的缓存条目 SHALL 被立即清除
