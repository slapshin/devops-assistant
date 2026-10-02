# syntax=docker/dockerfile:1
# Local/self-hosted image: FastAPI backend serving the built Vue UI. See docs/OPERATIONS.md.
# Build from the repository root: docker build -f devops/docker/Dockerfile .

FROM node:24.21.0-trixie-slim AS web

WORKDIR /src/frontend

COPY frontend/package.json frontend/package-lock.json ./

RUN npm ci --no-audit --no-fund

COPY frontend/ ./
COPY fixtures/ /src/fixtures/

RUN npm run build

FROM ghcr.io/astral-sh/uv:0.12.21 AS uv

FROM python:3.14.7-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
  PYTHONUNBUFFERED=1 \
  UV_COMPILE_BYTECODE=1 \
  UV_LINK_MODE=copy \
  UV_PYTHON_DOWNLOADS=never \
  APP_HOST=0.0.0.0 \
  APP_PORT=8000 \
  DATA_DIR=/data \
  UI_STATIC_DIR=/app/static

RUN apt-get update \
  && apt-get install -y --no-install-recommends curl \
  && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./

RUN uv sync --locked --no-dev --no-install-project

COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/scripts ./scripts
COPY --from=web /src/frontend/dist /app/static

RUN groupadd --system --gid 10001 app \
  && useradd --system --uid 10001 --no-log-init -g app --home-dir /data app \
  && mkdir -p /data \
  && chown app:app /data

USER app

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS --max-time 4 -o /dev/null "http://127.0.0.1:${APP_PORT}/api/health" || exit 1

# --no-sync: the venv is baked at build time; never re-resolve or write to it at runtime.
CMD ["uv", "run", "--no-sync", "python", "-m", "app"]
