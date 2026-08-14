---
id: deployment
title: 部署与运维
sidebar_position: 1
update_docs: engine-b
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
| `deploy/projects/` | `/app/projects` | 项目数据、生成资产和默认 SQLite 数据库 |
| `deploy/logs/` | `/app/logs` | 应用日志 |
| `deploy/vertex_keys/` | `/app/vertex_keys` | Google Vertex AI 凭据文件 |
| `deploy/claude_data/` | `/root/.claude` | Agent 运行时相关数据 |

默认 SQLite 数据库位于应用数据目录下的 `.arcreel.db`，在 Docker 默认部署中会随 `projects/` 一起持久化。

> 不要只备份数据库而忽略 `projects/`。数据库保存任务、配置和索引信息，项目目录保存原始素材和生成文件，两者需要保持一致。

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
| `deploy/production/projects/` | 项目和媒体资产 |
| `deploy/production/logs/` | 应用日志 |
| `deploy/production/vertex_keys/` | Vertex AI 凭据 |
| `deploy/production/claude_data/` | Agent 运行时数据 |
| `deploy/production/.env` | 认证和数据库配置 |

`pgdata/` 只保存 PostgreSQL 集群数据，`projects/` 保存项目元数据和媒体资产，两者都必须持久化且配套备份。生产部署通过 `DATABASE_URL` 使用 PostgreSQL，不会使用 `deploy/production/projects/.arcreel.db`；不要把 SQLite 文件复制进 `pgdata/`，也不要把两个目录当成可相互替代的数据库备份。

### 2.3 数据库迁移 {#database-migrations}

ArcReel 在应用启动时运行 Alembic 迁移，将数据库结构升级到当前版本。

升级前仍然必须备份。自动迁移解决的是结构升级，不代替可回滚的数据备份。

## 3. 环境变量 {#environment-variables}

默认部署示例当前包含以下核心变量：

| 变量 | 默认值 | 建议 |
|---|---|---|
| `AUTH_USERNAME` | `admin` | 可修改管理员用户名 |
| `AUTH_PASSWORD` | 空 | 正式部署必须显式设置强密码 |
| `AUTH_TOKEN_SECRET` | 空 | 正式部署必须设置长期固定随机值 |
| `LOG_LEVEL` | `INFO` | 排障时临时改为 `DEBUG`，完成后恢复 |
| `POSTGRES_PASSWORD` | 无 | 仅生产部署需要，必须设置 |
| `TZ` | `Asia/Shanghai` | 可在 Compose 环境中覆盖 |
| `DATABASE_URL` | SQLite 默认路径 | 生产 Compose 自动设置 PostgreSQL URL |
| `ARCREEL_DATA_DIR` | `projects` | 需要自定义应用数据根目录时使用 |

注意：

- `AUTH_TOKEN_SECRET` 变化后，现有登录 Token 会失效。
- `.env` 中可能包含密钥，不要提交到版本库。
- Vertex 凭据文件应只授予运行 ArcReel 的用户读取权限。
- 第三方模型 API Key 通常在 ArcReel 设置页中管理，不要写入公开文档。

ArcReel 的沙箱要求父进程环境中不保留供应商密钥。以下凭据环境变量存在非空值时，服务会拒绝启动并提示迁移到 WebUI 设置页：

- `ANTHROPIC_API_KEY`
- `ARK_API_KEY` / `XAI_API_KEY` / `GEMINI_API_KEY` / `VIDU_API_KEY`
- `DASHSCOPE_API_KEY` / `MINIMAX_API_KEY` / `AGNES_API_KEY` / `OPENAI_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS`（Vertex 凭据继续放在 `vertex_keys/` 目录）

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
3. 备份数据库和项目目录；
4. 记录当前镜像版本；
5. 在可接受的维护窗口执行升级。

### 5.2 默认部署升级 {#upgrade-sqlite-deployment}

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

在 `deploy/production/` 中：

