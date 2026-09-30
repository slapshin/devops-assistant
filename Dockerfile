# syntax=docker/dockerfile:1
# Local/self-hosted image: FastAPI backend serving the built Vue UI. See docs/OPERATIONS.md.

FROM node:24.21.0-trixie-slim AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY fixtures/ /src/fixtures/
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

FROM python:3.14.7-slim-trixie AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    DATA_DIR=/data \
    STATIC_DIR=/app/static
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/scripts ./scripts
COPY --from=web /src/frontend/dist /app/static
RUN useradd --system --uid 10001 --home-dir /data assistant \
    && mkdir -p /data && chown assistant /data
USER assistant
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["/app/backend/.venv/bin/python", "-c", "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"APP_PORT\",\"8000\")}/api/health', timeout=4)"]
CMD ["/app/backend/.venv/bin/python", "-m", "app"]
