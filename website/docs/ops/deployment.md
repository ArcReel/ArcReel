---
id: deployment
title: 部署与运维
sidebar_position: 1
---

# 部署与运维 {#deployment}

本文档介绍 ArcReel 的默认部署、PostgreSQL 生产部署、环境变量、数据持久化、升级、备份与恢复、反向代理及故障排查。正式支持边界请参阅 [安全政策](https://github.com/ArcReel/ArcReel/blob/main/SECURITY.md)，完整信任边界请参阅 [安全威胁模型](https://github.com/ArcReel/ArcReel/blob/main/docs/security/threat-model.md)。

## 部署方式选择 {#choose-deployment-mode}

| 场景 | 推荐方式 | 数据库 | 说明 |
|---|---|---|---|
| 首次体验、个人轻量使用 | `deploy/` | SQLite | 配置最少，启动最快 |
| 长期运行、并发访问、正式服务 | `deploy/production/` | PostgreSQL | 更适合并发、备份和运维，但不提供用户隔离 |
| 本地开发 | 源码启动 | SQLite 或 PostgreSQL | 见[贡献指南](../dev/contributing.md) |

无论选择哪种方式，项目图片、视频和其他生成资产都需要持久化保存。

ArcReel 当前按单一可信操作员设计，不支持互不信任的用户共享实例。PostgreSQL 生产部署不会增加租户隔离、角色权限或按用户划分的项目授权。

## 1. 默认部署：SQLite {#sqlite-deployment}

### 1.1 启动 {#sqlite-start}

```bash
git clone https://github.com/ArcReel/ArcReel.git
cd ArcReel/deploy

cp .env.example .env
```

编辑 `.env`：

```dotenv
AUTH_USERNAME=admin
AUTH_PASSWORD=请设置强密码
AUTH_TOKEN_SECRET=请设置长期固定的随机密钥
# LOG_LEVEL=INFO
```

生成随机密钥：

```bash
openssl rand -hex 32
```

启动：

```bash
docker compose up -d
```

验证：

```bash
docker compose ps
docker compose logs --tail=100 arcreel
curl http://localhost:1241/health
```

### 1.2 持久化目录 {#sqlite-volumes}

默认 Compose 会挂载：

| 宿主机路径 | 容器路径 | 内容 |
|---|---|---|
| `deploy/.env` | `/app/.env` | 认证和运行配置 |
| `deploy/projects/` | `/app/projects` | 数据根：项目、生成资产、默认 SQLite 数据库、日志、Vertex AI 凭据等全部运行数据 |
| `deploy/claude_data/` | `/root/.claude` | Agent 运行时相关数据 |

`deploy/projects/` 是 ArcReel 的数据根（`ARCREEL_DATA_DIR`，容器内为 `/app/projects`）。项目存放在其中的 `projects/` 子目录下，其余运行数据与之并列：

```text
deploy/projects/               数据根
├── projects/<项目名>/         项目与生成资产
├── deleted_projects/          已删除项目，保留 30 天
├── global_assets/             全局资产库
├── users/<user_id>/memory/    Agent 用户记忆
├── arcreel.db                 默认 SQLite 数据库（连同 -wal / -shm）
├── logs/                      应用日志
├── vertex_keys/               Vertex AI 凭据文件
├── trial_runs/                端点「测试连接」的产物
└── runtime/                   迁移完成标记、生成准入锁、图片缩略图缓存等运行时状态
```

只有在 `projects/` 下、名称由英文字母、数字或中划线组成且包含 `project.json` 的目录才会被识别为项目，数据根中的其他条目不会出现在项目列表中。在设置页「关于」中下载的诊断日志会列出数据根及上述各类数据的实际路径。

> 备份时归档整个数据根，不要只备份数据库而忽略项目文件。数据库保存任务、配置和索引信息，项目目录保存原始素材和生成文件，两者需要保持一致。

项目回收站位于 `projects/<项目名>/.arcreel/trash/`。完整的数据根备份会保留其中的删除记录，但项目 ZIP 导出不包含它。删除记录自删除起保留 30 天，应用启动时及运行期间会定期清理；恢复备份不会重置记录的保留期。

已删除的项目会连同其项目回收站一起整体移至 `deleted_projects/<项目名>#deleted-<删除时间>/`，同样保留 30 天并按相同方式清理。你可以在项目列表页的「已删除项目」中恢复或彻底删除；恢复时若原名已被占用，项目会自动附加后缀以新名称恢复。

## 2. 生产部署：PostgreSQL {#postgresql-deployment}

### 2.1 启动 {#postgresql-start}

```bash
cd "$(git rev-parse --show-toplevel)/deploy/production"
cp .env.example .env
```

编辑 `.env`：

```dotenv
AUTH_USERNAME=admin
AUTH_PASSWORD=请设置强密码
AUTH_TOKEN_SECRET=请设置长期固定的随机密钥
POSTGRES_PASSWORD=请设置数据库密码
# LOG_LEVEL=INFO
```

推荐生成只含十六进制字符的密码：

```bash
openssl rand -hex 16
```

默认 Compose 会将 `POSTGRES_PASSWORD` 的原始值传给 PostgreSQL，同时拼入 `DATABASE_URL` 的密码段。如果密码包含 `@`、`:`、`/`、`?`、`#`、`%` 等 URL 保留字符，连接 URI 中的密码必须进行百分号编码。切勿将编码后的值直接填入 `POSTGRES_PASSWORD`：PostgreSQL 需要原始密码，只有 URI 需要编码。

当必须使用特殊字符时，在 `.env` 中分开保存原始值与编码值：

```dotenv
POSTGRES_PASSWORD='p@ss/word'
POSTGRES_PASSWORD_URLENCODED=p%40ss%2Fword
```

随后将 `deploy/production/docker-compose.yml` 中 `DATABASE_URL` 的密码部分改为 `${POSTGRES_PASSWORD_URLENCODED}`，PostgreSQL 容器的 `POSTGRES_PASSWORD` 保持不变。可用 `urllib.parse.quote(raw_password, safe="")` 生成编码值。如果不想维护这项 Compose 定制，直接使用上述十六进制密码即可。

启动：

```bash
docker compose up -d
```

验证：

```bash
docker compose ps
docker compose logs --tail=100 postgres
docker compose logs --tail=100 arcreel
curl http://localhost:1241/health
```

### 2.2 PostgreSQL 持久化目录 {#postgresql-volumes}

| 宿主机路径 | 内容 |
|---|---|
| `deploy/production/pgdata/` | PostgreSQL 数据目录 |
| `deploy/production/projects/` | 数据根：项目、媒体资产、日志、Vertex AI 凭据等，布局与[默认部署](#sqlite-volumes)相同，不含数据库 |
| `deploy/production/claude_data/` | Agent 运行时数据 |
| `deploy/production/.env` | 认证和数据库配置 |

`pgdata/` 仅用于保存 PostgreSQL 集群数据，`projects/` 则是数据根，保存项目元数据、媒体资产及其他运行数据，两者都必须持久化并配套备份。生产部署通过 `DATABASE_URL` 连接 PostgreSQL，不会使用 `deploy/production/projects/arcreel.db`；切勿将 SQLite 文件复制进 `pgdata/`，也不要将这两个目录当作可互相替代的数据库备份。

### 2.3 数据库迁移 {#database-migrations}

ArcReel 启动时会自动运行 Alembic 升级数据库结构；你也可以先手动执行 `alembic upgrade head`，再启动应用。迁移会先记录升级前的数据库版本，以便[保留原来使用的模型](#project-schema-migrations)。写入失败时先修复数据库，再重试。SQLite 在首次创建全局设置表后写入失败时，重试可以继续，但旧版本视为未知，需在「全局设置 → 默认模型」检查并重新选择。请通过实际数据库执行迁移，`alembic upgrade --sql` 离线模式不受支持。

升级前仍需备份数据库与数据根，见[备份与恢复](#backup-and-restore)。

## 3. 环境变量 {#environment-variables}

默认部署示例当前包含以下核心变量：

| 变量 | 默认值 | 建议 |
|---|---|---|
| `AUTH_USERNAME` | `admin` | 可修改管理员用户名 |
| `AUTH_PASSWORD` | 空 | 正式部署必须显式设置强密码 |
| `AUTH_TOKEN_SECRET` | 空 | 建议设置长期固定随机值；留空时自动生成并保存到数据根下的 `runtime/auth-token-secret`，重启后沿用 |
| `AUTH_ENABLED` | `true` | 远程部署不得关闭；设为 `false` 时全部管理接口无需认证 |
| `LOG_LEVEL` | `INFO` | 排障时临时改为 `DEBUG`，完成后恢复 |
| `POSTGRES_PASSWORD` | 无 | 仅生产部署需要，必须设置 |
| `TZ` | `Asia/Shanghai` | 可在 Compose 环境中覆盖 |
| `DATABASE_URL` | SQLite 默认路径 | 生产 Compose 自动设置 PostgreSQL URL |
| `ARCREEL_DATA_DIR` | `projects` | 数据根；项目、默认 SQLite 数据库、日志和 Vertex 凭据都在其下 |
| `CORS_ORIGINS` | 通配 | 设为白名单时，浏览器型 MCP 客户端的 Origin 也须列入 |
| `ARCREEL_OFFICIAL_SERVICE_URL` | 内置官方服务地址 | 市场安装量、评分与分享提交所用的官方服务；设为空即整体关闭，市场回到只读取市场源的形态 |

注意：

- 修改 `AUTH_TOKEN_SECRET` 会导致现有登录 Token 失效。显式设置时以该值为准，不再使用数据根下自动生成的密钥文件；未显式设置时，删除该密钥文件并重启会生成新密钥，同样会使现有登录失效。
- 修改 `.env` 后，请在 Compose 目录执行 `docker compose up -d` 重建容器。`docker compose restart` 和容器自动拉起只会沿用创建容器时的环境变量，不会重新加载 `.env`。若启动时检测到进程环境变量与挂载的 `.env` 不一致，日志会输出一条仅列出变量名的 WARNING。
- `AUTH_ENABLED=false` 仅适用于受独立网络边界保护的本机环境。Compose 默认会将 `1241` 端口发布到宿主机所有网卡，远程部署切勿关闭认证。认证关闭时，启动日志会输出 WARNING。
- `.env` 中可能包含密钥，不要提交到版本库。
- Vertex 凭据文件应只授予运行 ArcReel 的用户读取权限。
- 文件日志固定写入数据根下的 `logs/`。旧变量 `ARCREEL_LOG_DIR` 已失效，若仍配置，启动日志会输出一次提示；如只需 stdout 日志，可设置 `ARCREEL_LOG_FILE_DISABLED=true`。
- 第三方模型 API Key 通常在 ArcReel 设置页中管理，不要写入公开文档。

远程 MCP 端点为 `/mcp`，始终要求使用 `arc-` 前缀的 API Key，即使设置了 `AUTH_ENABLED=false` 也不会允许匿名访问。外部接入无需额外的 `MCP_*` 配置：只需将设置页「外部 Agent 接入」中提供的端点地址，以及在「访问令牌」中创建的 API Key 填入客户端，并通过支持 SSE 长连接的 HTTPS 反向代理、VPN 或安全隧道访问即可。

服务端不校验请求的 `Host` 头，接口访问边界由每次请求强制校验的 API Key 承担；域名归属由部署层控制，请在反向代理上限定 `server_name`（Nginx）或等价规则，仅将预期域名的请求转发给 ArcReel。

当 `CORS_ORIGINS` 保持默认通配时，浏览器型 MCP 客户端同样无需额外配置；一旦收紧为白名单，必须将该客户端的 Origin 一并列入——这份白名单同时约束应用 API 与 MCP 端点，不存在独立的 MCP 专用白名单。

远程 MCP 不提供 OAuth 授权，也不发布 OAuth 受保护资源元数据（RFC 9728）；因此，仅支持 OAuth 自动发现、无法手动填写 Bearer 凭证的客户端无法接入。

ArcReel 的沙箱要求父进程环境中不得保留供应商密钥。若以下任一凭据环境变量存在非空值，服务会拒绝启动，并提示将其迁移至 WebUI 设置页：

- `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`
- `ARK_API_KEY` / `XAI_API_KEY` / `GEMINI_API_KEY` / `VIDU_API_KEY`
- `DASHSCOPE_API_KEY` / `MINIMAX_API_KEY` / `AGNES_API_KEY` / `OPENAI_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS`（Vertex 凭据在设置页上传，保存在数据根下的 `vertex_keys/`）

`ANTHROPIC_BASE_URL`、模型名等非密钥配置不会单独触发启动拒绝，但仍建议与对应凭据一起在 WebUI 中管理。

## 4. 健康检查和日志 {#health-and-logs}

### 4.1 健康检查 {#health-check}

Compose 使用：

```text
GET /health
```

手动检查：

```bash
curl -f http://localhost:1241/health
```

该端点无需认证。官方镜像的响应中包含 `commit` 字段，值为构建该镜像对应的 commit sha，可用于确认实例运行的版本；从源码直接运行时该字段为 `null`。

### 4.2 查看日志 {#view-logs}

```bash
# 最近 200 行
docker compose logs --tail=200 arcreel

# 持续跟踪
docker compose logs -f arcreel

# 生产数据库日志
docker compose logs -f postgres
```

文件日志位于数据根下的 `logs/`，默认部署为 `deploy/projects/logs/`。

不要在公开 Issue 中直接粘贴完整日志。提交前先清理：

- API Key；
- Token；
- Base URL 中的凭据；
- 用户输入内容；
- 本地文件路径中的隐私信息。

## 5. 升级 {#upgrade}

### 5.1 升级前 {#before-upgrade}

1. 阅读 [CHANGELOG](https://github.com/ArcReel/ArcReel/blob/main/CHANGELOG.md) 和目标 Release 说明；
2. 确认是否存在破坏性变更；
3. 备份数据根和数据库，见[备份与恢复](#backup-and-restore)；
4. 记录当前镜像版本；
5. 在可接受的维护窗口执行升级。

> ArcReel 不支持降级。升级可能改写数据根布局、数据库结构和项目结构，旧版本无法读取升级后的数据；要回到旧版本，需要停止服务并恢复升级前的备份。

Compose 默认配置 60 秒容器停机总宽限期（`stop_grace_period: 60s`），涵盖 HTTP 连接排空、在途任务与常驻 Agent 会话并行退出及数据库关闭。容器收到停机信号后，服务会立即停止领取新的排队任务、主动断开项目事件与 Agent 会话的浏览器实时连接（新版本启动后自动重连），并在宽限期内等待短时在途任务完成与 Agent 会话优雅关闭。超出宽限期仍处于运行中的视频任务若已记录供应商任务号、检查点有效且原调用通道支持续查，新实例启动后会尝试自动续查并取回结果，不会重复提交计费；需要给本地渲染或慢速图片任务留出更长收尾时间时，可在 Compose 文件中调高 `stop_grace_period`。

### 5.2 默认部署升级 {#upgrade-sqlite-deployment}

从数据根布局调整之前的版本升级时，请先沿用原来的 Compose 文件执行下面的命令，再按[数据根布局迁移](#data-root-layout-migration)切换至单卷 Compose。

在 `deploy/` 中：

```bash
# 先备份，见后文
docker compose pull
docker compose up -d

docker compose ps
docker compose logs --tail=100 arcreel
curl -f http://localhost:1241/health
```

### 5.3 生产部署升级 {#upgrade-postgresql-deployment}

从数据根布局调整之前的版本升级时，同样先沿用原来的 Compose 文件，再按[数据根布局迁移](#data-root-layout-migration)切换至单卷 Compose。

在 `deploy/production/` 中：

```bash
# 先备份数据库和数据根 projects/
docker compose pull
docker compose up -d

docker compose ps
docker compose logs --tail=100 postgres
docker compose logs --tail=200 arcreel
curl -f http://localhost:1241/health
```

应用启动时会执行数据库迁移。不要在没有备份的情况下跳过多个版本直接升级。

### 5.4 数据根布局迁移 {#data-root-layout-migration}

从项目、数据库等平铺在数据根下的旧版本升级时，首次启动会自动将数据根整理为[持久化目录](#sqlite-volumes)一节所示的新布局。数据根仍是原先配置和挂载的目录，无需修改环境变量：

- 项目目录会移入数据根下的 `projects/`。名称合法但不含 `project.json` 的目录也会原样移入，只是不会显示为项目；与 `logs`、`users`、`runtime` 等系统目录同名的目录除外，它们会留在原位。名为 `projects` 的项目移入后名称保持不变。
- `_global_assets/` 更名为 `global_assets/`，`.arcreel/users/` 移至 `users/`，迁移标记等运行时状态统一收归 `runtime/`。
- 数据根上一级 `vertex_keys/`（Docker 中为 `./vertex_keys` 卷）中的凭据会并入数据根下的 `vertex_keys/`：关联了凭据记录的文件按凭据编号保存为 `vertex_cred_<凭据编号>.json`，其余 `.json` 文件保持原名。
- 既有的 Agent 会话、全局资产、Vertex 凭据、费用记录和排队中的任务都会随之更新，升级后可照常使用。

这些步骤逐步执行，每一步都可以安全重跑：容器被杀或进程崩溃后，下次启动会接着完成。全部完成后在 `runtime/` 写入完成标记，之后每次启动只检查这个标记。某一步失败时，启动日志记录 ERROR，服务停止启动，不会在半迁移的布局上继续运行；按日志处理后重启即可续跑。例如数据根的 `projects/` 下已有与待搬项目同名的目录时，迁移会停下，需要人工确认保留哪一份。全局资产和用户记忆在新位置已有同名文件时不会停下：旧文件留在原位置，启动日志记录告警。

另有两项不受完成标记控制，每次启动都会检查：

- 默认 SQLite 数据库 `.arcreel.db`（连同 `-wal` / `-shm`）会更名为 `arcreel.db`。该步骤在解析数据库地址时执行，无论先启动应用还是先运行 Alembic 都会连接到原库。若新旧两个文件同时存在，则不改名，继续使用 `arcreel.db` 并在启动日志中记录告警。配置了 `DATABASE_URL`（包括 PostgreSQL）时不会触动数据库。
- 原先代码目录下的日志目录（Docker 中为 `./logs` 卷）中的文件会并入数据根下的 `logs/`，若新位置已有同名文件则旧文件留在原处。该步骤出错仅记录日志，不阻止启动。由 `ARCREEL_LOG_DIR` 指定的自定义日志目录不会被搬动。

**升级前备份整个数据根**，连同数据根外的旧日志目录与旧凭据目录（Docker 中为 `./logs`、`./vertex_keys` 卷，其余部署为代码目录下的 `logs/` 与数据根上一级的 `vertex_keys/`）；外部数据库另行备份。迁移会把这两处的文件搬进数据根。旧版本会把 `projects/` 当成一个项目，也不认识新的数据库文件名；降级需要恢复这份备份。

新版 Compose 仅挂载一个数据卷 `./projects`（外加 `.env` 和 `claude_data/`），不再挂载 `./logs` 与 `./vertex_keys`。旧卷中的文件只有在启动时仍处于挂载状态才会被并入数据根，其中 Vertex 凭据仅在布局迁移那一次搬入，写入完成标记后不再处理。因此，Docker 部署从旧版本升级需要分两步进行（以下以 `deploy/` 为例，生产部署请替换为 `deploy/production/`）：

1. **保留旧卷挂载，先启动新版。** 如果固定了镜像标签，先将 ArcReel 的镜像标签改为目标版本。保留原 Compose 的卷挂载，按[默认部署升级](#upgrade-sqlite-deployment)或[生产部署升级](#upgrade-postgresql-deployment)执行 `docker compose pull` 与 `docker compose up -d`。此时两个旧卷仍处于挂载状态，首次启动会将其中的文件并入数据根。请在启动日志中确认出现「数据根布局迁移完成」，并核实原有日志和凭据文件已出现在 `deploy/projects/logs/` 与 `deploy/projects/vertex_keys/` 下（凭据文件名可能已更名为 `vertex_cred_<凭据编号>.json`）。
2. **再切换至单卷 Compose。** 删除 Compose 中的 `./logs` 与 `./vertex_keys` 两行挂载配置（或直接换用新版 Compose 文件），再执行 `docker compose up -d`。完成后即可删除 `deploy/logs/` 和 `deploy/vertex_keys/`；若其中仍有残留文件（例如数据根中已存在同名文件，或旧卷以只读方式挂载），删除前请先确认不再需要。

如果已经换用新版 Compose 但尚未启动新版本，请在首次启动前临时把这两行挂载加回去，再按上述两个阶段操作。

如果已经换用新版 Compose 且启动过新版本，旧卷中的文件将不再自动并入，需要手动补齐。由于旧卷文件由容器内 root 用户写入，必要时请在命令前加上 `sudo`：

```bash
# 在 deploy/ 中执行
docker compose stop arcreel

# 设置页上传的 Vertex 凭据保存为 vertex_cred_<凭据编号>.json，ArcReel 按这个文件名读取，拷贝时保持原名
cp -p vertex_keys/vertex_cred_*.json projects/vertex_keys/
chmod 600 projects/vertex_keys/vertex_cred_*.json

# 旧日志放进 logs/ 下的子目录，避免与新版本已写出的 arcreel.log 同名覆盖
mkdir -p projects/logs/pre-upgrade
cp -Rp logs/. projects/logs/pre-upgrade/

docker compose start arcreel
```

确认设置页中的 Vertex 凭据可正常使用后，再删除 `deploy/logs/` 和 `deploy/vertex_keys/`。旧目录中不符合 `vertex_cred_<凭据编号>.json` 命名格式的凭据文件不会被读取，对应凭据需在设置页重新上传。

### 5.5 项目结构迁移 {#project-schema-migrations}

启动服务时，ArcReel 会自动升级项目数据。请在升级前[备份数据根与数据库](#backup-and-restore)，并为迁移预留足够磁盘空间。大型项目可能需要复制整个项目目录，首次启动耗时可能较长。

如果项目目录或归档解包的临时目录经由上级目录的符号链接访问，迁移仍会将旧剧本归到对应的集，并保留各集已有的媒体内容。

**查看迁移结果**

- 点击项目页中的「本次升级调整了 N 项内容」可打开只读迁移报告。报告中的「已调整」列出结构规整及保留副本的位置，「未能带入」列出原样保留但未登记的文件。归档导入涉及此类调整时，系统会在导入完成前请你确认；取消将放弃本次导入。
- 从旧版本升级后，请检查全局与项目的默认模型。ArcReel 会尽量保留升级前实际使用的模型；若旧设置无法还原，需在设置页重新选择。导入旧项目 ZIP 时，旧配置会按导入时的推荐模型补全，导入后也请检查项目设置。
- 项目级迁移备份保存在被改写文件旁，文件名以 `.bak.v<起点版本>-<时间戳>` 结尾；冲突中保留的副本以 `.kept.v<起点版本>-<时间戳>` 结尾。项目级备份仅覆盖迁移涉及的文件，整站恢复仍需使用升级前的完整备份。

**项目迁移失败**

1. 保留项目目录、备份与保留副本。根据启动日志检查磁盘空间、权限或损坏的文件，切勿手工修改 schema 版本。
2. 解决问题后重启服务，ArcReel 会自动重试迁移。
3. 如果断电或容器被强制终止后项目目录消失，请先停止服务，在项目目录同级位置查找以 `.<项目名>.v6-rollback-` 开头的目录。若存在多个，选择修改时间最新的一个，将其改回原项目名后再启动服务。恢复完成前切勿新建同名项目或删除隐藏目录。

逐版本的规则见[项目数据迁移说明](https://github.com/ArcReel/ArcReel/blob/main/docs/agents/project-migrations.md)。

### 5.6 固定版本 {#pin-version}

`latest` 适合快速体验，但生产环境更适合固定 Release 标签。

将 Compose 中的镜像改为：

```yaml
image: arcreel/arcreel:X.Y.Z
```

升级时显式修改版本号，可以降低无意中拉取新版本的风险。

## 6. 备份与恢复 {#backup-and-restore}

迁移整个站点到不同的绝对路径后，应用启动时会按当前项目目录调整已有 Agent 会话的存储位置，保留续聊上下文；已删除项目的会话也会随其恢复到的新名称调整。使用 `ARCREEL_SDK_SESSION_STORE=off` 时，会话仅保存在 SDK 本地目录，迁移时须一并保留该目录（Docker 为 `claude_data/`，原生部署为 `CLAUDE_CONFIG_DIR` 或默认的 `~/.claude`）。同一会话在多个来源位置都有记录，或目标位置已有记录时，不会自动合并；移动失败会记录告警，服务仍会启动。

在「新增图片保存位置」中启用对象存储后，远端图片的实际内容仅保存在你的 TOS 存储桶中，数据根与数据库备份只包含其记录及可清理的本地缓存。恢复备份时，连接配置仍须指向原来的存储桶，桶内对象也需一并保留。由于 ArcReel 会清理不再使用的图片对象，备份后被删除的项目或版本，其对应对象可能已从桶中移除，恢复较早备份后这些图片将无法读取。如需支持恢复到较早时间点，请在停止写入期间一并备份桶内 `arcreel-materials/` 下的对象（或为存储桶开启版本控制），恢复时再放回原桶与原对象键，详见[新增图片保存到对象存储](../guide/asset-storage.md#remote-image-storage)。

备份前用 `docker compose config` 核对生效的配置，确认 ArcReel 实际连接的数据库和挂载的数据根。下文的 `tar` 示例使用默认的 `docker-compose.yml`。如果有自动加载的 override 文件，或通过 `COMPOSE_FILE`、`-f` 选用了其他文件，将实际使用的所有 Compose 文件加入配置归档，保留相对路径。恢复到新目录后，更新指向原目录的 `COMPOSE_FILE` 或 `-f` 路径，按原顺序加载解包后的文件。参见 [Docker Compose 多文件配置](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)。

### 6.1 SQLite 部署备份 {#backup-sqlite}

备份整个数据根，其中包括项目、默认 SQLite 数据库、日志和 Vertex 凭据。当前使用的 Compose 文件、`.env` 和 `claude_data/` 也要一并归档。

在当前 Compose 目录执行。首次部署默认位于 `deploy/`，使用同目录下的 `projects/`。先确认宿主机上的实际数据根目录，再停止写入。如果使用了 `ARCREEL_DATA_DIR` 与自定义挂载，请将 `data_dir` 设为该挂载对应的宿主机绝对路径：

```bash
data_dir="$(cd projects && pwd)"
# 自定义数据目录示例：data_dir="/srv/arcreel/projects"

docker compose stop arcreel
```

备份：

```bash
backup_stamp="$(date +%Y%m%d-%H%M%S)"
umask 077
mkdir -p backups
chmod 700 backups

tar -czf "backups/arcreel-config-${backup_stamp}.tar.gz" \
  docker-compose.yml .env claude_data

tar -czf "backups/arcreel-projects-${backup_stamp}.tar.gz" \
  -C "${data_dir}" .
```

服务停止后再归档整个 `data_dir`，可以让 `arcreel.db` 与项目资产保持在同一时点。配置归档与数据归档必须使用相同时间标签并配套保存；`umask 077` 与备份目录模式 `0700` 会限制其中凭据和项目资产的读取权限。不要在 ArcReel 写入时只复制 `arcreel.db`：WAL 模式下，已提交事务可能仍留在 `arcreel.db-wal` 中，丢失或错配 WAL 文件会造成数据丢失甚至损坏。如果无法停服，应使用 SQLite Online Backup API（例如 `sqlite3` 的 `.backup`）或 `VACUUM INTO` 生成一致快照，而不是直接 `cp` 主数据库文件。

恢复服务：

```bash
docker compose start arcreel
```

恢复时：

1. 停止 ArcReel，保留原部署目录供回滚。
2. 把配置归档解压到新部署目录。如果 Compose 配置里的自定义挂载还指向原部署的目录，先改到新位置。确认数据根挂载指向新的空目录后，把整份数据归档解压到这个空目录。
3. 在恢复目录执行 `docker compose up -d --wait arcreel`，检查 `/health`，打开项目验证图片、视频和版本历史。

### 6.2 PostgreSQL 部署备份 {#backup-postgresql}

下面的数据库命令仅适用于 ArcReel 连接到同一套 Compose 的 `postgres` 服务。连接其他 PostgreSQL 时，按[外部 PostgreSQL 的备份与恢复](#external-postgresql-backup-restore)操作。示例的数据库名和用户均为 `arcreel`，密码取 PostgreSQL 容器的 `POSTGRES_PASSWORD`。若修改了库名或用户，请按实际连接调整备份和恢复命令的 `-d`、`-U` 与认证配置。

在当前生产 Compose 目录执行。首次部署默认位于 `deploy/production/`。先停止 ArcReel 应用，同时保持 PostgreSQL 运行，以确保数据库和项目文件处于同一备份时间点。归档 Compose 配置中实际挂载的数据根。若使用自定义挂载，先将 `data_dir` 改成对应的宿主机绝对路径。

```bash
data_dir="$(pwd -P)/projects"
# 自定义数据目录示例：data_dir="/srv/arcreel/projects"
umask 077
mkdir -p backups
chmod 700 backups
docker compose stop arcreel

backup_stamp="$(date +%Y%m%d-%H%M%S)"

docker compose exec -T postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -h 127.0.0.1 -U arcreel -d arcreel' \
  > "backups/arcreel-db-${backup_stamp}.sql"

tar -czf "backups/arcreel-files-${backup_stamp}.tar.gz" \
  .env docker-compose.yml claude_data
tar -czf "backups/arcreel-projects-${backup_stamp}.tar.gz" \
  -C "${data_dir}" .

docker compose start arcreel
```

SQL、配置和数据根归档使用同一时间标签，须配套保存和恢复。数据根归档已包含日志与 Vertex 凭据。配置归档中的 `docker-compose.yml` 和 `.env` 会保留自定义的 `DATABASE_URL`。`umask 077` 与备份目录模式 `0700` 会让宿主机重定向生成的 SQL 文件和文件归档仅对当前用户可读写。

`pg_dump` 会使用 libpq 的 `PGPASSWORD`。上述命令只在 PostgreSQL 容器内的该次 `pg_dump` 进程中设置它，因此 `docker compose exec -T` 可以非交互执行，也不会把密码展开到宿主机命令行。长期的宿主机备份自动化应改用权限为 `0600` 的 PostgreSQL password file，不要把密码写进脚本或备份文件名。

如果 `tar` 报 `Permission denied`，说明挂载目录中存在由容器内 root 用户创建、宿主机当前用户不可读的文件。可用 `sudo` 重新执行对应的 `tar` 命令，并在完成后限制备份文件的读取权限。

### 6.3 PostgreSQL 恢复 {#restore-postgresql}

本节恢复同一套 Compose 管理的 `postgres` 服务。外部数据库见[外部 PostgreSQL 的备份与恢复](#external-postgresql-backup-restore)。

请使用[同一时间戳的归档和 SQL 备份](#backup-postgresql)。在当前生产 Compose 目录下执行，恢复至全新的相邻目录，以便保留原目录中的数据库与文件用于回滚。以下步骤适用于默认的相对挂载路径；若使用自定义挂载，请先修改备份配置，让数据根和 Agent 数据挂载到新部署目录中解包后的 `projects/` 和 `claude_data/`，并将 PostgreSQL 挂载到新部署目录下尚未创建的 `pgdata/`。若启用了图片对象存储，请提前准备好[同一备份时点的对象](#backup-and-restore)，确保恢复后的连接能正常读取原存储桶中的对应图片。

先将归档解压到空目录，核对恢复配置和凭证。文件归档已经包含 `projects/` 时，直接使用其中的数据根。替换命令中的时间戳，并在同一 shell 中完成本节各步。

```bash
set -euo pipefail

active_dir="$(pwd -P)"
backup_stamp=YYYYMMDD-HHMMSS
archive="${active_dir}/backups/arcreel-files-${backup_stamp}.tar.gz"
projects_archive="${active_dir}/backups/arcreel-projects-${backup_stamp}.tar.gz"
db_dump="${active_dir}/backups/arcreel-db-${backup_stamp}.sql"
test -s "${archive}" && test -s "${db_dump}" || exit 1

restore_dir="$(mktemp -d "$(dirname "${active_dir}")/arcreel-restored-XXXXXX")"
tar -xzf "${archive}" -C "${restore_dir}"
test -f "${restore_dir}/.env"
test -f "${restore_dir}/docker-compose.yml"
if [ ! -d "${restore_dir}/projects" ]; then
  mkdir "${restore_dir}/projects"
  tar -xzf "${projects_archive}" -C "${restore_dir}/projects"
fi
test -d "${restore_dir}/claude_data"
test ! -e "${restore_dir}/pgdata" && test ! -L "${restore_dir}/pgdata" || exit 1
```

在恢复目录运行 `docker compose config`，确认挂载都指向新位置，ArcReel 的 `DATABASE_URL` 也指向新部署的 `postgres` 服务。然后回到原部署目录停止原服务，再回到恢复目录，只启动 PostgreSQL。

```bash
cd "${active_dir}"
docker compose down
cd "${restore_dir}"
docker compose up -d postgres
docker compose ps postgres
```

等 PostgreSQL 健康检查通过后，把 SQL 导入这次新建的空数据库。如果使用自定义库名或用户，先在恢复实例中创建相应的空数据库和角色。导入在一个事务中完成，失败时会自动回滚。排除问题后可以重试，导入成功后再启动 ArcReel。

```bash
docker compose exec -T postgres psql -X --single-transaction -v ON_ERROR_STOP=1 -U arcreel -d arcreel -f - < "${db_dump}"
```

确认数据库和对象存储都已就绪后，按恢复的配置创建 ArcReel 容器。登录抽查项目、图片、视频、版本历史与关键会话。

```bash
docker compose up -d --wait arcreel
curl -f http://localhost:1241/health
```

验证通过后，后续请在恢复目录执行 Compose 和备份命令。如需回滚，先在恢复目录执行 `docker compose down`，再回到原目录执行 `docker compose up -d`。

### 6.4 外部 PostgreSQL 的备份与恢复 {#external-postgresql-backup-restore}

1. 停止 ArcReel 写入。根据生效的 `DATABASE_URL` 确认源库，用托管服务的备份工具或 [`pg_dump`](https://www.postgresql.org/docs/18/app-pgdump.html) 备份该库。按上文归档相同时间点的配置和数据根，无需执行 Compose 内的 `pg_dump`。文件和数据库备份完成后再恢复原服务。
2. 恢复时将配置与数据根解包到新位置，并将恢复配置中的宿主机挂载改到新路径。使用托管服务的恢复工具，或按 [PostgreSQL 的恢复说明](https://www.postgresql.org/docs/18/backup-dump.html#BACKUP-DUMP-RESTORE)将 SQL 导入独立的空数据库。保留原库用于回滚。
3. 更新恢复部署中的 `DATABASE_URL`，用 `docker compose config` 确认它指向恢复后的库。核对挂载与对象存储。如果新旧部署使用同一宿主机端口，先在原部署目录执行 `docker compose stop arcreel`。再在恢复目录启动 ArcReel，检查 `/health` 和项目内容。

如需回滚，先在恢复目录执行 `docker compose stop arcreel`，再在原部署目录执行 `docker compose up -d arcreel`。

## 7. 反向代理与 HTTPS {#reverse-proxy-and-https}

ArcReel 当前不支持直接暴露到公网。私有远程部署必须启用认证，并通过 TLS、VPN 或安全隧道保护传输。不要把 `1241` 端口直接发布到不受信任的网络。建议：

- 使用 Nginx、Caddy、Traefik 或云负载均衡器；
- 配置 HTTPS；
- 只允许代理服务器访问 ArcReel 容器端口；
- 保留 SSE 长连接；
- 设置足够的上传大小和读取超时。

官方 Compose 文件默认使用 `1241:1241`，会将后端端口发布到宿主机的所有网络接口；仅添加反向代理并不会关闭这条直连路径。当反向代理运行在同一宿主机时，启动前请将 `arcreel` 服务的端口映射改为仅监听 loopback：

```yaml
ports:
  - "127.0.0.1:1241:1241"
```

如果反向代理运行在容器网络或其他主机上，应取消不必要的宿主机端口发布，并通过容器网络、主机防火墙或等效网络策略保证只有代理能够访问 ArcReel 后端。

ArcReel 自身已处理缓存头和压缩，反向代理无需另行配置：

- 带版本键（`?v=`、`?fp=`）或指向 `versions/` 版本快照的媒体地址，以及前端 `/assets/` 下带内容哈希的构建产物，返回 `Cache-Control: public, max-age=31536000, immutable`，浏览器直接复用本地缓存；
- 不带版本键的媒体地址返回 `Cache-Control: no-cache`，浏览器每次使用前向服务端再验证，文件未变化时返回 `304`；
- 页面入口 HTML 返回 `no-store`，升级后浏览器会加载新版前端；
- JSON、JS、CSS、HTML 等文本响应使用 gzip 压缩，SSE 事件流和图片、音视频不压缩。

反向代理应原样透传这些响应头，不要用 `expires`、`add_header Cache-Control` 或 `proxy_hide_header` 覆盖。

Nginx 示例。示例把媒体文件路径单独拆出：SSE 与普通 API 关闭代理缓冲，事件实时到达浏览器；`/api/v1/files/` 与 `/api/v1/global-assets/` 开启代理缓冲，图片和视频由 Nginx 先行接收，后端连接尽早释放。

```nginx
server {
    listen 443 ssl http2;
    server_name arcreel.example.com;

    ssl_certificate /etc/letsencrypt/live/arcreel.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/arcreel.example.com/privkey.pem;

    client_max_body_size 2g;

    # 只压缩文本类型。ArcReel 已压缩的响应 Nginx 不会重复压缩；
    # 不要加入 text/event-stream，压缩缓冲会延迟 SSE 事件；图片和音视频本身已压缩，也不要加入
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/markdown application/json application/javascript image/svg+xml;

    # 项目媒体与全局资产：只读 GET，开启缓冲
    location ~ ^/api/v1/(files|global-assets)/ {
        proxy_pass http://127.0.0.1:1241;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_buffering on;
    }

    location / {
        proxy_pass http://127.0.0.1:1241;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # ArcReel 使用 SSE 推送 Agent 回复和项目事件
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

证书配置取决于你的基础设施，可以使用 ACME/Let's Encrypt 或云平台托管证书。

## 8. 容器权限与 Agent 沙箱 {#container-permissions-and-sandbox}

ArcReel 在 Linux 和 macOS 启动时会严格检查 Agent 沙箱，所需工具缺失或不可用时会拒绝启动。Windows 原生环境没有 `bwrap`，会自动降级为受限的 Bash 命令白名单；该模式只保证项目创建与基础流程，生产部署建议使用 WSL2 或 Docker Desktop。

| 环境 | 工具 | 安装 |
|---|---|---|
| macOS | `sandbox-exec` | 系统自带，无需额外安装 |
| Linux 本地开发 | `bwrap` + `socat` | Ubuntu/Debian：`sudo apt install bubblewrap socat`；Fedora：`sudo dnf install bubblewrap socat`；Arch：`sudo pacman -S bubblewrap socat` |
| Docker | `bwrap` + `socat` | 官方镜像已包含 |
| Windows 原生 | 无 `bwrap` 沙箱 | 自动降级为 Bash 命令白名单；推荐 WSL2 / Docker Desktop |

官方 Compose 为 Agent Bash 沙箱配置了：

- `seccomp:unconfined`
- `apparmor:unconfined`
- `NET_ADMIN`

这些设置用于支持容器中的 `bwrap` 隔离和嵌套网络命名空间，但也意味着容器获得了比普通 Web 应用更高的权限。

生产部署建议：

- 使用专用主机或至少使用隔离良好的运行环境；
- 不把 Docker Socket 挂载到容器；
- 不额外挂载不必要的宿主机目录；
- 限制管理页面访问范围；
- 及时更新 ArcReel 和基础镜像；
- 只为 Agent 配置必要的网络和文件访问权限；
- 对未知来源的项目输入保持谨慎。

虽然 Docker 镜像已内置 `bwrap` 和 `socat`，但宿主机的 user namespace 或 AppArmor 策略仍可能阻止沙箱启动。若启动失败，请根据服务输出的 `SANDBOX_*` 诊断信息逐项修复，切勿改为特权模式绕过检查，也不要在不了解后果的情况下删去官方 Compose 中的沙箱配置。

## 9. 监控建议 {#monitoring}

最低限度应监控：

- `/health` 是否可用；
- 容器是否频繁重启；
- 磁盘剩余空间；
- `projects/` 增长速度；
- PostgreSQL 数据目录大小；
- 任务失败率；
- 供应商限流和额度不足；
- 备份最近成功时间。

媒体资产增长通常快于数据库，应优先为项目目录设置容量告警。

## 10. 常见故障 {#troubleshooting}

### 服务无法启动 {#service-wont-start}

```bash
docker compose ps
docker compose logs --tail=300 arcreel
```

检查：

- `.env` 是否存在；
- 端口 `1241` 是否被占用；
- 镜像是否成功拉取；
- 挂载目录是否可写；
- 生产部署是否设置 `POSTGRES_PASSWORD`。

### 健康检查失败 {#health-check-fails}

```bash
curl -v http://localhost:1241/health
docker compose logs --tail=300 arcreel
```

如果容器刚启动，先确认是否仍在执行数据库迁移。

### 无法登录 {#cannot-log-in}

- 检查 `AUTH_USERNAME`；
- 检查 `.env` 中的 `AUTH_PASSWORD`；
- 如果首次启动时密码留空，查看是否已被回写；
- 修改 `.env` 后是否执行过 `docker compose up -d`，启动日志里是否有进程环境变量与 `.env` 不一致的 WARNING；
- 修改 `AUTH_TOKEN_SECRET` 后需要重新登录。

### Agent 请求失败 {#agent-request-fails}

- 验证 Agent 凭据；
- 检查 Base URL 和模型名称；
- 检查网络和代理；
- 查看供应商是否限流；
- 使用少量内容验证，不要一上来就用完整小说跑通全流程。

### 任务一直排队 {#tasks-stuck-in-queue}

- 查看图像、视频和音频并发设置；
- 检查是否有长时间停留在运行中的异常任务；
- 查看供应商 RPM 配额；
- 检查前序任务是否尚未完成。

### 磁盘快速增长 {#disk-growth}

在 Compose 目录中重点检查数据根各部分的占用：

```bash
du -sh projects/*
find projects/projects -type f -size +500M
```

不要直接删除当前项目仍在引用的文件，建议优先通过项目归档、清理无用项目以及只保留必要版本来控制磁盘占用。

## 11. 上线检查清单 {#go-live-checklist}

- [ ] 使用 PostgreSQL；
- [ ] 固定 Release 镜像版本；
- [ ] 设置强 `AUTH_PASSWORD`；
- [ ] 设置固定 `AUTH_TOKEN_SECRET`；
- [ ] 配置 HTTPS；
- [ ] 不直接暴露 `1241`；
- [ ] 验证 SSE 可正常工作；
- [ ] 备份数据库和数据根；
- [ ] 完成一次恢复演练；
- [ ] 配置磁盘和健康检查告警；
- [ ] 确认模型 API Key 不出现在日志和仓库；
- [ ] 阅读许可证和 `NOTICE`。
