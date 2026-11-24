FROM node:24-slim AS frontend-builder

WORKDIR /build/frontend

# 关闭交互式下载确认，否则 docker build 这种非 TTY 环境会卡在
# "Corepack is about to download ..." 直到超时
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build

FROM python:3.12-slim AS production

# APT_REFRESH 由 CI 按 ISO 周传入，值变化时本层缓存失效，保证系统包定期刷新
# git：Agent 派发子智能体时可以要求 worktree 隔离，Claude Code 先用 git 校验 WorktreeCreate 钩子
# 返回的项目目录，缺少 git 就拒绝隔离，这次派发随之失败（钩子见 agent_runtime_profile/.claude/settings.json）
ARG APT_REFRESH=unset
RUN echo "apt refresh: ${APT_REFRESH}" \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    curl \
    git \
    bubblewrap \
    socat \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# 升级基础镜像预装的 pip：依赖全部由 uv 安装、运行时不调用 pip，
# 但 python:3.12-slim 自带的旧 pip 会被镜像扫描器报 CVE，升级以清除这些告警
RUN python -m pip install --no-cache-dir --upgrade pip

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV PYTHONUNBUFFERED=1

ENV TZ=Asia/Shanghai

# 先复制依赖和包元数据文件，利用缓存；workspace 子包作为依赖随这一步安装，源码须先就位
COPY pyproject.toml uv.lock README.md ./
COPY packages/ packages/
RUN uv sync --no-dev --no-install-project

COPY lib/ lib/
COPY server/ server/
COPY alembic/ alembic/
COPY alembic.ini ./
COPY scripts/ scripts/
COPY agent_runtime_profile/ agent_runtime_profile/
COPY public/ public/
# 随包 ffmpeg 的许可证声明（见 NOTICE）随镜像分发
COPY LICENSE NOTICE ./
COPY LICENSES/ LICENSES/

COPY --from=frontend-builder /build/frontend/dist/ frontend/dist/

ARG BUILD_COMMIT=
ENV ARCREEL_BUILD_COMMIT=${BUILD_COMMIT}

EXPOSE 1241

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:1241/health || exit 1

CMD ["uv", "run", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "1241", "--timeout-graceful-shutdown", "10"]
