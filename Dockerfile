# ============================================================
# Stage 1: build frontend
# ============================================================
FROM node:24-slim AS frontend-builder

WORKDIR /build/frontend

# Enable corepack; frontend/package.json packageManager pins pnpm.
# Disable download confirmation: non-TTY docker builds otherwise wait at
# "Corepack is about to download ..." until timeout.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable

# Copy dependency files first for caching; corepack selects pnpm from packageManager.
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Copy frontend source and build.
COPY frontend/ ./
RUN pnpm build

# ============================================================
# Stage 2: production image
# ============================================================
FROM python:3.12-slim AS production

# Install system dependencies and upgrade preinstalled packages to current repository versions.
# CI passes ISO week as APT_REFRESH; changes invalidate this layer for periodic package refresh.
ARG APT_REFRESH=unset
RUN echo "apt refresh: ${APT_REFRESH}" \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    curl \
    bubblewrap \
    socat \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Upgrade preinstalled pip: uv installs all dependencies and runtime never invokes pip,
# but old pip in python:3.12-slim triggers CVE scan alerts; upgrading removes them.
RUN python -m pip install --no-cache-dir --upgrade pip

# Install uv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Disable Python output buffering for immediate Docker logs.
ENV PYTHONUNBUFFERED=1

# Default timezone, overridable by docker-compose / runtime -e TZ=...
ENV TZ=Asia/Shanghai

# Copy dependency/package metadata first for caching; workspace dependencies require their source in place.
COPY pyproject.toml uv.lock README.md ./
COPY packages/ packages/
RUN uv sync --no-dev --no-install-project

# Copy application code.
COPY lib/ lib/
COPY server/ server/
COPY alembic/ alembic/
COPY alembic.ini ./
COPY scripts/ scripts/
COPY agent_runtime_profile/ agent_runtime_profile/
COPY public/ public/
# Distribute bundled-ffmpeg license notices in the image (see NOTICE).
COPY LICENSE NOTICE ./
COPY LICENSES/ LICENSES/

# Copy frontend build artifacts.
COPY --from=frontend-builder /build/frontend/dist/ frontend/dist/

# CI supplies build commit for /health runtime-version reporting; keep last to preserve earlier layer caches.
ARG BUILD_COMMIT=
ENV ARCREEL_BUILD_COMMIT=${BUILD_COMMIT}

# Expose port.
EXPOSE 1241

# Health check.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:1241/health || exit 1

# Startup command.
CMD ["uv", "run", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "1241"]
