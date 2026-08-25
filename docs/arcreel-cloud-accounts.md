# ArcReel 云端账号接入

这套模式将“账号管理”和“本地业务运行”分开：

- 数据中台创建的是 ArcReel 自己的账号，不是数据中台账号。
- ArcReel 仍可部署在 Windows、macOS 或 Linux 本机，登录请求发送到 ArcReel Supabase Edge Function。
- 云端账号 UUID 是唯一身份。用户名可展示和修改，但不能作为跨设备身份主键。
- 每个账号的供应商 API Key 在 ArcReel 云端使用 AES-GCM 加密保存；本地端登录后按账号 UUID 拉取并写入该本地影子用户的凭据表。
- 本地项目、素材和任务数据仍保留在本机。只有账号、角色和供应商凭据由云端统一管理。

## 云端部署

在 ArcReel 专用 Supabase Project 中执行：

```powershell
supabase link --project-ref <ARCREEL_PROJECT_REF>
supabase db push

supabase secrets set `
  ARCREEL_ADMIN_INTEGRATION_TOKEN="<至少32位随机值>" `
  ARCREEL_CREDENTIAL_ENCRYPTION_KEY="<至少32位随机值>"

supabase functions deploy arcreel-auth --no-verify-jwt
supabase functions deploy arcreel-admin --no-verify-jwt
```

`ARCREEL_ADMIN_INTEGRATION_TOKEN` 只提供给数据中台后端，不能写入 ArcReel 本地 `.env` 或任何前端环境变量。`ARCREEL_CREDENTIAL_ENCRYPTION_KEY` 必须长期保存；丢失或更换后，已存储的 API Key 将无法解密。

## 数据中台配置

在“子系统管理”中设置：

- 云端账号管理接口：`https://<ARCREEL_PROJECT_REF>.supabase.co/functions/v1/arcreel-admin`
- 子系统管理凭据：与 `ARCREEL_ADMIN_INTEGRATION_TOKEN` 完全相同
- 状态：正常

随后进入“子系统账号”：

1. 创建 ArcReel 登录账号，设置初始密码和 ArcReel 角色。
2. 在账号行点击“API Key”，按供应商配置密钥。
3. 可随时重置密码、修改角色或停用账号。

中台审计日志只记录账号 ID、供应商 ID 和操作类型，不记录密码或 API Key 原文。

## ArcReel 本地启动

正式的 ArcReel 云端登录地址和 Publishable Key 已随程序提供。普通用户不需要创建或修改
`.env`，按原有方式启动 ArcReel 后，直接使用数据中台分配的 ArcReel 账号和密码登录。

只有测试环境或迁移到另一个 Supabase Project 时，才需要在 `.env` 中**同时**覆盖：

```dotenv
ARCREEL_CLOUD_AUTH_URL=https://<ARCREEL_PROJECT_REF>.supabase.co/functions/v1/arcreel-auth
ARCREEL_CLOUD_PUBLISHABLE_KEY=sb_publishable_xxx
ARCREEL_CLOUD_SYNC_INTERVAL_SECONDS=60
```

只配置其中一个值会导致启动后的登录配置校验失败，避免把一个项目的 URL 与另一个项目的 Key 混用。
无论使用内置配置还是环境变量覆盖，云端登录失败时都不会回退到本机
`AUTH_USERNAME` / `AUTH_PASSWORD`，避免绕过账号停用和角色控制。

开发测试或紧急回滚时可设置 `ARCREEL_CLOUD_ENABLED=false` 恢复本地账号模式；正式分发版本无需设置，
默认启用云端账号。

首次登录时：

1. Edge Function 校验用户名和密码，并返回云端 UUID。
2. 本地按 UUID 查找影子用户；没有时创建。
3. 如果本地已有同名且尚未绑定云身份的用户，则绑定该用户，从而保留其原有项目和数据。
4. 拉取该 UUID 对应的供应商凭据并应用到该用户。
5. 后台定时刷新会话和配置，管理员后续修改 API Key 后无需重新部署本地 ArcReel。

## 安全边界

- 不把 Supabase `service_role`、ArcReel 集成 Token 或加密密钥放进浏览器和本地客户端。
- 停用账号后，新的登录和配置同步立即拒绝；本地已签发的 ArcReel JWT 最长仍受其本地有效期影响。高安全环境可进一步缩短本地 JWT 有效期或增加每次请求的云端状态校验。
- 删除账号应作为最后手段，日常使用“停用”。这样便于审计和恢复，也避免误删关联凭据。
- 同一个云端账号可以在多台机器登录，但本地业务数据不会自动互相同步。
