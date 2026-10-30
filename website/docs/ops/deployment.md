---
id: deployment
title: 部署与运维
sidebar_position: 1
update_docs: fact-check
---

# 部署与运维 {#deployment}

本文档说明 ArcReel 的默认部署、PostgreSQL 生产部署、环境变量、数据持久化、升级、备份、恢复、反向代理和故障排查。正式支持边界见 [安全政策](https://github.com/ArcReel/ArcReel/blob/main/SECURITY.md)，完整信任边界见 [安全威胁模型](https://github.com/ArcReel/ArcReel/blob/main/docs/security/threat-model.md)。

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

`deploy/projects/` 是 ArcReel 的数据根（`ARCREEL_DATA_DIR`，容器内为 `/app/projects`）。项目收在其中的 `projects/` 子目录里，其余运行数据与之并列：

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

只有 `projects/` 下名字由英文字母、数字或中划线组成、并且包含 `project.json` 的目录才算项目，数据根里的其它条目不会出现在项目列表中。设置页「关于」中下载的诊断日志会列出数据根及上述各类数据的实际位置。

> 备份时归档整个数据根，不要只备份数据库而忽略项目文件。数据库保存任务、配置和索引信息，项目目录保存原始素材和生成文件，两者需要保持一致。

项目回收站存放在 `projects/<项目名>/.arcreel/trash/`，完整数据根备份会保留其中的删除记录，项目 ZIP 导出不包含它。删除记录按删除时间保留 30 天，应用启动时和运行期间定期清理；恢复备份不会重置记录的保留期。

删除的项目整体移到 `deleted_projects/<项目名>#deleted-<删除时间>/`，连同它的项目回收站，同样保留 30 天、按同样的方式清理。在项目列表页的「已删除项目」中可以恢复或彻底删除；恢复时原名已被占用，项目会以加后缀的新名字恢复。

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

默认 Compose 会把 `POSTGRES_PASSWORD` 的原始值交给 PostgreSQL，同时把它拼入 `DATABASE_URL` 的密码段。如果密码含有 `@`、`:`、`/`、`?`、`#`、`%` 等 URL 保留字符，连接 URI 中的密码必须做百分号编码。不要把编码后的值直接填入 `POSTGRES_PASSWORD`：PostgreSQL 需要原始密码，只有 URI 需要编码。

当必须使用特殊字符时，在 `.env` 中分开保存原始值与编码值：

```dotenv
POSTGRES_PASSWORD='p@ss/word'
POSTGRES_PASSWORD_URLENCODED=p%40ss%2Fword
```

然后把 `deploy/production/docker-compose.yml` 中 `DATABASE_URL` 的密码部分改为 `${POSTGRES_PASSWORD_URLENCODED}`；PostgreSQL 容器的 `POSTGRES_PASSWORD` 仍保持不变。可用 `urllib.parse.quote(raw_password, safe="")` 生成编码值。如果不想维护这项 Compose 改动，就使用上述十六进制密码。

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

`pgdata/` 只保存 PostgreSQL 集群数据，`projects/` 是数据根，保存项目元数据、媒体资产和其余运行数据，两者都必须持久化且配套备份。生产部署通过 `DATABASE_URL` 使用 PostgreSQL，不会使用 `deploy/production/projects/arcreel.db`；不要把 SQLite 文件复制进 `pgdata/`，也不要把两个目录当成可相互替代的数据库备份。

### 2.3 数据库迁移 {#database-migrations}

ArcReel 在应用启动时运行 Alembic 迁移，将数据库结构升级到当前版本。

启动时还会将可确认归属项目的历史调用记录产物路径改为项目内相对路径，以便移动数据目录后费用明细仍能显示缩略图；无法确认项目根的旧路径会保留并写入启动日志。排队中的旧文本任务按启动时的当前数据目录执行，载荷里的旧目录不再生效。

升级前仍然必须备份。自动迁移解决的是结构升级，不代替可回滚的数据备份。

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

- `AUTH_TOKEN_SECRET` 变化后，现有登录 Token 会失效。显式设置时以设置值为准，数据根下自动生成的密钥文件不再使用。未显式设置时，删除密钥文件并重启会生成新密钥，使现有登录失效。
- 修改 `.env` 后，在 Compose 目录执行 `docker compose up -d` 重建容器。`docker compose restart` 与容器自动拉起沿用创建容器时的环境变量，不会重新载入 `.env`；启动时发现进程环境变量与挂载的 `.env` 取值不一致，日志会输出一条 WARNING，只列出变量名。
- `AUTH_ENABLED=false` 仅适用于受独立网络边界保护的本机环境。Compose 默认把 `1241` 端口发布到宿主机所有网卡，远程部署不得关闭认证。认证关闭时，启动日志会输出 WARNING。
- `.env` 中可能包含密钥，不要提交到版本库。
- Vertex 凭据文件应只授予运行 ArcReel 的用户读取权限。
- 文件日志固定写入数据根下的 `logs/`。旧变量 `ARCREEL_LOG_DIR` 已不再生效，仍设置时启动日志会提示一次；只需要 stdout 日志时设置 `ARCREEL_LOG_FILE_DISABLED=true`。
- 第三方模型 API Key 通常在 ArcReel 设置页中管理，不要写入公开文档。

远程 MCP 端点为 `/mcp`，始终要求 `arc-` 前缀 API Key；即使 `AUTH_ENABLED=false` 也不会匿名放行。外部接入不需要额外的 `MCP_*` 配置：把设置页「外部 Agent 接入」给出的端点地址，与在「访问令牌」创建的 API Key 填进客户端即可，通过保留 SSE 长连接的 HTTPS 反向代理、VPN 或安全隧道访问。

服务端不校验请求的 `Host` 头，端点边界由每请求强制的 API Key 承担；域名归属交给部署形态，请在反向代理上限定 `server_name`（Nginx）或等价规则，只把预期域名的请求转发给 ArcReel。

`CORS_ORIGINS` 保持默认通配时，浏览器型 MCP 客户端同样无需配置；一旦收紧为白名单，就要把该客户端的 Origin 也列进去——这一个白名单同时约束应用 API 与 MCP 端点，不存在第二份 MCP 专用清单。

远程 MCP 不提供 OAuth 授权，也不发布 OAuth 受保护资源元数据（RFC 9728）；只支持 OAuth 自动发现、无法自行填写 Bearer 凭证的客户端无法接入。

ArcReel 的沙箱要求父进程环境中不保留供应商密钥。以下凭据环境变量存在非空值时，服务会拒绝启动并提示迁移到 WebUI 设置页：

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

### 5.2 默认部署升级 {#upgrade-sqlite-deployment}

从数据根布局调整之前的版本升级时，先沿用原来的 Compose 文件执行下面的命令，再按[数据根布局迁移](#data-root-layout-migration)切到单卷 Compose。

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

从数据根布局调整之前的版本升级时，同样先沿用原来的 Compose 文件，再按[数据根布局迁移](#data-root-layout-migration)切到单卷 Compose。

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

从项目、数据库等平铺在数据根下的旧版本升级时，首次启动会自动把数据根整理成[持久化目录](#sqlite-volumes)一节所示的布局。数据根仍是原来配置和挂载的那个目录，不需要修改环境变量：

- 项目目录搬进数据根下的 `projects/`。名字合法但没有 `project.json` 的目录也原样搬入，只是不显示为项目；与 `logs`、`users`、`runtime` 等系统目录同名的目录除外，它们留在原位。名为 `projects` 的项目搬入后名字不变。
- `_global_assets/` 改名为 `global_assets/`，`.arcreel/users/` 搬到 `users/`，迁移标记等运行时状态收进 `runtime/`。
- 数据根上一级 `vertex_keys/`（Docker 中为 `./vertex_keys` 卷）里的凭据并入数据根下的 `vertex_keys/`：凭据记录对应的文件按凭据编号保存为 `vertex_cred_<凭据编号>.json`，其余 `.json` 文件保持原名。
- 已有的 Agent 会话、全局资产、Vertex 凭据、费用记录和排队中的任务随之改写，升级后照常可用。

这些步骤逐步执行，每一步都可以安全重跑：容器被杀或进程崩溃后，下次启动会接着完成。全部完成后在 `runtime/` 写入完成标记，之后每次启动只检查这个标记。某一步失败时，启动日志记录 ERROR，服务停止启动，不会在半迁移的布局上继续运行；按日志处理后重启即可续跑。例如数据根的 `projects/` 下已有与待搬项目同名的目录时，迁移会停下，需要人工确认保留哪一份。全局资产和用户记忆在新位置已有同名文件时不会停下：旧文件留在原位置，启动日志记录告警。

另有两项不受完成标记控制，每次启动都会检查：

- 默认 SQLite 数据库 `.arcreel.db`（连同 `-wal` / `-shm`）改名为 `arcreel.db`。这一步在解析数据库地址时完成，先启动应用还是先运行 Alembic 都会连到原来的库。新旧两个文件同时存在时不改名，继续使用 `arcreel.db`，启动日志记录告警。设置了 `DATABASE_URL`（含 PostgreSQL）时不动数据库。
- 原先代码目录下的日志目录（Docker 中为 `./logs` 卷）里的文件并入数据根下的 `logs/`，新位置已有同名文件时旧文件留在原处。这一步出错只记录日志、不阻止启动。`ARCREEL_LOG_DIR` 指向的自定义日志目录不会搬动。

**升级前备份整个数据根**，连同数据根外的旧日志目录与旧凭据目录（Docker 中为 `./logs`、`./vertex_keys` 卷，其余部署为代码目录下的 `logs/` 与数据根上一级的 `vertex_keys/`）；外部数据库另行备份。迁移会把这两处的文件搬进数据根。旧版本会把 `projects/` 当成一个项目，也不认识新的数据库文件名；降级需要恢复这份备份。

新版 Compose 只挂载一个数据卷 `./projects`，外加 `.env` 和 `claude_data/`，不再挂载 `./logs` 与 `./vertex_keys`。旧卷里的文件只有在启动时仍挂着才会并入数据根，其中 Vertex 凭据只在布局迁移那一次搬入，写下完成标记后不再处理。因此 Docker 部署从旧版本升级分两个阶段（以下以 `deploy/` 为例，生产部署换成 `deploy/production/`）：

1. **沿用原来的 Compose 升级（零配置）。** 不改 Compose 文件，按[默认部署升级](#upgrade-sqlite-deployment)或[生产部署升级](#upgrade-postgresql-deployment)执行 `docker compose pull` 与 `docker compose up -d`。此时两个旧卷仍挂着，首次启动会把其中的文件并入数据根。在启动日志中确认出现「数据根布局迁移完成」，并确认原来的日志和凭据文件已出现在 `deploy/projects/logs/`、`deploy/projects/vertex_keys/` 下（凭据文件名可能已改为 `vertex_cred_<凭据编号>.json`）。
2. **之后再切到单卷 Compose。** 删去 Compose 中 `./logs` 与 `./vertex_keys` 两行卷（或换用新版 Compose 文件），再执行 `docker compose up -d`。之后 `deploy/logs/`、`deploy/vertex_keys/` 可以删除；如果其中还有文件（例如数据根里已有同名文件，或旧卷是只读挂载），先确认不再需要。

如果已经换成新版 Compose、但还没有用新版本启动过，首次启动前把这两行卷临时加回去，再按上面两个阶段操作。

如果已经换成新版 Compose 并用新版本启动过，旧卷里的文件不会再自动并入，需要手工补齐。旧卷里的文件由容器以 root 写入，必要时在命令前加 `sudo`：

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

确认设置页中的 Vertex 凭据可以正常使用后，再删除 `deploy/logs/`、`deploy/vertex_keys/`。旧目录里不是 `vertex_cred_<凭据编号>.json` 这种名字的凭据文件不会被读取，对应凭据需要在设置页重新上传。

### 5.5 项目结构迁移 {#project-schema-migrations}

应用启动时除数据库迁移外，也会逐个升级数据根 `projects/` 目录下的项目结构。只有名字由英文字母、数字或中划线组成，且包含 `project.json` 的目录会被视为项目并参与迁移及过期备份回收。升级到启用产物状态记录的版本时，ArcReel 会先完整校验项目和正式剧本，再一次性写入项目的产物记录，最后才更新 `project.json` 的 schema 版本。

迁移提交前会在各文件旁创建带 `.bak.v<起点版本>-<时间戳>` 后缀的备份，覆盖：

- `project.json`；
- `project.json` 登记的正式剧本文件；
- 已存在的 `.arcreel_artifacts.json`；
- 改写版本记录的迁移另备份 `versions/versions.json`。
- 改写剪辑时间线的迁移另备份被改写的 `edit_timelines/episode_*/*.json`。

迁移可安全重试：如果上次启动在备份或提交中断，下一次启动会重新校验，并确保至少有一份与迁移前内容完全一致的备份后再继续；内容相同的备份只保留一份，反复失败不会堆出多份。自动生成的这些项目级备份只用于迁移恢复，不能代替数据库与整个数据根的部署级备份。

迁移完成后会在项目目录写入 `.migration_report.json`，只记两组内容：「已调整」列出迁移改了什么（旧形态规整时的取舍、补上的占位定义、修复的结构），「未能带入」列出原地保留、但没有登记的文件（读不出的文件、没有合成设置记录的旧版旁白音频）。生成缺口、需要重新规划的单位，以及依据算不出而没有登记的产物（例如源文不是 UTF-8 时的脚本规划）不进报告。报告只作说明，不阻断任何操作。报告非空时，项目页顶栏会出现一次「本次升级调整了 N 项内容」的提示，点开是只读报告；关闭后不再出现。制作状态接口的 `migration_report` 字段只在报告非空、尚未关闭时给出。

归档导入同样走这条迁移链，并且不论导入包是哪个版本导出的，导入时读不出的文件也写进这份报告。报告非空时，项目出现在项目列表之前会弹出「导入前请确认」，内容就是这份报告；选「取消」不留下任何项目数据，选「确认导入」才完成导入，项目页不再重复提示同一份报告。报告为空时直接导入；当前版本导出的正常项目再导入，报告就是空的。导入时迁移链出错，导入会带着具体原因失败，导入包里的数据不会留下。

迁移只因以下数据问题拒绝整个项目：`project.json` 或剧本读不出（不是合法 JSON，或缺了创作类型这类决定其形态的内容）；版本记录 `versions/versions.json` 或产物清单 `.arcreel_artifacts.json` 读不出；剧本里的条目结构损坏（例如广告旧镜头索引或参考生视频单元列表里有不是对象的条目）；导入包本身不安全。迁移本身没能做完（磁盘空间不足、写入失败）照旧阻断，项目保持升级前的状态，排除故障后重试即可。其余数据问题按条目处理，原有内容不会丢弃：

- 过去正常流程写出的旧形态由迁移自动规整：同一集在集列表里登记了多次时只留一条；不同的集绑到同一份剧本时，剧本留给它自己记录的那一集，其余各集改用自己的剧本路径；剧本没有记录所属的集、或记录的集与绑定不符时，以集列表为准写回；集的剧本绑定不是 `scripts/episode_N.json` 时，剧本挪到这个规范路径并改绑，版本记录与宫格记录里的引用随之改写；新旧两个文件名的脚本规划草稿并存时以新文件名为准；同一类资产里名字去掉首尾空白、统一 Unicode 形式后相同（不区分大小写）的只留第一条，去掉的那条名下的衍生并到保留的那条名下（与已有衍生只差大小写时并入已有的那个），剧本与脚本规划里写成只差大小写那个名字的引用改为保留的名字；剧本里缺 ID 或 ID 重复的条目补上新 ID，重复的从第二条起换号，原 ID 的媒体文件留给保留原 ID 的那一条；两集里有同 ID 的条目时，媒体留给 ID 前缀所指的那一集（对不上时留给集列表里靠前的那一集），另一集的条目换新 ID；广告旧镜头索引里重复的镜头或单元编号同样换号。
- 参考生视频单元不符合当前单元格式时（例如旧版本允许的、正文为空的单元），单元原样保留并标为需要重新规划，在界面上与其他需要重新规划的单元一样处理，不进报告。
- 资产图、参考图、参考音频或商品原图指向其他现存资产名下的文件（含衍生图）时，迁移会复制一份到本资产名下并改写引用，原文件留给原资产。复制会增加磁盘占用；本资产名下的落点已有不同内容时，原有文件先移为保留副本。资产图的制作状态不因复制改变：改写前是最新的仍是最新，改写前已失效的仍需重新生成。复制落点、引用与资产图登记在同一事务内提交，失败时恢复原状。源路径含符号链接时不复制；目标目录是符号链接时，迁移会失败，需先还原为项目内的实际目录再重试；目标文件是符号链接时，只替换链接本身，不改动链接指向的文件。复制与保留副本的位置列入「已调整」。
- 规整需要取舍时，另一份留作保留副本，文件名带 `.kept.v<起点版本>-<时间戳>` 后缀，放在原文件旁。保留副本不会被自动清理，报告的「已调整」里写明它的位置。
- 单个宫格记录、单份脚本规划草稿或单个媒体文件读不出时，文件原样留在原处、不登记，列入「未能带入」，其余内容照常迁移。归档里的剧本引用了未定义的角色、场景或道具时，导入会补一条空白定义并列入「已调整」。

早于产物记录机制生成的视频（旧版本记录没有类型化来源字段）由迁移按当时的项目状态补写来源，升级后照常显示与预览；无法补写来源的视频不登记、按缺失显示，不进迁移报告。

视频生成与旁白时长解耦后，曾因旁白较长而申请更高时长档位的视频，只要升级前为最新，升级后仍显示为最新，预览和版本还原保持一致。迁移保留视频文件、实际付费时长与请求记录，仅改写时效基准；升级前已过期的视频仍为过期。此前报告中的跳过项继续保留。

升级到 schema 18 时，迁移会为已有的视频与旁白配音补录内容指纹：正式文件与当前选中版本的快照各读一遍，两者一致才记下。之后只要文件的字节数和修改时间没变，打开项目判定这些产物是否最新时就不再读文件内容。视频多、文件大的项目在这一步要多花一些时间，首次启动的迁移耗时会相应变长。正式文件与快照对不上（例如在 ArcReel 之外替换过视频）或读不出的产物不补录，显示结果与升级前相同。

升级到 schema 19 时，分镜图、宫格图与宫格切出的分镜图不再按引用资产的名字判断是否最新：发给图像模型的是「图N」编号，不含资产名，因此只给资产改名不会再让它们显示为已过期。迁移会把升级前为最新的这些产物连同版本记录一并改写到新口径，升级后仍为最新；升级前已过期的仍为过期。升级前删进项目回收站、升级后才恢复的分镜图或宫格图，如果引用了资产，恢复后显示为已过期，按需重新生成即可。参考生视频与资产图的生成输入含资产名，改名后照旧显示为已过期。

`project.json` 里没有画面比例字段的剧情演绎项目（很早期版本创建或导入的项目），分镜图按剧情模式的缺省比例 16:9 判断是否最新，与实际生成时一致。这类项目此前按 9:16 登记的分镜图，升级后会显示为已过期；按需重新生成即可。

用户上传的分镜图与视频，当前选中的版本仍为手动上传、且文件与上传快照字节一致时，升级会按上传字节补登或改写产物记录，显示为最新；提示词为空或引用资产缺图也不影响登记。之后改提示词、资产或画风不会让它们过期，批量补缺会保留它们。已改选生成版本或文件与上传快照不一致的产物不按上传口径补登。

生成的分镜图里引用的资产如果已删除或改名，或者引用的角色、场景、道具没有可登记的资产图（从未生成、文件丢失，或描述为空），这张分镜图照常登记、升级后显示为最新，缺的引用记在它的产物记录里；但重新生成该分镜图会被拒绝，直到补齐。商品资产图是可选的：未声明资产图时，可只用原图；也未声明原图时，可只用文字。已声明的商品资产图不可用，或已声明的商品原图读不到，处理方式相同。补齐资产登记或资产图、重新上传缺失原图或清掉失效的原图字段后，这张分镜图显示为已过期，按需重新生成即可。

资产图与衍生资产图也按同一口径登记：角色或商品声明了原图但原图读不到，或资产描述为空，资产图照常登记、升级后显示为最新；衍生的本体资产图有缺口时，衍生资产图同样照常登记。重新生成会被拒绝，直到重新上传原图或清掉失效的原图字段、补上描述；补齐后这张资产图显示为已过期。原图丢失的角色与商品同样不能生成资产图，处理方式相同。

早于这一版本的升级曾把上面两类分镜图与资产图，以及分镜提示词待补、分镜图不可用、所在单位同时写了旁白与台词的视频，列为缺失并写进迁移报告。升级到这一版本时，迁移会补登这些产物，显示为最新，并把它们从迁移报告的跳过列表中移除；已登记的产物记录一个字节都不改。补登让前一张分镜图变得可用时，紧随其后的分镜图会显示为已过期，按需重新生成即可。同时写了旁白与台词的单位仍须重新规划，重新生成视频会被拒绝。

升级到「内容确认即转出正式脚本」的版本时，迁移按各集的状态收编：

- 脚本规划已确认、但还没有正式脚本的集，按确认过的脚本规划转出正式脚本，全部分镜标为待编写提示词；转不出来的集不生成正式脚本，重新确认脚本规划即可。
- 已有正式脚本的集保留原有内容；视觉提示词为空的分镜标为待编写，剧情演绎分镜缺的视觉改编描述与参考生视频单元缺的对应原文从脚本规划补齐。
- 已有正式脚本、但从未记录过内容确认的集，以当前脚本规划作为已确认的基线；此后重跑脚本规划只会让该集回到待确认，不影响已有正式脚本继续制作。
- 集的剧本绑定统一为逐字的 `scripts/episode_N.json`。旧版本写出的其他绑定（例如 `scripts/custom.json`，或 `episode_N.json`、`./scripts/episode_N.json`、`scripts\episode_N.json` 这类字面不同的写法）与同一集号的重复条目，由迁移按前文的规则规整，不再让项目迁移失败。

升级到转场移出脚本的版本时，剧本分镜与视频单元上的转场设置被删除，淡入淡出、叠化等取值不保留；导出的剪映草稿在此之后暂时不带转场。`presentations/` 下已生成的成片描述随之去掉转场，升级前最新的成片描述与字幕在升级后仍是最新。成片描述可由预览重新生成，这一步不为它们另做备份。

同一次升级里，旁白交付方式成为项目配置：已经生成并登记过 TTS 旁白配音的项目判为「TTS 配音」，最近一次生成旁白配音所用的 TTS 模型、音色和语速写进项目设置；其余项目判为「后期配音」。此后项目不再跟随全局默认的 TTS 设置，需要调整时在项目设置里修改。

同一次升级里，集的内部编号与播出顺序分离：界面、通知和导出的剪映草稿按集在列表中的位置与标题称呼集，不再显示集号。迁移会记下项目用过的最大集号，范围包括现有的集，以及重置或重新规划后仍留在草稿、源文留底、媒体文件、版本历史、宫格记录、任务记录及调用记录里的旧集号（包括生成输入引用的文件路径）；之后新建的集从这个号之后编号，不会与这些残留重名。现有集的排列顺序不变。脚本规划参考的「下一集大纲」改为取列表中紧接的那一集；升级前为最新的脚本规划，升级后仍显示为最新，升级前已过期的仍为过期。

同一次升级里，集原文与整本源文文件改由项目显式登记：

- 每一集记下本集原文的来源：切自整本源文、自带原文或没有原文。`source/` 下有 `episode_N.txt`、集列表里却没有这一集的，迁移后作为一集自带原文的集接在列表末尾。
- 整本源文的文件清单按原来的文件名顺序补记一次，已切出的集与下一批分集规划的起点保持不变；此后新上传的文件接在清单末尾。
- 有集切自其中的整本源文文件，会在 `source/snapshots/` 下留一份规范化文本快照。
- 升级后直接放进 `source/`、没有经上传登记的文件不算源文，不参与分集规划，也不改变任何一集的原文。这类文件列在「分集」视图的「有 N 个文件还没用上」里，可以逐个加入整本源文、作为一集的原文，或者删除。

同一次升级里，剧情演绎项目的源文件类型（小说或剧本）从项目设置移到每份原文上：迁移按项目原来的类型，给整本源文的每个文件和每一集自带原文的集记上同一种类型；其他创作类型删去这项设置。之后，整本源文的文件在「分集」视图的文件条上逐个修改，自带原文的集在集页编辑原文时修改。剧情演绎分镜图生视频的脚本规划在升级后仍是最新；参考生视频此前不区分类型、一律按小说规划，剧本项目里这类脚本规划在升级后显示为过期，正式脚本不受影响。

有一类迁移需要先在项目目录旁复制一份完整的项目副本，改写完成后再整目录替换。它对磁盘的要求与善后：

- 迁移开始前会核对可用空间，不足以容纳副本时该项目以「磁盘空间不足」失败，项目目录不被改动；清理磁盘后重启即可继续。
- 替换过程中进程被强制终止（断电、`kill -9`、容器被 OOM 杀掉）时，项目目录可能缺失，项目从列表中消失，同级留下以 `.<项目名>.v6-` 开头的隐藏目录。ArcReel 不会自动恢复这种情况。这些隐藏目录是项目数据，不要手工删除，也不要急着重建同名项目。
- 人工恢复：停止服务，把以 `.<项目名>.v6-rollback-` 开头的目录改名为 `<项目名>`（有多个时取修改时间最新的那个），再启动服务。它是迁移前的原项目，启动后迁移会重新执行。
- 项目目录在位时，这些隐藏目录按与上述备份相同的保留期自动清理；项目目录缺失期间不会清理。

如果某个项目迁移失败，先保留现场并查看启动日志，不要手工修改 schema 版本、删除备份文件或保留副本。修复损坏的项目文件或权限后再重启服务。

### 5.6 固定版本 {#pin-version}

`latest` 适合快速体验，但生产环境更适合固定 Release 标签。

将 Compose 中的镜像改为：

```yaml
image: arcreel/arcreel:X.Y.Z
```

升级时显式修改版本，可以降低无意中拉取新版本的风险。

## 6. 备份与恢复 {#backup-and-restore}

迁移整个站点到不同的绝对路径后，应用启动时会按当前项目目录调整已有 Agent 会话的存储位置，保留续聊上下文；已删除项目的会话也会随其恢复到的新名称调整。使用 `ARCREEL_SDK_SESSION_STORE=off` 时，会话仅保存在 SDK 本地目录，迁移时须一并保留该目录（Docker 为 `claude_data/`，原生部署为 `CLAUDE_CONFIG_DIR` 或默认的 `~/.claude`）。同一会话在多个来源位置都有记录，或目标位置已有记录时，不会自动合并；移动失败会记录告警，服务仍会启动。

### 6.1 SQLite 部署备份 {#backup-sqlite}

数据根已经包含项目、默认 SQLite 数据库、日志和 Vertex 凭据，文件系统备份只需归档数据根，外加 `.env` 与 `claude_data/`。

先确认宿主机上的实际数据根目录，再停止写入。默认 Compose 使用 `deploy/projects/`；如果通过 `ARCREEL_DATA_DIR` 和自定义挂载改变了容器内路径，请把 `data_dir` 改为该挂载对应的宿主机绝对路径：

```bash
cd deploy

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
  .env claude_data

tar -czf "backups/arcreel-projects-${backup_stamp}.tar.gz" \
  -C "${data_dir}" .
```

服务停止后再归档整个 `data_dir`，可以让 `arcreel.db` 与项目资产保持在同一时点。配置归档与数据归档必须使用相同时间标签并配套保存；`umask 077` 与备份目录模式 `0700` 会限制其中凭据和项目资产的读取权限。不要在 ArcReel 写入时只复制 `arcreel.db`：WAL 模式下，已提交交易可能仍在 `arcreel.db-wal` 中，丢失或错配 WAL 文件会造成数据丢失甚至损坏。如果无法停服，应使用 SQLite Online Backup API（例如 `sqlite3` 的 `.backup`）或 `VACUUM INTO` 生成一致快照，而不是直接 `cp` 主数据库文件。

恢复服务：

```bash
docker compose start arcreel
```

恢复时：

1. 停止 ArcReel；
2. 备份当前目录，避免覆盖后无法回退；
3. 将配置归档中的 `.env` 和 `claude_data/` 恢复到原位置，并将配套数据归档完整解压到空的 `data_dir`；
4. 启动并检查 `/health`；
5. 打开几个项目验证图片、视频和版本历史。

### 6.2 PostgreSQL 部署备份 {#backup-postgresql}

先停止 ArcReel 应用，保留 PostgreSQL 运行，避免备份数据库和项目文件期间继续产生写入：

```bash
cd "$(git rev-parse --show-toplevel)/deploy/production"
umask 077
mkdir -p backups
chmod 700 backups
docker compose stop arcreel

backup_stamp="$(date +%Y%m%d-%H%M%S)"

docker compose exec -T postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -h 127.0.0.1 -U arcreel -d arcreel' \
  > "backups/arcreel-db-${backup_stamp}.sql"

tar -czf "backups/arcreel-files-${backup_stamp}.tar.gz" \
  .env docker-compose.yml projects claude_data

docker compose start arcreel
```

数据库备份和文件备份使用同一时间标签，必须配套保存和恢复。文件备份中的 `projects/` 是数据根，已包含日志与 Vertex 凭据。文件备份包含当前 `docker-compose.yml`，因此特殊字符密码所需的 `DATABASE_URL` 定制也会随 `.env` 一起恢复。`umask 077` 与备份目录模式 `0700` 会让宿主机重定向生成的 SQL 文件和文件归档仅对当前用户可读写。

`pg_dump` 会使用 libpq 的 `PGPASSWORD`。上述命令只在 PostgreSQL 容器内的该次 `pg_dump` 进程中设置它，因此 `docker compose exec -T` 可以非交互执行，也不会把密码展开到宿主机命令行。长期的宿主机备份自动化应改用权限为 `0600` 的 PostgreSQL password file，不要把密码写进脚本或备份文件名。

如果 `tar` 报 `Permission denied`，说明挂载目录中存在由容器内 root 用户创建、宿主机当前用户不可读的文件。可用 `sudo` 重新执行对应的 `tar` 命令，并在完成后限制备份文件的读取权限。

### 6.3 PostgreSQL 恢复 {#restore-postgresql}

恢复前停止 ArcReel，保留 PostgreSQL：

```bash
cd "$(git rev-parse --show-toplevel)/deploy/production"
docker compose stop arcreel

backup_stamp=YYYYMMDD-HHMMSS
tar -xzf "backups/arcreel-files-${backup_stamp}.tar.gz"
```

文件归档会恢复 `.env`、`docker-compose.yml`、数据根 `projects/` 和 `claude_data/`。以下流程还会删除目标 `arcreel` 数据库中的现有数据；先确认同一 `backup_stamp` 的数据库与文件备份完整，并在隔离环境演练恢复流程。

重建空数据库后再导入，避免与已有表结构或数据冲突：

```bash
docker compose exec -T postgres \
  dropdb -U arcreel --maintenance-db=postgres --if-exists --force arcreel

docker compose exec -T postgres \
  createdb -U arcreel --maintenance-db=postgres -O arcreel arcreel

cat "backups/arcreel-db-${backup_stamp}.sql" | \
  docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U arcreel -d arcreel
```

数据库导入成功后，重新启动：

```bash
docker compose start arcreel
curl -f http://localhost:1241/health
```

> 恢复策略取决于是否覆盖现有数据库、是否跨版本以及备份时服务是否仍有写入。生产环境应定期做真实恢复演练，而不只是确认备份文件存在。

## 7. 反向代理与 HTTPS {#reverse-proxy-and-https}

ArcReel 当前不支持直接暴露到公网。私有远程部署必须启用认证，并通过 TLS、VPN 或安全隧道保护传输。不要把 `1241` 端口直接发布到不受信任的网络。建议：

- 使用 Nginx、Caddy、Traefik 或云负载均衡器；
- 配置 HTTPS；
- 只允许代理服务器访问 ArcReel 容器端口；
- 保留 SSE 长连接；
- 设置足够的上传大小和读取超时。

官方 Compose 文件默认使用 `1241:1241`，会把后端端口发布到宿主机的所有网络接口；仅添加反向代理不会关闭这条直连路径。反向代理运行在同一宿主机时，启动前将 `arcreel` 服务的端口映射改为仅监听 loopback：

```yaml
ports:
  - "127.0.0.1:1241:1241"
```

如果反向代理运行在容器网络或其他主机上，应取消不必要的宿主机端口发布，并通过容器网络、主机防火墙或等效网络策略保证只有代理能够访问 ArcReel 后端。

ArcReel 自身已经处理缓存头和压缩，反向代理不需要另行配置：

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

Docker 镜像虽然已包含 `bwrap` 和 `socat`，宿主机的 user namespace 或 AppArmor 策略仍可能阻止沙箱启动。启动失败时应根据服务输出的 `SANDBOX_*` 诊断修复，不要改成特权模式绕过检查，也不要在不了解影响的情况下删除官方 Compose 的沙箱配置。

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

不要直接删除当前项目引用的文件。优先通过项目归档、清理无用项目和保留必要版本控制空间。

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