```bash
# 先备份数据库和 projects/
docker compose pull
docker compose up -d

docker compose ps
docker compose logs --tail=100 postgres
docker compose logs --tail=200 arcreel
curl -f http://localhost:1241/health
```

应用启动时会执行数据库迁移。不要在没有备份的情况下跳过多个版本直接升级。

### 5.4 固定版本 {#pin-version}

`latest` 适合快速体验，但生产环境更适合固定 Release 标签。

将 Compose 中的镜像改为：

```yaml
image: ghcr.io/arcreel/arcreel:vX.Y.Z
```

升级时显式修改版本，可以降低无意中拉取新版本的风险。

## 6. 备份与恢复 {#backup-and-restore}

### 6.1 SQLite 部署备份 {#backup-sqlite}

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
  .env vertex_keys claude_data

tar -czf "backups/arcreel-projects-${backup_stamp}.tar.gz" \
  -C "${data_dir}" .
```

服务停止后再归档整个 `data_dir`，可以让 `.arcreel.db` 与项目资产保持在同一时点。配置归档与数据归档必须使用相同时间标签并配套保存；`umask 077` 与备份目录模式 `0700` 会限制其中凭据和项目资产的读取权限。不要在 ArcReel 写入时只复制 `.arcreel.db`：WAL 模式下，已提交交易可能仍在 `.arcreel.db-wal` 中，丢失或错配 WAL 文件会造成数据丢失甚至损坏。如果无法停服，应使用 SQLite Online Backup API（例如 `sqlite3` 的 `.backup`）或 `VACUUM INTO` 生成一致快照，而不是直接 `cp` 主数据库文件。

恢复服务：

```bash
docker compose start arcreel
```

恢复时：

1. 停止 ArcReel；
2. 备份当前目录，避免覆盖后无法回退；
3. 将配置归档中的 `.env`、`vertex_keys/` 和 `claude_data/` 恢复到原位置，并将配套数据归档完整解压到空的 `data_dir`；
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
  .env docker-compose.yml projects vertex_keys claude_data

docker compose start arcreel
```

数据库备份和文件备份使用同一时间标签，必须配套保存和恢复。文件备份包含当前 `docker-compose.yml`，因此特殊字符密码所需的 `DATABASE_URL` 定制也会随 `.env` 一起恢复。`umask 077` 与备份目录模式 `0700` 会让宿主机重定向生成的 SQL 文件和文件归档仅对当前用户可读写。

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

文件归档会恢复 `.env`、`docker-compose.yml`、`projects/` 和运行时目录。以下流程还会删除目标 `arcreel` 数据库中的现有数据；先确认同一 `backup_stamp` 的数据库与文件备份完整，并在隔离环境演练恢复流程。

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

Nginx 示例：

```nginx
server {
    listen 443 ssl http2;
    server_name arcreel.example.com;

    ssl_certificate /etc/letsencrypt/live/arcreel.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/arcreel.example.com/privkey.pem;

    client_max_body_size 2g;

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
- 修改 `AUTH_TOKEN_SECRET` 后需要重新登录。

### Agent 请求失败 {#agent-request-fails}

- 验证 AI 助手凭据；
- 检查 Base URL 和模型名称；
- 检查网络和代理；
- 查看供应商是否限流；
- 使用少量内容验证，不要用完整小说做连接测试。

### 任务一直排队 {#tasks-stuck-in-queue}

- 查看图像、视频和音频并发设置；
- 检查是否有长时间停留在运行中或取消中的异常任务；
- 查看供应商 RPM 配额；
- 检查前序任务是否尚未完成。

### 磁盘快速增长 {#disk-growth}

重点检查：

```bash
du -sh projects logs
find projects -type f -size +500M
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
- [ ] 备份数据库和项目目录；
- [ ] 完成一次恢复演练；
- [ ] 配置磁盘和健康检查告警；
- [ ] 确认模型 API Key 不出现在日志和仓库；
- [ ] 阅读许可证和 `NOTICE`。
